"""Declarative rule engine: rules are data (config/rules.yaml), conditions are code.

Each rule lists conditions from a small, fixed vocabulary. A condition returns a
plain-English clause when it holds and None when it does not; a rule fires when all
of its conditions hold, and the clauses become the alert's explanation. That keeps
the explanation honest: it is literally the list of checks that passed.

Two noise controls keep alerts actionable:

* **Cooldown**: the same rule for the same entity (or zone) is suppressed for
  `cooldown_minutes` unless the severity escalates.
* **Correlation**: when several rules fire for one entity in the same frame (a person
  at night who is also loitering), the most severe alert is raised and the others are
  listed in `related_rules`.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import yaml

from ..config import PROJECT_ROOT, PropertyConfig, haversine_m
from ..models import Alert, Detection, Frame, Severity, Telemetry
from ..tracking import TrackUpdate

DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "rules.yaml"
_OPS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq,
}
_ACTION_TEXT = {
    "climbing": "climbing", "tampering": "tampering with an access point", "loitering": "loitering",
    "standing": "standing", "entering": "entering", "exiting": "leaving", "running": "running",
    "walking": "walking", "parked": "parked",
}
_FIELD_TEXT = {
    "battery_pct": "battery {value:.0f}%", "altitude_m": "altitude {value:.0f} m", "wind_mps": "wind {value:.1f} m/s",
    "gps_hdop": "GPS HDOP {value:.1f}", "distance_from_home_m": "distance from dock {value:.0f} m",
    "jump_speed_mps": "GPS fix moved at {value:.0f} m/s since the previous frame", "speed_mps": "speed {value:.1f} m/s",
}
_MAX_JUMP_GAP = timedelta(minutes=10)  # older fixes say nothing about a sudden jump


class RuleConfigError(ValueError):
    """rules.yaml is invalid (unknown condition, bad severity, missing field)."""


@dataclass
class RuleContext:
    frame: Frame
    observations: list[tuple[Detection, TrackUpdate]]
    config: PropertyConfig
    prev_frame: Optional[Frame] = None  # previous frame with telemetry, for GPS jump checks

    @property
    def at(self) -> datetime:
        return self.frame.timestamp

    @property
    def hhmm(self) -> str:
        return self.frame.timestamp.strftime("%H:%M")

    @property
    def is_night(self) -> bool:
        return self.config.is_night(self.frame.timestamp)

    def zone_name(self, zone_id: Optional[str]) -> str:
        return self.config.zone_name(zone_id)


@dataclass
class Rule:
    id: str
    title: str
    scope: str  # detection | telemetry
    severity: Severity
    severity_at_night: Optional[Severity]
    when: dict
    message: str
    action: str
    cooldown: timedelta
    dedupe_by: str

    @classmethod
    def from_dict(cls, raw: dict, defaults: dict) -> "Rule":
        try:
            scope = raw["scope"]
            if scope not in ("detection", "telemetry"):
                raise RuleConfigError(f"rule {raw['id']}: scope must be 'detection' or 'telemetry'")
            _validate_conditions(raw["id"], raw["when"], scope)
            night = raw.get("severity_at_night")
            return cls(
                id=raw["id"],
                title=raw.get("title", raw["id"]),
                scope=scope,
                severity=Severity.parse(raw["severity"]),
                severity_at_night=Severity.parse(night) if night else None,
                when=raw["when"],
                message=raw["message"],
                action=raw.get("action", ""),
                cooldown=timedelta(minutes=raw.get("cooldown_minutes", defaults.get("cooldown_minutes", 15))),
                dedupe_by=raw.get("dedupe_by", "entity" if scope == "detection" else "rule"),
            )
        except KeyError as exc:
            raise RuleConfigError(f"rule {raw.get('id', '?')}: missing field {exc}") from None


def load_rules(path: str | Path | None = None) -> list[Rule]:
    raw = yaml.safe_load(Path(path or DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    rules = [Rule.from_dict(r, defaults) for r in raw["rules"]]
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise RuleConfigError("duplicate rule ids in rules file")
    return rules


class RuleEngine:
    def __init__(self, config: PropertyConfig, rules: Optional[list[Rule]] = None, path: str | Path | None = None):
        self.config = config
        self.rules = rules if rules is not None else load_rules(path)
        self._by_id = {r.id: r for r in self.rules}
        self._last_fired: dict[str, tuple[datetime, Severity]] = {}
        self.raised_before_noise_control = 0
        self.suppressed = 0

    def evaluate(self, ctx: RuleContext) -> list[Alert]:
        fired: list[Alert] = []
        telemetry = _telemetry_values(ctx)
        for rule in self.rules:  # file order = specificity, used to break severity ties
            if rule.scope == "detection":
                for det, upd in ctx.observations:
                    clauses = _check(rule.when, _DETECTION_CONDITIONS, (det, upd, ctx))
                    if clauses is not None:
                        fired.append(self._detection_alert(rule, ctx, det, upd, clauses))
            elif telemetry is not None:
                clauses = _check(rule.when, _TELEMETRY_CONDITIONS, (telemetry, ctx))
                if clauses is not None:
                    fired.append(self._telemetry_alert(rule, ctx, telemetry, clauses))

        self.raised_before_noise_control += len(fired)
        fresh = [a for a in fired if not self._in_cooldown(a)]
        self.suppressed += len(fired) - len(fresh)
        # alerts absorbed by correlation still count as raised, so they don't resurface next frame
        for alert in fresh:
            self._last_fired[alert.dedupe_key] = (alert.timestamp, alert.severity)
        return sorted(self._correlate(fresh), key=lambda a: a.severity, reverse=True)

    # ------------------------------------------------------------------ alert building
    def _detection_alert(self, rule: Rule, ctx: RuleContext, det: Detection, upd: TrackUpdate,
                         clauses: list[str]) -> Alert:
        entity = upd.entity
        visits_today = entity.visits_on(ctx.at.date())
        values = {
            "subject": det.display_name,
            "zone": ctx.zone_name(det.zone),
            "time": ctx.hhmm,
            "action_text": _ACTION_TEXT.get(det.action or "", "detected"),
            "label": det.label.capitalize(),
            "dwell_min": int(upd.dwell_seconds // 60),
            "visit_count_text": _times(len(visits_today)),
            "visit_times": ", ".join(v.start.strftime("%H:%M") for v in visits_today),
        }
        evidence = list(entity.visits[-1].frame_ids[-5:])
        if _uses(rule.when, "min_dwell_minutes"):
            evidence += entity.zone_frame_ids
        if _uses(rule.when, "min_visits_today"):
            evidence += [v.frame_ids[0] for v in visits_today if v.frame_ids]
        keys = {"entity": entity.entity_id, "zone": f"{det.zone}|{det.label}", "entity_zone": f"{entity.entity_id}|{det.zone}"}
        return self._alert(rule, ctx, values, clauses, evidence, zone=det.zone, entity_id=entity.entity_id,
                           dedupe_key=f"{rule.id}|{keys.get(rule.dedupe_by, rule.id)}")

    def _telemetry_alert(self, rule: Rule, ctx: RuleContext, telemetry: dict, clauses: list[str]) -> Alert:
        values = {**telemetry, "zone": ctx.zone_name(ctx.frame.zone), "time": ctx.hhmm}
        evidence = [ctx.frame.frame_id]
        if _uses(rule.when, "jump_speed_mps") and ctx.prev_frame is not None:
            evidence.insert(0, ctx.prev_frame.frame_id)
        return self._alert(rule, ctx, values, clauses, evidence, zone=ctx.frame.zone, entity_id=None, dedupe_key=rule.id)

    def _alert(self, rule: Rule, ctx: RuleContext, values: dict, clauses: list[str], evidence: list[int], *,
               zone: Optional[str], entity_id: Optional[str], dedupe_key: str) -> Alert:
        severity = rule.severity_at_night if (rule.severity_at_night and ctx.is_night) else rule.severity
        if severity != rule.severity:
            clauses = clauses + [f"severity raised to {severity.name} at night"]
        alert = Alert(
            timestamp=ctx.at,
            frame_id=ctx.frame.frame_id,
            rule_id=rule.id,
            severity=severity,
            message=rule.message.format_map(_Defaults(values)),
            explanation=f"{rule.title}: " + "; ".join(clauses) + ".",
            evidence_frame_ids=sorted(set(evidence) | {ctx.frame.frame_id}),
            zone=zone,
            entity_id=entity_id,
            recommended_action=rule.action,
        )
        alert.dedupe_key = dedupe_key
        return alert

    # ------------------------------------------------------------------ noise control
    def _in_cooldown(self, alert: Alert) -> bool:
        last = self._last_fired.get(alert.dedupe_key)
        cooldown = self._by_id[alert.rule_id].cooldown
        return bool(last and timedelta(0) <= alert.timestamp - last[0] < cooldown and alert.severity <= last[1])

    @staticmethod
    def _correlate(alerts: list[Alert]) -> list[Alert]:
        best: dict[str, Alert] = {}
        order: list[str] = []
        for alert in alerts:
            group = alert.entity_id or alert.dedupe_key
            current = best.get(group)
            if current is None:
                best[group] = alert
                order.append(group)
            elif alert.severity > current.severity:
                alert.related_rules = current.related_rules + [current.rule_id]
                best[group] = alert
            else:
                current.related_rules.append(alert.rule_id)
        return [best[g] for g in order]


# ----------------------------------------------------------------------------------- conditions
def _check(when: dict, conditions: dict, args: tuple) -> Optional[list[str]]:
    """All conditions must hold; `any_of` needs one option to hold. Returns the clauses or None."""
    clauses: list[str] = []
    for key, expected in when.items():
        if key == "any_of":
            for option in expected:
                sub = _check(option, conditions, args)
                if sub is not None:
                    clauses += sub
                    break
            else:
                return None
            continue
        clause = conditions[key](expected, *args)
        if clause is None:
            return None
        if clause:  # "" = holds, nothing worth saying
            clauses.append(clause)
    return clauses


def _category(expected, det, upd, ctx):
    if det.category != expected:
        return None
    return f"{det.display_name} detected at the {ctx.zone_name(det.zone)} (frame {ctx.frame.frame_id})"


def _label_in(expected, det, upd, ctx):
    return f"'{det.label}' is a watched object type" if det.label in expected else None


def _action_in(expected, det, upd, ctx):
    return f"the caption describes {det.action}" if det.action in expected else None


def _attributes_any(expected, det, upd, ctx):
    found = [a for a in expected if a in det.attributes]
    return f"marked as {', '.join(found)}" if found else None


def _exclude_attributes(expected, det, upd, ctx):
    if any(a in det.attributes for a in expected):
        return None
    return f"not marked as {' or '.join(expected)}"


def _zone_is(expected, det, upd, ctx):
    zone = ctx.config.zone(det.zone)
    if not zone or not getattr(zone, expected, False):
        return None
    return f"the {zone.name} is a {expected} zone"


def _time(expected, det, upd, ctx):
    cfg = ctx.config
    if expected == "night" and ctx.is_night:
        return f"{ctx.hhmm} is within night hours ({cfg.night.start:%H:%M}-{cfg.night.end:%H:%M})"
    if expected == "outside_business_hours" and not cfg.is_business_hours(ctx.at):
        return f"{ctx.hhmm} is outside business hours ({cfg.business.start:%H:%M}-{cfg.business.end:%H:%M})"
    return None


def _min_dwell_minutes(expected, det, upd, ctx):
    minutes = upd.dwell_seconds / 60
    if minutes < expected:
        return None
    frames = upd.entity.zone_frame_ids
    return (f"present at the {ctx.zone_name(det.zone)} for {minutes:.0f} min since "
            f"{upd.entity.zone_since:%H:%M} across {len(frames)} frames (threshold {expected} min)")


def _new_visit(expected, det, upd, ctx):
    return "this sighting starts a new visit" if upd.is_new_visit == expected else None


def _min_visits_today(expected, det, upd, ctx):
    visits = upd.entity.visits_on(ctx.at.date())
    if len(visits) < expected:
        return None
    times = ", ".join(v.start.strftime("%H:%M") for v in visits)
    return (f"re-identified as {upd.entity.entity_id} (match confidence {upd.confidence:.2f}) with "
            f"{len(visits)} visits today at {times} (threshold {expected})")


def _not_known_vehicle(expected, det, upd, ctx):
    owner = ctx.config.is_known_vehicle(det.color, det.make, det.model, det.label)
    return None if owner else "not on the known-vehicle list"


def _no_person_in_zone(expected, det, upd, ctx):
    people = [d for d, _ in ctx.observations if d.category == "person" and d.zone == det.zone]
    return None if people else "no person is near it in this frame"


def _telemetry_threshold(expected, values, ctx):
    clauses = []
    for check in expected:
        field, op = check["field"], check["op"]
        threshold = _resolve(check["value"], ctx.config)
        value = values.get(field)
        if value is None or not _OPS[op](value, threshold):
            return None
        clauses.append(f"{_FIELD_TEXT.get(field, field + ' {value}').format(value=value)} ({op} {threshold:g})")
    return "; ".join(clauses)


def _mode_not(expected, values, ctx):
    return None if values["mode"] in expected else ""


def _mode_is(expected, values, ctx):
    return "" if values["mode"] in expected else None


_DETECTION_CONDITIONS = {
    "category": _category, "label_in": _label_in, "action_in": _action_in, "attributes_any": _attributes_any,
    "exclude_attributes": _exclude_attributes, "zone_is": _zone_is, "time": _time,
    "min_dwell_minutes": _min_dwell_minutes, "new_visit": _new_visit, "min_visits_today": _min_visits_today,
    "not_known_vehicle": _not_known_vehicle, "no_person_in_zone": _no_person_in_zone,
}
_TELEMETRY_CONDITIONS = {"telemetry": _telemetry_threshold, "mode_not": _mode_not, "mode_is": _mode_is}


def _validate_conditions(rule_id: str, when: dict, scope: str) -> None:
    known = _DETECTION_CONDITIONS if scope == "detection" else _TELEMETRY_CONDITIONS
    for key, value in when.items():
        if key == "any_of":
            for option in value:
                _validate_conditions(rule_id, option, scope)
        elif key not in known:
            raise RuleConfigError(f"rule {rule_id}: unknown {scope} condition '{key}'")
        elif key == "telemetry":
            for check in value:
                if check.get("op") not in _OPS or "field" not in check or "value" not in check:
                    raise RuleConfigError(f"rule {rule_id}: telemetry checks need field, op ({', '.join(_OPS)}) and value")


# ----------------------------------------------------------------------------------- helpers
def _telemetry_values(ctx: RuleContext) -> Optional[dict]:
    """Telemetry fields plus the derived GPS jump speed; None when the frame has no telemetry."""
    t: Optional[Telemetry] = ctx.frame.telemetry
    if t is None:
        return None
    values = {
        "battery_pct": t.battery_pct, "altitude_m": t.altitude_m, "wind_mps": t.wind_mps, "gps_hdop": t.gps_hdop,
        "distance_from_home_m": t.distance_from_home_m, "speed_mps": t.speed_mps, "mode": t.mode, "jump_speed_mps": 0.0,
    }
    prev = ctx.prev_frame.telemetry if ctx.prev_frame else None
    if prev is not None:
        dt = (t.timestamp - prev.timestamp).total_seconds()
        if 0 < dt <= _MAX_JUMP_GAP.total_seconds():
            values["jump_speed_mps"] = haversine_m(prev.lat, prev.lon, t.lat, t.lon) / dt
    return values


def _resolve(value, config: PropertyConfig) -> float:
    """Numbers stay numbers; "$name" reads an attribute of the property config."""
    if isinstance(value, str) and value.startswith("$"):
        return float(getattr(config, value[1:]))
    return float(value)


def _uses(when: dict, key: str) -> bool:
    """Does this rule (including any_of options and telemetry fields) refer to `key`?"""
    for name, value in when.items():
        if name == key:
            return True
        if name == "any_of" and any(_uses(option, key) for option in value):
            return True
        if name == "telemetry" and any(check.get("field") == key for check in value):
            return True
    return False


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


class _Defaults(dict):
    """format_map helper: a missing template field renders as '?' instead of raising."""

    def __missing__(self, key):
        return "?"


__all__ = ["Rule", "RuleContext", "RuleEngine", "RuleConfigError", "load_rules", "DEFAULT_RULES_PATH"]
