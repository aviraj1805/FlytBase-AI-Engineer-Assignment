"""The five retrieval tools the analyst agent uses to read its long-term memory (the index).

They are plain Python methods returning compact JSON-able dicts, so the same toolkit backs
both the LangChain + Gemini agent and the offline analyst. Every result carries frame ids,
which is what lets answers cite evidence ("[f123]") and lets us check those citations.
Results are trimmed on purpose: tools return what an answer needs, not raw rows.
"""
from __future__ import annotations

from typing import Any, Optional

from ..analysis import DRONE_RULES, summarize_video
from ..config import PropertyConfig
from ..indexing import FrameIndex
from ..perception import RuleBasedAnalyzer
from ..retrieval import HybridRetriever
from ..timeutil import from_ts
from .grounding import cited_frame_ids

_parser = RuleBasedAnalyzer()
TOOL_NAMES = ["query_frames", "entity_history", "list_alerts", "summarize_window", "drone_status"]


class AnalystToolkit:
    def __init__(self, index: FrameIndex, config: PropertyConfig, retriever: Optional[HybridRetriever] = None):
        self.index = index
        self.config = config
        self.retriever = retriever or HybridRetriever(index)
        self.retrieved_ids: set[int] = set()  # every frame id any tool returned (for citation checks)

    # -------------------------------------------------------------------- tools
    def query_frames(self, text: Optional[str] = None, object: Optional[str] = None, zone: Optional[str] = None,
                     start: Optional[str] = None, end: Optional[str] = None, limit: int = 12) -> dict:
        """Find frames. Use `object` for things the analyzer detects ('truck', 'blue Ford F150', 'person',
        'package', 'gate open'), `text` for anything else in free words ('someone hanging around the entrance'),
        or only a time window to list frames. zone: main_gate|driveway|garage|front_door|backyard|back_fence|
        warehouse. Times: 'HH:MM' on the footage day or 'YYYY-MM-DD HH:MM'. Returns frame ids, times, captions."""
        if object:
            filters = self._object_filters(object)
            rows = self.index.query_detections(zone=zone, start=start, end=end, limit=limit, **filters)
            frames = [{"frame_id": r["frame_id"], "time": r["ts"][11:16], "zone": r["zone"], "caption": r["description"],
                       "object": r["display_name"], "entity_id": r["entity_id"]} for r in rows]
            how = f"detections matching {filters}"
        elif text:
            rows = self.retriever.search(text, start=start, end=end, zone=zone, limit=limit)
            frames = [{"frame_id": r["frame_id"], "time": r["ts"][11:16], "zone": r["zone"], "caption": r["description"]}
                      for r in rows]
            how = f"{self.retriever.mode} search"
        else:
            rows = self.index.query_frames(start=start, end=end, zone=zone, limit=limit)
            frames = [{"frame_id": r["frame_id"], "time": r["ts"][11:16], "zone": r["zone"], "caption": r["description"]}
                      for r in rows]
            how = "frames in the window"
        return self._remember({"how": how, "count": len(frames), "frames": frames})

    def entity_history(self, description: str) -> dict:
        """Everything known about one tracked identity, e.g. 'blue Ford F150' or 'person in dark hoodie':
        visits (start/end times, zones, frame ids), number of sightings, re-identification confidence and the
        alerts it was involved in. Use for 'how many times did X come', 'when did it leave'."""
        matches = self._match_entities(description)
        if not matches:
            return {"description": description, "found": False}
        reports = []
        for e in matches[:3]:
            alerts = self.index.alerts(entity_id=e["entity_id"])
            reports.append({
                "entity_id": e["entity_id"], "name": e["display_name"], "category": e["category"],
                "visit_count": e["visit_count"], "sightings": e["sightings"],
                "reid_confidence": e["mean_confidence"],
                "visits": [{"start": v["start"][11:16], "end": v["end"][11:16], "zones": v["zones"], "left": v["left"],
                            "frame_ids": _ends(v["frame_ids"])} for v in e["visits"]],
                "alerts": [{"time": a["ts"][11:16], "severity": a["severity_name"], "rule": a["rule_id"],
                            "message": a["message"], "frame_id": a["frame_id"]} for a in alerts],
            })
        return self._remember({"description": description, "found": True, "entities": reports})

    def list_alerts(self, min_severity: Optional[str] = None, rule_id: Optional[str] = None, zone: Optional[str] = None,
                    start: Optional[str] = None, end: Optional[str] = None, limit: int = 40) -> dict:
        """Alerts raised by the rule engine, each with its rule, severity, time, message, the plain-English
        explanation of why it fired and its evidence frame ids. min_severity: LOW|MEDIUM|HIGH|CRITICAL.
        rule_id: INTRUSION, LOITERING, RESTRICTED_ZONE, AFTER_HOURS_PERSON, AFTER_HOURS_VEHICLE, REPEAT_VEHICLE,
        UNATTENDED_OBJECT, ACCESS_OPEN, BATTERY_LOW, BATTERY_CRITICAL, GEOFENCE, GPS_DRIFT, LOW_ALTITUDE, HIGH_WIND."""
        rows = self.index.alerts(min_severity=min_severity or None, rule_id=rule_id or None, zone=zone or None,
                                 start=start, end=end, limit=limit)
        return self._remember({"count": len(rows), "alerts": [
            {"time": r["ts"][11:16], "severity": r["severity_name"], "rule": r["rule_id"], "message": r["message"],
             "explanation": r["explanation"], "frame_id": r["frame_id"], "evidence_frame_ids": r["evidence_frame_ids"],
             "recommended_action": r["recommended_action"]} for r in rows
        ]})

    def summarize_window(self, start: Optional[str] = None, end: Optional[str] = None) -> dict:
        """Hierarchical summary of a time window, plus the objects seen in it. No window: the whole footage (day
        summaries). Windows longer than 2 hours: hour summaries. Shorter windows: 10-minute summaries. Every
        summary line cites frame ids; drill down with a narrower window or query_frames."""
        start_ts, end_ts = self.index.time_range(start, end)
        if not start_ts and not end_ts:
            level = "day"
        else:
            hours = (from_ts(end_ts or self.index.stats()["last_ts"]) - from_ts(start_ts or self.index.stats()["first_ts"])).total_seconds() / 3600
            level = "hour" if hours > 2 else "minute"
        rows = self.index.summaries(level, start=start, end=end)
        objects = self.index.object_counts(start=start, end=end)
        return self._remember({
            "level": level,
            "one_sentence": summarize_video(self.index).one_sentence if level == "day" else None,
            "summaries": [r["text"] for r in rows if r["frame_ids"] or level != "minute"],
            "frame_ids": sorted({i for r in rows for i in r["frame_ids"]}),
            "objects": [{"category": o["category"], "label": o["label"], "sightings": o["sightings"],
                         "distinct": o["distinct_entities"], "first_frame_id": o["first_frame"]} for o in objects],
        })

    def drone_status(self, time: Optional[str] = None) -> dict:
        """Drone telemetry. With a time ('03:30'): zone, mode, altitude, battery, wind, GPS HDOP and distance from
        the dock at the closest frame. Without a time: the extremes of the footage (lowest battery, furthest from
        dock, worst GPS, lowest altitude, highest wind) and all flight-safety alerts."""
        keys = ("frame_id", "ts", "zone", "mode", "altitude_m", "battery_pct", "wind_mps", "gps_hdop", "distance_from_home_m")
        if time:
            row = self.index.telemetry_near(time)
            if not row:
                return {"found": False}
            return self._remember({"found": True, "telemetry": {k: row[k] for k in keys}})
        extremes = self.index.telemetry_extremes()
        alerts = [a for a in self.index.alerts() if a["rule_id"] in DRONE_RULES]
        return self._remember({
            "found": True,
            "extremes": {name: {k: row[k] for k in keys} for name, row in extremes.items() if row},
            "alerts": [{"time": a["ts"][11:16], "rule": a["rule_id"], "severity": a["severity_name"],
                        "message": a["message"], "frame_id": a["frame_id"]} for a in alerts],
        })

    # -------------------------------------------------------------------- helpers
    def _object_filters(self, description: str) -> dict:
        if description.strip().lower() in ("vehicle", "vehicles"):
            return {"category": "vehicle"}
        if description.strip().lower() in ("people", "persons", "person"):
            return {"category": "person"}
        parsed = _parser.parse(description)
        if not parsed:
            return {"label": description.strip().lower()}
        d = parsed[0]
        filters = {"label": d.label, "color": d.color, "make": d.make, "model": d.model}
        return {k: v for k, v in filters.items() if v}

    def _match_entities(self, description: str) -> list[dict[str, Any]]:
        """Resolve 'the blue F150' to tracked entities: parse it like a caption, then score attribute overlap."""
        parsed = _parser.parse(description)
        candidates = []
        if parsed:
            d = parsed[0]
            wanted = [v.lower() for v in (d.color, d.make, d.model) if v] + [a for a in d.attributes]
            for e in self.index.entities(category=d.category):
                name, signature = e["display_name"].lower(), e["signature"].lower()
                score = sum(w in name or w in signature for w in wanted) + (d.label in signature)
                if score:
                    candidates.append((score, e))
        if not candidates:
            words = [w for w in description.lower().split() if w not in {"the", "a", "an"}]
            for e in self.index.entities():
                score = sum(w in e["display_name"].lower() for w in words)
                if score:
                    candidates.append((score, e))
        candidates.sort(key=lambda c: (c[0], c[1]["visit_count"], c[1]["sightings"]), reverse=True)
        best = candidates[0][0] if candidates else 0
        return [e for score, e in candidates if score == best]

    def _remember(self, result: dict) -> dict:
        self.retrieved_ids |= _frame_ids_in(result)
        return result

    def as_dict(self) -> dict:
        """name -> bound method, for frameworks that build tools from callables."""
        return {name: getattr(self, name) for name in TOOL_NAMES}


def _ends(frame_ids: list[int]) -> list[int]:
    """First and last few frames of a visit: enough to cite without flooding the context."""
    return frame_ids if len(frame_ids) <= 6 else frame_ids[:3] + frame_ids[-3:]


def _frame_ids_in(value) -> set[int]:
    """All frame ids mentioned anywhere in a tool result (keys ending in frame_id / frame_ids)."""
    found: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("frame_id", "first_frame_id") and isinstance(item, int):
                found.add(item)
            elif key in ("frame_ids", "evidence_frame_ids") and isinstance(item, list):
                found |= {i for i in item if isinstance(i, int)}
            elif key in ("summaries", "one_sentence"):
                found |= cited_frame_ids(item)
            else:
                found |= _frame_ids_in(item)
    elif isinstance(value, list):
        for item in value:
            found |= _frame_ids_in(item)
    return found
