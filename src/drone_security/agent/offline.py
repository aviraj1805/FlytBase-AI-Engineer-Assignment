"""Offline analyst: deterministic intent routing over the same tools the LLM agent uses.

This keeps follow-up Q&A working with no API key (and makes it unit-testable). It
handles the question shapes operators actually ask: summaries, inventories,
"how many times did X enter", alerts in a window, drone status at a time, plus
follow-ups that refer back to the previous subject ("when did it leave?").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..config import PropertyConfig
from ..perception import RuleBasedAnalyzer
from ..perception.lexicon import CLOTHING
from ..perception.rule_analyzer import zone_in
from .tools import AnalystToolkit

_parser = RuleBasedAnalyzer()
_CLOTHING = set(CLOTHING.values())
_TIME = r"(\d{1,2}:\d{2})"
_PRONOUNS = re.compile(r"\b(it|its|they|them|their|that one|this one|he|she|him|her|that vehicle|that person)\b")
_PERIODS = {"morning": ("06:00", "11:59"), "afternoon": ("12:00", "16:59"), "evening": ("17:00", "21:59"),
            "midnight": ("00:00", "00:59")}


@dataclass
class AgentAnswer:
    text: str
    mode: str  # "claude" | "offline"
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)


@dataclass
class _Window:
    start: Optional[str] = None
    end: Optional[str] = None
    point: Optional[str] = None
    night: bool = False


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

        timed = bool(window.start or window.end or window.night)
        if re.search(r"what happened|what was going on|activity|timeline", q) and timed:
            text = self._timeline(window)
        elif re.search(r"summar|overview|what happened|recap|tl;?dr|brief me", q):
            text = self._summary()
        elif re.search(r"recommend|what should|advice|advise|suggest|next steps|action items|to do\b", q):
            text = self._recommendations()
        elif re.search(r"battery|altitude|wind|telemetry|drone status|drone position|where was the drone|geofence|flight", q):
            text = self._drone(window)
        elif re.search(r"what (objects|things)|which objects|inventory|what did (you|the drone) see|objects (were )?in", q):
            text = self._inventory(window)
        elif (subject and self._is_trackable(subject)
              and re.search(r"how many times|how often|visit|when did|came back|return|enter|arriv|leave|\bleft\b(?! open)|history", q)):
            text = self._entity(subject, q)
        elif re.search(r"alert|threat|suspicious|incident|intru|danger|unusual|security (issue|event)|wrong", q):
            severity = "CRITICAL" if "critical" in q else "HIGH" if re.search(r"serious|severe|high", q) else None
            text = self._alerts(window, zone_in(q), severity)
        elif subject or zone_in(q):
            text = self._detections(subject, q, window)
        else:
            text = self._search(q, window)
        return AgentAnswer(text, self.mode, list(self._calls))

    # ------------------------------------------------------------------ intents
    def _summary(self) -> str:
        out = self._call("video_summary")
        lines = [out["summary"]]
        if out["highlights"]:
            lines.append("Highlights:")
            lines += [f"  - {h}" for h in out["highlights"]]
        return "\n".join(lines)

    def _recommendations(self) -> str:
        recs = self._call("recommendations")["recommendations"]
        if not recs:
            return "No actions needed: nothing in the footage calls for follow-up."
        return "Recommended actions:\n" + "\n".join(f"  [{r['priority']}] {r['text']}" for r in recs)

    def _drone(self, w: _Window) -> str:
        if w.point:
            s = self._call("drone_status", time=w.point)
            if not s["found"]:
                return "No telemetry recorded yet."
            return (f"At {s['ts'][11:16]} the drone was in {s['mode']} mode over the {self.config.zone_name(s['zone'])}: "
                    f"altitude {s['altitude_m']:.0f} m, battery {s['battery_pct']:.0f}%, wind {s['wind_mps']:.1f} m/s, "
                    f"{s['distance_from_home_m']:.0f} m from the dock. Camera: \"{s['description']}\"")
        alerts = [a for a in self._call("get_alerts", start=w.start, end=w.end)["alerts"]
                  if a["rule"] in {"R-BATTERY", "R-GEOFENCE", "R-ALTITUDE", "R-WIND"}]
        if not alerts:
            return "No flight-safety issues: battery, altitude, wind and geofence stayed within limits."
        return "Flight-safety events:\n" + "\n".join(f"  - {a['time']} [{a['severity']}] {a['message']}" for a in alerts)

    def _inventory(self, w: _Window) -> str:
        objects = self._call("object_inventory", start=w.start, end=w.end)["objects"]
        if not objects:
            return "Nothing was detected in that window."
        groups: dict[str, list[str]] = {}
        for o in objects:
            examples = ", ".join(e for e in o["examples"] if e)
            groups.setdefault(o["category"], []).append(
                f"{o['label']} ({o['sightings']} sightings, {o['distinct']} distinct; e.g. {examples})")
        return "Objects seen in the footage:\n" + "\n".join(f"  {cat}: " + "; ".join(items) for cat, items in groups.items())

    def _entity(self, subject: str, q: str) -> str:
        report = self._call("entity_report", description=subject)
        if not report["found"]:
            return f"I have no tracked identity matching '{subject}'."
        e = report["entities"][0]
        self.focus = e["name"]
        visits = e["visits"]
        if re.search(r"leave|left|exit|depart", q):
            exits = [v["end"] for v in visits if v.get("left")]
            text = f"{e['name']} was seen leaving at {', '.join(exits)}." if exits else f"No exit was observed for {e['name']}."
            if not visits[-1].get("left"):
                text += (f" After its {visits[-1]['start']} arrival it was last seen at {visits[-1]['end']} "
                         f"at the {self.config.zone_name(e['last_zone'])} (no exit observed).")
            return text
        times = ", ".join(f"{v['start']}-{v['end']} ({', '.join(z.replace('_', ' ') for z in v['zones'])})" for v in visits)
        text = (f"{e['name']} ({e['entity_id']}) made {e['visit_count']} visit{'s' if e['visit_count'] != 1 else ''} "
                f"with {e['sightings']} sightings: {times}.")
        if e["alerts"]:
            text += "\nRelated alerts:\n" + "\n".join(f"  - {a}" for a in e["alerts"])
        return text

    def _alerts(self, w: _Window, zone: Optional[str], min_severity: Optional[str]) -> str:
        out = self._call("get_alerts", min_severity=min_severity, start=w.start, end=w.end, zone=zone)
        alerts = out["alerts"]
        if w.night:
            alerts = [a for a in alerts if self._is_night(a["time"])]
        level = {"CRITICAL": "critical ", "HIGH": "high/critical "}.get(min_severity or "", "")
        if not alerts:
            return f"No {level}alerts in that window."
        header = f"{len(alerts)} {level}alert{'s' if len(alerts) != 1 else ''}" + (" at night" if w.night else "") + ":"
        return header + "\n" + "\n".join(f"  - {a['time']} [{a['severity']}] {a['message']}" for a in alerts)

    def _timeline(self, w: _Window) -> str:
        events = self._call("timeline", start=w.start, end=w.end)["events"]
        alerts = self._call("get_alerts", start=w.start, end=w.end)["alerts"]
        if w.night:
            alerts = [a for a in alerts if self._is_night(a["time"])]
        if not events and not alerts:
            return "Nothing notable happened in that window."
        lines = ["Events:"] + [f"  - {e}" for e in events[:30]] if events else []
        if alerts:
            lines += ["Alerts:"] + [f"  - {a['time']} [{a['severity']}] {a['message']}" for a in alerts]
        return "\n".join(lines)

    def _detections(self, subject: Optional[str], q: str, w: _Window) -> str:
        parsed = _parser.parse(q)
        if parsed and parsed[0].category == "person" and any(a not in _CLOTHING for a in parsed[0].attributes):
            return self._search(q, w)  # "person with a ladder": carried items live in captions, not filters
        filters = self._filters(subject)
        zone = zone_in(q)
        out = self._call("find_detections", zone=zone, start=w.start, end=w.end, **filters)
        rows = out["detections"]
        if w.night:
            rows = [r for r in rows if self._is_night(r["time"])]
        what = subject or "activity"
        where = f" at the {self.config.zone_name(zone)}" if zone else ""
        if not rows:
            # fall back to free text (catches things the structured filters cannot express)
            return self._search(q, w) if subject else f"No {what}{where} in that window."
        self.focus = subject or self.focus
        lines = [f"{len(rows)} {what} event{'s' if len(rows) != 1 else ''}{where}" + (" at night" if w.night else "") + ":"]
        lines += [f"  - {r['time']} {r['object']}{' ' + r['action'] if r['action'] else ''} at "
                  f"{self.config.zone_name(r['zone'])} (frame {r['frame_id']})" for r in rows[:25]]
        if len(rows) > 25:
            lines.append(f"  ... {len(rows) - 25} more")
        return "\n".join(lines)

    def _search(self, q: str, w: _Window) -> str:
        matches = self._call("search_frames", query=q, start=w.start, end=w.end)["matches"]
        if not matches:
            return "I could not find frames matching that. Try naming an object, colour, zone or time."
        return "Closest matching frames:\n" + "\n".join(
            f"  - {m['time']} [{self.config.zone_name(m['zone'])}] {m['caption']} (frame {m['frame_id']})" for m in matches)

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
                return f"{d.label} open"  # bare "gate" is a zone; "gate open" parses back to the access point
            return d.display_name.lower()
        if _PRONOUNS.search(q):
            return self.focus
        return None

    def _filters(self, subject: Optional[str]) -> dict:
        if not subject:
            return {}
        if re.fullmatch(r"vehicles?", subject.strip()):  # "vehicle" parses as label "car"; the user means any
            return {"category": "vehicle"}
        d = (_parser.parse(subject) or [None])[0]
        if d is None:
            return {}
        return {"label": d.label, "color": d.color, "make": d.make, "model": d.model}

    @staticmethod
    def _is_trackable(subject: str) -> bool:
        parsed = _parser.parse(subject)
        return bool(parsed) and parsed[0].category in {"vehicle", "person", "animal"}

    def _is_night(self, hhmm: str) -> bool:
        return self.config.is_night(datetime.strptime(hhmm, "%H:%M"))


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
        center = datetime.strptime(m.group(1), "%H:%M")
        return _Window((center - timedelta(minutes=10)).strftime("%H:%M"),
                       (center + timedelta(minutes=10)).strftime("%H:%M"), point=m.group(1))
    for word, (start, end) in _PERIODS.items():
        if re.search(rf"\b{word}\b", q):
            return _Window(start, end)
    if re.search(r"\b(night|overnight|after hours|after dark|nighttime)\b", q):
        return _Window(night=True)
    return _Window()
