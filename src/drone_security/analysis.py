"""Whole-video analysis: one-sentence summary, daily security briefing, recommendations.

All three are built offline from the frame index, so the facts (times, counts, frame
ids) always come from stored data and a summary can never mention an event that is not
in the index. When Gemini is configured, `daily_briefing(..., llm=...)` asks it to
rewrite the same facts as prose and keeps the offline version if the call fails.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .config import PropertyConfig
from .indexing import FrameIndex
from .models import Severity
from .timeutil import from_ts

SECURITY_RULES = {"INTRUSION", "LOITERING", "RESTRICTED_ZONE", "AFTER_HOURS_PERSON", "AFTER_HOURS_VEHICLE",
                  "REPEAT_VEHICLE", "UNATTENDED_OBJECT", "ACCESS_OPEN"}
DRONE_RULES = {"BATTERY_LOW", "BATTERY_CRITICAL", "GEOFENCE", "GPS_DRIFT", "LOW_ALTITUDE", "HIGH_WIND"}


@dataclass
class VideoSummary:
    one_sentence: str
    highlights: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class Recommendation:
    priority: str  # P1 (act now) | P2 (verify today) | P3 (improve setup)
    text: str
    frame_ids: list[int] = field(default_factory=list)


def summarize_video(index: FrameIndex) -> VideoSummary:
    stats = index.stats()
    if not stats["frames"]:
        return VideoSummary("No footage has been processed yet.", [], stats)

    counts: dict[str, int] = defaultdict(int)
    for entity in index.entities():
        counts[entity["category"]] += 1
    vehicles = index.entities(category="vehicle")
    frequent = next((v for v in vehicles if v["visit_count"] >= 2), None)
    alerts = index.alerts()
    worst = max(alerts, key=lambda a: (a["severity"], -_minutes(a["ts"])), default=None)

    parts = [f"{counts['person']} people", f"{counts['vehicle']} vehicles"]
    if frequent:
        visits = {2: "twice"}.get(frequent["visit_count"], f"{frequent['visit_count']} times")
        parts[1] += f" (the {frequent['display_name']} visited {visits})"
    if counts["animal"]:
        parts.append(f"{counts['animal']} animals")
    sentence = (f"Across {stats['frames']} frames from {stats['first_ts'][11:16]} to {stats['last_ts'][11:16]}, "
                f"the drone tracked {', '.join(parts[:-1])} and {parts[-1]} and raised {len(alerts)} alerts")
    if worst:
        sentence += f"; the most serious was {_lower_first(worst['message'].rstrip('.'))} [f{worst['frame_id']}]."
    else:
        sentence += "."
    highlights = [f"{a['ts'][11:16]} [{a['severity_name']}] {a['message']} [f{a['frame_id']}]"
                  for a in alerts if a["severity"] >= Severity.HIGH]
    return VideoSummary(sentence, highlights, stats)


def daily_briefing(index: FrameIndex, config: PropertyConfig, *, llm=None) -> str:
    """A short briefing for the property owner: night, day, drone health, actions."""
    facts = briefing_facts(index, config)
    if llm is not None:
        try:
            return llm.text(
                "You write a security briefing for a property owner from a drone's incident log. Use ONLY the facts "
                "given. Keep every [fN] frame citation next to the fact it supports. 120-180 words, plain prose with "
                "short headings: Overnight, Daytime, Drone health, Actions.\n\nFacts:\n" + facts)
        except Exception:  # network, quota, safety block: the offline briefing is still correct
            pass
    return facts


def briefing_facts(index: FrameIndex, config: PropertyConfig) -> str:
    stats = index.stats()
    if not stats["frames"]:
        return "No footage has been processed yet."
    alerts = index.alerts()
    night = [a for a in alerts if a["rule_id"] in SECURITY_RULES and config.is_night(from_ts(a["ts"]))]
    day = [a for a in alerts if a["rule_id"] in SECURITY_RULES and not config.is_night(from_ts(a["ts"]))]
    drone = [a for a in alerts if a["rule_id"] in DRONE_RULES]
    lines = [f"Security briefing, {config.property_name}, {stats['first_ts'][:10]} "
             f"({stats['frames']} frames, {len(alerts)} alerts).", ""]
    for title, items in (("Overnight", night), ("Daytime", day), ("Drone health", drone)):
        lines.append(f"{title}:")
        lines += [f"- {a['ts'][11:16]} {a['severity_name']}: {a['message']} [f{a['frame_id']}]" for a in items] or ["- nothing to report"]
    lines.append("Actions:")
    lines += [f"- [{r.priority}] {r.text}" for r in recommend(index, config)[:5]] or ["- none"]
    return "\n".join(lines)


def recommend(index: FrameIndex, config: PropertyConfig) -> list[Recommendation]:
    recs: list[Recommendation] = []
    alerts = index.alerts()

    # P1: serious security incidents, grouped per zone, most severe first
    by_zone: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        if a["rule_id"] in SECURITY_RULES and a["severity"] >= Severity.HIGH:
            by_zone[a["zone"]].append(a)
    for zone, items in sorted(by_zone.items(), key=lambda kv: -max(a["severity"] for a in kv[1])):
        worst = max(items, key=lambda a: a["severity"])
        first, last = items[0]["ts"][11:16], items[-1]["ts"][11:16]
        when = f"at {first}" if first == last else f"between {first} and {last}"
        ids = [a["frame_id"] for a in items]
        recs.append(Recommendation("P1", f"Review the {config.zone_name(zone)} footage {when} "
                                         f"({_cite(ids)}). {worst['recommended_action']}", ids))

    # P2: identities and items to verify
    for a in alerts:
        if a["rule_id"] in ("REPEAT_VEHICLE", "AFTER_HOURS_VEHICLE", "UNATTENDED_OBJECT"):
            recs.append(Recommendation("P2", f"{a['recommended_action']} ({a['message']} [f{a['frame_id']}])", [a["frame_id"]]))

    # P3: hardening the site and the patrol plan
    night_zones: dict[str, list[int]] = defaultdict(list)
    for a in alerts:
        if a["rule_id"] in SECURITY_RULES and config.is_night(from_ts(a["ts"])):
            night_zones[a["zone"]].append(a["frame_id"])
    for zone, ids in night_zones.items():
        if len(ids) >= 2:
            recs.append(Recommendation("P3", f"Add a night patrol waypoint and IR floodlight at the {config.zone_name(zone)} "
                                             f"({len(ids)} night alerts, {_cite(ids)}).", ids))
    for a in alerts:
        if a["rule_id"] == "ACCESS_OPEN":
            recs.append(Recommendation("P3", f"Fit an open/closed sensor or auto-close: {a['message']} [f{a['frame_id']}]",
                                       [a["frame_id"]]))
        elif a["rule_id"] in DRONE_RULES and a["severity"] >= Severity.MEDIUM:
            recs.append(Recommendation("P3", f"{a['recommended_action']} ({a['message']} [f{a['frame_id']}])", [a["frame_id"]]))
    return recs


def _cite(frame_ids: list[int]) -> str:
    return ", ".join(f"[f{i}]" for i in frame_ids[:4]) + (" ..." if len(frame_ids) > 4 else "")


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


def _minutes(ts: str) -> int:
    return int(ts[11:13]) * 60 + int(ts[14:16])


__all__ = ["VideoSummary", "Recommendation", "summarize_video", "daily_briefing", "briefing_facts", "recommend",
           "SECURITY_RULES", "DRONE_RULES"]
