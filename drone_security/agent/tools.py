"""Retrieval tools the analyst agent uses to read its long-term memory (the frame index).

They are plain Python methods returning compact JSON-able dicts, so the same
toolkit backs both the LangChain + Claude agent and the offline analyst.
Results are trimmed on purpose: tools return what an answer needs, not raw rows.
"""
from __future__ import annotations

from typing import Any, Optional

from ..analysis import recommend, summarize_video
from ..config import PropertyConfig
from ..indexing import FrameIndex
from ..perception import RuleBasedAnalyzer

_parser = RuleBasedAnalyzer()


class AnalystToolkit:
    def __init__(self, index: FrameIndex, config: PropertyConfig):
        self.index = index
        self.config = config

    # -------------------------------------------------------------------- tools
    def search_frames(self, query: str, start: Optional[str] = None, end: Optional[str] = None, limit: int = 10) -> dict:
        """Full-text search over frame captions and detection tags (BM25 ranked).
        Use for free-form questions like 'person with a ladder' or 'gate open'.
        Times may be 'HH:MM' (footage day) or 'YYYY-MM-DD HH:MM'."""
        rows = self.index.search(query, start=start, end=end, limit=limit)
        return {"query": query, "matches": [
            {"frame_id": r["frame_id"], "time": r["ts"][11:16], "zone": r["zone"], "caption": r["description"]} for r in rows
        ]}

    def find_detections(self, label: Optional[str] = None, category: Optional[str] = None, color: Optional[str] = None,
                        make: Optional[str] = None, model: Optional[str] = None, zone: Optional[str] = None,
                        action: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                        limit: int = 50) -> dict:
        """Structured lookup of detected objects. label: truck|car|van|suv|person|dog|package|gate...;
        category: person|vehicle|animal|object|infrastructure; zone: main_gate|driveway|garage|front_door|
        backyard|back_fence|warehouse. Example: 'show all truck events' -> find_detections(label='truck')."""
        rows = self.index.query_detections(label=label, category=category, color=color, make=make, model=model,
                                           zone=zone, action=action, start=start, end=end, limit=limit)
        return {"count": len(rows), "detections": [
            {"time": r["ts"][11:16], "frame_id": r["frame_id"], "object": r["display_name"], "action": r["action"],
             "zone": r["zone"], "entity_id": r["entity_id"]} for r in rows
        ]}

    def get_alerts(self, min_severity: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                   zone: Optional[str] = None, limit: int = 50) -> dict:
        """Alerts raised by the rule engine. min_severity: INFO|LOW|MEDIUM|HIGH|CRITICAL."""
        rows = self.index.alerts(min_severity=min_severity, start=start, end=end, zone=zone, limit=limit)
        return {"count": len(rows), "alerts": [
            {"time": r["ts"][11:16], "severity": r["severity_name"], "rule": r["rule_id"], "message": r["message"],
             "recommended_action": r["recommended_action"]} for r in rows
        ]}

    def entity_report(self, description: str) -> dict:
        """Everything known about one tracked identity, e.g. 'blue Ford F150' or 'person in dark hoodie':
        visits (with times), sightings, zones and alerts that involved it."""
        matches = self._match_entities(description)
        if not matches:
            return {"description": description, "found": False}
        reports = []
        for e in matches[:3]:
            alerts = [a for a in self.index.alerts() if a["entity_id"] == e["entity_id"]]
            reports.append({
                "entity_id": e["entity_id"], "name": e["display_name"], "category": e["category"],
                "visits": [{"start": v["start"][11:16], "end": v["end"][11:16], "zones": v["zones"], "left": v.get("left", False)}
                           for v in e["visits"]],
                "visit_count": e["visit_count"], "sightings": e["sightings"],
                "first_seen": e["first_seen"][11:16], "last_seen": e["last_seen"][11:16], "last_zone": e["last_zone"],
                "alerts": [f"{a['ts'][11:16]} [{a['severity_name']}] {a['message']}" for a in alerts],
            })
        return {"description": description, "found": True, "entities": reports}

    def object_inventory(self, start: Optional[str] = None, end: Optional[str] = None) -> dict:
        """What objects appeared in the video: per label, number of sightings and distinct identities."""
        rows = self.index.object_counts(start=start, end=end)
        return {"objects": [
            {"category": r["category"], "label": r["label"], "sightings": r["sightings"],
             "distinct": r["distinct_entities"], "examples": sorted(set((r["examples"] or "").split(",")))[:4],
             "first_seen": r["first_seen"][11:16], "last_seen": r["last_seen"][11:16]} for r in rows
        ]}

    def timeline(self, start: Optional[str] = None, end: Optional[str] = None, limit: int = 60) -> dict:
        """Chronological event log (entries, exits, notable actions) for a time window."""
        rows = self.index.events(start=start, end=end, limit=limit)
        return {"events": [r["message"] for r in rows]}

    def drone_status(self, time: str) -> dict:
        """Drone telemetry (position zone, altitude, battery, wind, mode) closest to a time, e.g. '03:30'."""
        row = self.index.telemetry_near(time)
        if not row:
            return {"found": False}
        keys = ("ts", "zone", "altitude_m", "battery_pct", "wind_mps", "speed_mps", "mode", "distance_from_home_m", "description")
        return {"found": True, **{k: row[k] for k in keys}}

    def video_summary(self) -> dict:
        """One-sentence summary of all processed footage plus the high-severity highlights."""
        s = summarize_video(self.index)
        return {"summary": s.one_sentence, "highlights": s.highlights, "stats": s.stats}

    def recommendations(self) -> dict:
        """Prioritised operator recommendations (P1 act now, P2 verify today, P3 harden site/patrol)."""
        return {"recommendations": [{"priority": r.priority, "text": r.text} for r in recommend(self.index, self.config)]}

    # -------------------------------------------------------------------- helpers
    def _match_entities(self, description: str) -> list[dict[str, Any]]:
        """Resolve 'the blue F150' to tracked entities: parse it like a caption, then match attributes."""
        parsed = _parser.parse(description)
        candidates = []
        if parsed:
            d = parsed[0]
            for e in self.index.entities(category=d.category):
                name = e["display_name"].lower()
                score = sum(bool(v) and v.lower() in name for v in (d.color, d.make, d.model))
                score += d.label in e["signature"]
                if d.category == "person":
                    score += sum(a in name for a in d.attributes)
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

    def as_dict(self) -> dict:
        """name -> bound method, for frameworks that build tools from callables."""
        return {name: getattr(self, name) for name in TOOL_NAMES}


TOOL_NAMES = ["search_frames", "find_detections", "get_alerts", "entity_report", "object_inventory", "timeline",
              "drone_status", "video_summary", "recommendations"]
