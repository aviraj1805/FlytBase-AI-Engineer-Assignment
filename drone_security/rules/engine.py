"""Rule engine: evaluates predefined rules per frame, then de-duplicates and correlates.

Two noise controls keep alerts actionable:

* **Cooldown** - the same rule firing for the same entity (or zone) again within
  `alert_cooldown_minutes` is suppressed unless the severity escalates.
* **Correlation** - when several rules fire for the same entity in the same frame
  (a person at the gate at night who is also loitering), only the most severe
  alert is raised; the others are listed in `related_rules`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from ..config import PropertyConfig
from ..models import Alert, Detection, Frame, Severity
from ..tracking import EntityTracker, TrackUpdate


@dataclass
class RuleContext:
    frame: Frame
    observations: list[tuple[Detection, TrackUpdate]]
    config: PropertyConfig
    tracker: EntityTracker

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


class Rule(ABC):
    rule_id: str = ""
    title: str = ""

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> Iterable[Alert]:
        ...

    def alert(
        self,
        ctx: RuleContext,
        severity: Severity,
        message: str,
        *,
        zone: Optional[str] = None,
        entity_id: Optional[str] = None,
        action: str = "",
        dedupe_key: Optional[str] = None,
    ) -> Alert:
        alert = Alert(
            timestamp=ctx.at,
            frame_id=ctx.frame.frame_id,
            rule_id=self.rule_id,
            severity=severity,
            message=message,
            zone=zone,
            entity_id=entity_id,
            recommended_action=action,
        )
        alert.dedupe_key = dedupe_key or f"{self.rule_id}|{entity_id or zone}"
        return alert


class RuleEngine:
    def __init__(self, config: PropertyConfig, rules: Optional[list[Rule]] = None):
        from .builtin import default_rules

        self.config = config
        self.rules = rules if rules is not None else default_rules()
        self.cooldown = timedelta(minutes=config.thresholds.alert_cooldown_minutes)
        self._last_fired: dict[str, tuple[datetime, Severity]] = {}
        self.suppressed = 0

    def evaluate(self, ctx: RuleContext) -> list[Alert]:
        fired: list[Alert] = []
        for rule in self.rules:  # rule order = specificity, used to break severity ties
            fired.extend(rule.evaluate(ctx))

        fresh = [a for a in fired if not self._in_cooldown(a)]
        self.suppressed += len(fired) - len(fresh)
        # alerts absorbed by correlation still count as raised, so they don't resurface next frame
        for alert in fresh:
            self._last_fired[alert.dedupe_key] = (alert.timestamp, alert.severity)
        return sorted(self._correlate(fresh), key=lambda a: a.severity, reverse=True)

    def _in_cooldown(self, alert: Alert) -> bool:
        last = self._last_fired.get(alert.dedupe_key)
        return bool(last and alert.timestamp - last[0] < self.cooldown and alert.severity <= last[1])

    def _correlate(self, alerts: list[Alert]) -> list[Alert]:
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
