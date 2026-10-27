"""Offline analyst: deterministic question routing over the same five tools the LLM agent uses.

This keeps follow-up Q&A working with no API key (and makes it unit-testable). It handles
the question shapes operators actually ask (summaries, briefings, inventories, "how many
times did X come", alerts in a window, drone status) plus follow-ups that refer back to the
previous subject ("when did it leave?"). Like the LLM agent it cites frames as [fN] and
answers "not enough evidence" instead of guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..analysis import DRONE_RULES, SECURITY_RULES
from ..config import PropertyConfig
from ..perception import RuleBasedAnalyzer
from ..perception.rule_analyzer import zone_in
from .grounding import NOT_ENOUGH_EVIDENCE
from .tools import AnalystToolkit

_parser = RuleBasedAnalyzer()
_TIME = r"(\d{1,2}:\d{2})"
_PRONOUNS = re.compile(r"\b(it|its|they|them|their|that one|this one|he|she|him|her|that vehicle|that person)\b")
_PERIODS = {"morning": ("06:00", "11:59"), "afternoon": ("12:00", "16:59"), "evening": ("17:00", "21:59")}
# things the perception schema never records, so no amount of retrieval can answer them
_UNRECORDED = {r"licen[cs]e plate|number plate|\bplate\b": "licence plates", r"\bface|facial": "faces",
               r"\bname of|\bwho (exactly )?(is|was) (he|she|the person)\b|identity of": "names or identities",
               r"phone number": "phone numbers"}
_RULE_WORDS = [
    (r"loiter", "LOITERING"), (r"intru|break[- ]?in|climb|tamper|forc", "INTRUSION"), (r"restricted", "RESTRICTED_ZONE"),
    (r"unattended|left behind|abandoned", "UNATTENDED_OBJECT"), (r"left open|open gate|gate open|door open|\bopen\b", "ACCESS_OPEN"),
    (r"repeat|again|twice", "REPEAT_VEHICLE"), (r"gps|drift", "GPS_DRIFT"), (r"geofence", "GEOFENCE"),
]


@dataclass
class AgentAnswer:
    text: str
    mode: str  # "gemini" | "offline"
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)


@dataclass
class _Window:
    start: Optional[str] = None
    end: Optional[str] = None
    point: Optional[str] = None
    night: bool = False

    @property
    def given(self) -> bool:
        return bool(self.start or self.end or self.night)


class OfflineAnalyst:
    mode = "offline"

    def __init__(self, toolkit: AnalystToolkit, config: PropertyConfig):
        self.tools = toolkit
        self.config = config
        self.focus: Optional[str] = None  # subject of the previous question, for follow-ups
        self._calls: list[tuple[str, dict]] = []

    # ------------------------------------------------------------------ entry point
    def ask(self, question: str) -> AgentAnswer:
        self._calls = []
        q = " ".join(question.lower().split())
        window = _parse_window(q)
        subject = self._subject(q)
        zone = zone_in(q)

        unrecorded = next((what for pattern, what in _UNRECORDED.items() if re.search(pattern, q)), None)
        if unrecorded:
            text = (f"{NOT_ENOUGH_EVIDENCE} The frame descriptions never record {unrecorded}, so I cannot tell from "
                    f"this footage.")
        elif re.search(r"\bbrief(ing)?\b|report for the owner", q):
            text = self._briefing()
        elif re.search(r"what (objects|things)|which objects|inventory|what did (you|the drone) see|objects (were )?in|what animals", q):
            text = self._inventory(window, animals_only="animal" in q)
        elif re.search(r"battery|altitude|wind|telemetry|drone status|where was the drone|geofence|gps|flight|drone (health|problem|issue)", q):
            text = self._drone(window)
        elif re.search(r"\bsame\b.*\bas\b|\bdifferent\b", q) and len(_identities(q)) >= 2:
            text = self._same_entity(q)
        elif (subject and self._trackable(subject)
              and re.search(r"how many times|how often|visit|when did|came back|come back|return|enter|arriv|leave|\bleft\b(?! open)|history", q)):
            text = self._entity(subject, q)
        elif re.search(r"alert|threat|suspicious|incident|intru|break|danger|unusual|loiter|unattended|left open|restricted|"
                       r"tamper|after hours|after dark", q):
            text = self._alerts(q, window, zone)
        elif re.search(r"summar|overview|what happened|recap|tl;?dr|activity|timeline", q):
            text = self._window_summary(window) if window.given else self._day_summary()
        elif subject or zone:
            text = self._frames(subject, zone, window)
        else:
            text = self._search(q, window)
        return AgentAnswer(text, self.mode, list(self._calls))

    # ------------------------------------------------------------------ intents
    def _day_summary(self) -> str:
        out = self._call("summarize_window")
        if not out["summaries"]:
            return NOT_ENOUGH_EVIDENCE
        lines = [out["one_sentence"], "", *out["summaries"]]
        return "\n".join(line for line in lines if line is not None)

    def _window_summary(self, w: _Window) -> str:
        out = self._call("summarize_window", start=w.start, end=w.end)
        lines = [s for s in out["summaries"] if "quiet" not in s.split(":", 1)[0]]
        if w.night:
            lines = [s for s in lines if self._is_night(s[:5])]
        if not lines:
            return "Nothing notable in that window (the patrol frames show no events)."
        return "\n".join(lines)

    def _briefing(self) -> str:
        alerts = self._call("list_alerts")["alerts"]
        day = self._call("summarize_window")
        night = [a for a in alerts if a["rule"] in SECURITY_RULES and self._is_night(a["time"])]
        daytime = [a for a in alerts if a["rule"] in SECURITY_RULES and not self._is_night(a["time"])]
        drone = [a for a in alerts if a["rule"] in DRONE_RULES]
        lines = [day["one_sentence"] or "", ""]
        for title, items in (("Overnight", night), ("Daytime", daytime), ("Drone health", drone)):
            lines.append(f"{title}: " + ("; ".join(f"{a['time']} {a['message'].rstrip('.')} [f{a['frame_id']}]" for a in items)
                                         or "nothing to report") + ".")
        actions = [a["recommended_action"] for a in alerts if a["severity"] in ("CRITICAL", "HIGH")]
        if actions:
            lines.append("Actions: " + " ".join(dict.fromkeys(actions)))
        return "\n".join(lines)

    def _inventory(self, w: _Window, animals_only: bool = False) -> str:
        objects = self._call("summarize_window", start=w.start, end=w.end)["objects"]
        if animals_only:
            objects = [o for o in objects if o["category"] == "animal"]
        if not objects:
            return NOT_ENOUGH_EVIDENCE
        groups: dict[str, list[str]] = {}
        for o in objects:
            groups.setdefault(o["category"], []).append(
                f"{o['label']} ({o['sightings']} sightings, {o['distinct']} distinct, first [f{o['first_frame_id']}])")
        return "Objects seen in the footage:\n" + "\n".join(f"  {cat}: " + "; ".join(items) for cat, items in groups.items())

    def _drone(self, w: _Window) -> str:
        if w.point:
            s = self._call("drone_status", time=w.point)
            if not s["found"]:
                return NOT_ENOUGH_EVIDENCE
            t = s["telemetry"]
            return (f"At {t['ts'][11:16]} the drone was in {t['mode']} mode over the {self.config.zone_name(t['zone'])}: "
                    f"altitude {t['altitude_m']:.0f} m, battery {t['battery_pct']:.0f}%, wind {t['wind_mps']:.1f} m/s, "
                    f"GPS HDOP {t['gps_hdop']:.1f}, {t['distance_from_home_m']:.0f} m from the dock [f{t['frame_id']}].")
        s = self._call("drone_status")
        if not s["found"] or not s.get("extremes"):
            return NOT_ENOUGH_EVIDENCE
        ex = s["extremes"]
        lines = [f"Lowest battery {ex['lowest_battery']['battery_pct']:.0f}% at {ex['lowest_battery']['ts'][11:16]} "
                 f"[f{ex['lowest_battery']['frame_id']}]; furthest from dock {ex['furthest']['distance_from_home_m']:.0f} m at "
                 f"{ex['furthest']['ts'][11:16]} [f{ex['furthest']['frame_id']}]; worst GPS HDOP {ex['worst_gps']['gps_hdop']:.1f} "
                 f"at {ex['worst_gps']['ts'][11:16]} [f{ex['worst_gps']['frame_id']}]."]
        if s["alerts"]:
            lines.append("Flight-safety alerts:")
            lines += [f"  - {a['time']} [{a['severity']}] {a['rule']}: {a['message']} [f{a['frame_id']}]" for a in s["alerts"]]
        else:
            lines.append("No flight-safety alerts.")
        return "\n".join(lines)

    def _entity(self, subject: str, q: str) -> str:
        report = self._call("entity_history", description=subject)
        if not report["found"]:
            return f"{NOT_ENOUGH_EVIDENCE} No tracked identity matches '{subject}'."
        e = report["entities"][0]
        self.focus = e["name"].lower()
        visits = e["visits"]
        if re.search(r"leave|left|exit|depart", q):
            exits = [f"{v['end']} [f{v['frame_ids'][-1]}]" for v in visits if v["left"]]
            text = f"{e['name']} was seen leaving at {', '.join(exits)}." if exits else f"No exit was observed for {e['name']}."
            if not visits[-1]["left"]:
                text += (f" After its {visits[-1]['start']} arrival it was last seen at {visits[-1]['end']} "
                         f"[f{visits[-1]['frame_ids'][-1]}] with no exit observed.")
            return text
        times = "; ".join(f"{v['start']}-{v['end']} ({', '.join(z.replace('_', ' ') for z in v['zones'])}) "
                          f"[f{v['frame_ids'][0]}]" for v in visits)
        text = (f"{e['name']} ({e['entity_id']}, re-ID confidence {e['reid_confidence']:.2f}) made {e['visit_count']} "
                f"visit{'s' if e['visit_count'] != 1 else ''} with {e['sightings']} sightings: {times}.")
        if e.get("known_vehicle"):
            text += f" It is a known vehicle ({e['known_vehicle']}), so it raises no alerts."
        if e["alerts"]:
            text += "\nRelated alerts:\n" + "\n".join(f"  - {a['time']} [{a['severity']}] {a['message']} [f{a['frame_id']}]"
                                                     for a in e["alerts"])
        return text

    def _same_entity(self, q: str) -> str:
        first, second = _identities(q)[:2]
        a, b = self._call("entity_history", description=first), self._call("entity_history", description=second)
        if not a["found"] or not b["found"]:
            return NOT_ENOUGH_EVIDENCE
        ea, eb = a["entities"][0], b["entities"][0]
        if ea["entity_id"] == eb["entity_id"]:
            return f"Yes: both descriptions resolve to the same tracked identity {ea['entity_id']} ({ea['name']})."
        return (f"No: the tracker keeps them apart. {ea['name']} is {ea['entity_id']} (first seen {ea['visits'][0]['start']} "
                f"[f{ea['visits'][0]['frame_ids'][0]}]) and {eb['name']} is {eb['entity_id']} (first seen "
                f"{eb['visits'][0]['start']} [f{eb['visits'][0]['frame_ids'][0]}]).")

    def _alerts(self, q: str, w: _Window, zone: Optional[str]) -> str:
        rule = next((r for pattern, r in _RULE_WORDS if re.search(pattern, q)), None)
        if rule is None and not zone and re.search(r"after hours|after dark|overnight", q):  # a named zone: all its alerts
            rule = "AFTER_HOURS_VEHICLE" if re.search(r"vehicle|car|truck|van|suv", q) else "AFTER_HOURS_PERSON"
        severity = "CRITICAL" if "critical" in q else "HIGH" if re.search(r"serious|severe|\bhigh\b", q) else None
        if re.search(r"\balerts?\b|incident|suspicious|threat|unusual", q) and not re.search(r"loiter|unattended|restricted", q):
            rule = None  # a general question about alerts, not one rule
        out = self._call("list_alerts", min_severity=severity, rule_id=rule, zone=zone, start=w.start, end=w.end)
        alerts = [a for a in out["alerts"] if not w.night or self._is_night(a["time"])]
        if not alerts:
            what = rule.replace("_", " ").lower() if rule else "matching"
            return f"{NOT_ENOUGH_EVIDENCE} No {what} alerts were raised{' in that window' if w.given else ''}."
        header = f"{len(alerts)} alert{'s' if len(alerts) != 1 else ''}" + (" at night" if w.night else "") + ":"
        return header + "\n" + "\n".join(f"  - {a['time']} [{a['severity']}] {a['message']} [f{a['frame_id']}]" for a in alerts)

    def _frames(self, subject: Optional[str], zone: Optional[str], w: _Window) -> str:
        out = self._call("query_frames", object=subject, zone=zone, start=w.start, end=w.end, limit=25)
        frames = [f for f in out["frames"] if not w.night or self._is_night(f["time"])]
        if not frames and subject:
            return self._search(subject + (f" {zone.replace('_', ' ')}" if zone else ""), w)
        if not frames:
            return NOT_ENOUGH_EVIDENCE
        self.focus = subject or self.focus
        what = subject or "activity"
        where = f" at the {self.config.zone_name(zone)}" if zone else ""
        distinct = {f["entity_id"] for f in frames if f.get("entity_id")}
        count = f" of {len(distinct)} distinct tracked {'identity' if len(distinct) == 1 else 'identities'}" if distinct else ""
        lines = [f"{len(frames)} {what} sighting{'s' if len(frames) != 1 else ''}{count}{where}:"]
        lines += [f"  - {f['time']} {f.get('object') or ''} at {self.config.zone_name(f['zone'])}: {f['caption']} "
                  f"[f{f['frame_id']}]".replace("  at", " at") for f in frames]
        return "\n".join(lines)

    def _search(self, q: str, w: _Window) -> str:
        frames = self._call("query_frames", text=q, start=w.start, end=w.end, limit=8)["frames"]
        # Only answer from frames that contain every query term (caption or analyzer tags); a frame
        # that shares one word ("landing" for "helicopter landing") is a hint, not evidence.
        strong = {r["frame_id"] for r in self.tools.index.search(q, start=w.start, end=w.end, limit=50, match_all=True)}
        supported = [f for f in frames if f["frame_id"] in strong]
        if supported:
            return "Matching frames:\n" + self._listing(supported)
        if not frames:
            return NOT_ENOUGH_EVIDENCE
        return (f"{NOT_ENOUGH_EVIDENCE} No frame matches all of the words; the closest partial matches are:\n"
                + self._listing(frames[:5]))

    def _listing(self, frames: list[dict]) -> str:
        return "\n".join(f"  - {f['time']} [{self.config.zone_name(f['zone'])}] {f['caption']} [f{f['frame_id']}]"
                         for f in frames)

    # ------------------------------------------------------------------ helpers
    def _call(self, name: str, **kwargs) -> dict:
        args = {k: v for k, v in kwargs.items() if v is not None}
        self._calls.append((name, args))
        return getattr(self.tools, name)(**args)

    def _subject(self, q: str) -> Optional[str]:
        detections = _parser.parse(q)
        if detections:
            d = detections[0]
            if d.category == "vehicle" and not (d.color or d.make or d.model):
                return d.label
            if d.category == "person" and not d.attributes and not d.color:
                return "person"
            if d.category == "infrastructure":
                return f"{d.label} open"
            return d.display_name.lower()
        if _PRONOUNS.search(q):
            return self.focus
        return None

    @staticmethod
    def _trackable(subject: str) -> bool:
        """An identity needs a distinguishing detail: 'the blue F150' is one, 'a vehicle' is not."""
        parsed = _parser.parse(subject)
        if not parsed or parsed[0].category not in {"vehicle", "person", "animal"}:
            return False
        d = parsed[0]
        return d.category == "animal" or bool(d.color or d.make or d.model or d.attributes)

    def _is_night(self, hhmm: str) -> bool:
        return self.config.is_night(datetime.strptime(hhmm, "%H:%M"))


def _identities(q: str) -> list[str]:
    """Descriptions in a question that can name one identity ('blue Ford F150', not 'vehicle')."""
    found = _parser.parse(q)
    return [d.display_name.lower() for d in found if d.color or d.make or d.model or d.attributes]


def _parse_window(q: str) -> _Window:
    m = re.search(rf"(?:between|from)\s+{_TIME}\s+(?:and|to|-)\s+{_TIME}", q)
    if m:
        return _Window(m.group(1), m.group(2))
    m = re.search(rf"after\s+{_TIME}", q)
    if m:
        return _Window(start=m.group(1))
    m = re.search(rf"before\s+{_TIME}", q)
    if m:
        return _Window(end=m.group(1))
    m = re.search(rf"(?:at|around|near)\s+{_TIME}", q)
    if m:
        # "the package delivered at 10:05": what follows a stated time is usually what is asked about
        center = datetime.strptime(m.group(1), "%H:%M")
        return _Window((center - timedelta(minutes=5)).strftime("%H:%M"),
                       (center + timedelta(minutes=30)).strftime("%H:%M"), point=m.group(1))
    for word, (start, end) in _PERIODS.items():
        if re.search(rf"\b{word}\b", q):
            return _Window(start, end)
    if re.search(r"\b(night|overnight|after hours|after dark|nighttime|midnight)\b", q):
        return _Window(night=True)
    return _Window()


__all__ = ["OfflineAnalyst", "AgentAnswer"]
