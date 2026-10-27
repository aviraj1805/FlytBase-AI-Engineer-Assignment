"""Whole-video analysis: one-sentence summary, highlights and operator recommendations.

Both work offline from the frame index. The LLM layer (llm.py / the agent) can
rephrase or extend them, but the facts always come from here, so a summary can
never mention an event that is not in the index.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .config import PropertyConfig
from .indexing import FrameIndex
from .models import Severity
from .timeutil import from_ts

SECURITY_RULES = {"R-SUSPICIOUS", "R-LOITER", "R-DWELL", "R-RESTRICTED", "R-NIGHT-PERSON", "R-VEHICLE-NIGHT",
                  "R-REPEAT-VEHICLE", "R-ACCESS-OPEN"}


@dataclass
class VideoSummary:
    one_sentence: str
    highlights: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class Recommendation:
    priority: str  # P1 (act now) | P2 (today) | P3 (improve setup)
    text: str
    evidence: list[str] = field(default_factory=list)


def summarize_video(index: FrameIndex) -> VideoSummary:
    stats = index.stats()
    if not stats["frames"]:
        return VideoSummary("No footage has been processed yet.", [], stats)

    by_category: dict[str, int] = defaultdict(int)
    for entity in index.entities():
        if entity["category"] in {"person", "vehicle", "animal"}:
            by_category[entity["category"]] += 1
    vehicles = index.entities(category="vehicle")
    frequent = next((v for v in vehicles if v["visit_count"] >= 2), None)
    alerts = index.alerts()
    worst = max(alerts, key=lambda a: (a["severity"], -_minutes(a["ts"])), default=None)

    parts = [f"{by_category['person']} people", f"{by_category['vehicle']} vehicles"]
    if frequent:
        parts[1] += f" (the {frequent['display_name']} visited {frequent['visit_count']} times)"
    if by_category["animal"]:
        parts.append(f"{by_category['animal']} animals")
    sentence = (
        f"Across {stats['frames']} frames from {stats['first_ts'][11:16]} to {stats['last_ts'][11:16]}, "
        f"the drone tracked {', '.join(parts[:-1])} and {parts[-1]}, raising {len(alerts)} alerts"
    )
    sentence += f" - most serious: {_lower_first(worst['message'].rstrip('.'))}." if worst else "."

    highlights = [
        f"{a['ts'][11:16]} [{a['severity_name']}] {a['message']}"
        for a in alerts if a["severity"] >= Severity.HIGH
    ]
    return VideoSummary(sentence, highlights, stats)


def recommend(index: FrameIndex, config: PropertyConfig) -> list[Recommendation]:
    recs: list[Recommendation] = []
    alerts = index.alerts()

    # P1: incidents that need a human to look now, grouped per zone
    by_zone: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        if a["rule_id"] in SECURITY_RULES and a["severity"] >= Severity.HIGH:
            by_zone[a["zone"]].append(a)
    for zone, items in sorted(by_zone.items(), key=lambda kv: -max(a["severity"] for a in kv[1])):
        worst = max(items, key=lambda a: a["severity"])
        first, last = items[0]["ts"][11:16], items[-1]["ts"][11:16]
        when = f"at {first}" if first == last else f"between {first} and {last}"
        recs.append(Recommendation(
            "P1",
            f"Review footage at the {config.zone_name(zone)} {when} "
            f"({len(items)} serious alert{'s' if len(items) > 1 else ''}). {worst['recommended_action']}",
            [a["message"] for a in items],
        ))

    # P2: identities to verify
    for v in index.entities(category="vehicle"):
        if v["visit_count"] >= config.thresholds.repeat_vehicle_visits and not _known(config, v):
            times = ", ".join(visit["start"][11:16] for visit in v["visits"])
            recs.append(Recommendation(
                "P2",
                f"Confirm whether the {v['display_name']} is expected: it entered {v['visit_count']} times ({times}). "
                "Add it to the whitelist if it belongs to staff or a contractor.",
                [f"{v['entity_id']} visits: {times}"],
            ))
    for a in alerts:
        if a["rule_id"] == "R-VEHICLE-NIGHT":
            recs.append(Recommendation("P2", f"Identify the vehicle from this alert and pull its plate from the zoom pass: {a['message']}",
                                       [a["message"]]))

    # P3: hardening the site and the patrol
    night_zones: dict[str, int] = defaultdict(int)
    for a in alerts:
        if a["rule_id"] in SECURITY_RULES and config.is_night(from_ts(a["ts"])):
            night_zones[a["zone"]] += 1
    for zone, n in night_zones.items():
        if n >= 2:
            recs.append(Recommendation(
                "P3", f"Add an extra night patrol waypoint and IR floodlight at the {config.zone_name(zone)} ({n} night alerts).",
                [f"{n} night alerts at {zone}"],
            ))
    for a in alerts:
        if a["rule_id"] == "R-ACCESS-OPEN":
            recs.append(Recommendation("P3", f"Fit an open/closed sensor or auto-close to the {_lower_first(a['message'].split(' left open')[0])}: "
                                             f"{a['message']}", [a["message"]]))
        elif a["rule_id"] == "R-BATTERY" and a["severity"] >= Severity.HIGH:
            recs.append(Recommendation("P3", "Shorten the night patrol loop or add a mid-patrol recharge; " + _lower_first(a["message"]),
                                       [a["message"]]))
        elif a["rule_id"] == "R-GEOFENCE":
            recs.append(Recommendation("P3", "Check GPS health and wind drift logs; " + _lower_first(a["message"]), [a["message"]]))
        elif a["rule_id"] == "R-WIND":
            recs.append(Recommendation("P3", "Add an automatic weather hold to the mission plan; " + _lower_first(a["message"]),
                                       [a["message"]]))
    return recs


def _known(config: PropertyConfig, entity: dict) -> bool:
    sig = entity["signature"].split("|")  # vehicle|label|color|make|model
    clean = [None if part == "?" else part for part in sig]
    return bool(config.is_known_vehicle(clean[2], clean[3], clean[4], clean[1]))


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


def _minutes(ts: str) -> int:
    return int(ts[11:13]) * 60 + int(ts[14:16])
