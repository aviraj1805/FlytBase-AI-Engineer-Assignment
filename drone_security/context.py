"""Context management for the analyst agent.

A day of patrol footage is thousands of frames; pasting them all into a prompt is
slow, costly and buries the signal. The agent gets a two-tier memory instead:

* **Working memory** (rendered into the system prompt, hard character budget):
  property profile, coverage, high-severity alerts, key entities, hierarchical
  timeline digests (one per `chunk_minutes` of footage), and the latest frames.
  Sections are added in priority order until the budget is used up.
* **Long-term memory** (the SQLite frame index): every frame, detection, entity,
  log and alert, which the agent reaches through retrieval tools on demand.

Digests are produced as footage streams in, so the context is always ready, and a
pluggable `summarizer` lets an LLM write them instead of the deterministic template.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from .config import PropertyConfig
from .models import Alert, FrameResult, Severity
from .timeutil import to_ts
from .tracking import EntityTracker


@dataclass
class Chunk:
    start: datetime
    end: datetime
    frames: int = 0
    sightings: Counter = field(default_factory=Counter)  # display name @ zone -> count
    alerts: list[Alert] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def facts(self) -> str:
        """Compact factual dump of the chunk, the input for any summarizer."""
        lines = [f"Window {self.start:%H:%M}-{self.end:%H:%M}, {self.frames} frames."]
        if self.logs:
            lines.append("Events: " + " ".join(self.logs[:12]))
        if self.alerts:
            lines.append("Alerts: " + " ".join(f"[{a.severity.name}] {a.message}" for a in self.alerts[:8]))
        return "\n".join(lines)


def template_summary(chunk: Chunk) -> str:
    if not chunk.sightings and not chunk.alerts:
        return f"{chunk.start:%H:%M}-{chunk.end:%H:%M}: quiet ({chunk.frames} patrol frames, nothing detected)."
    seen = ", ".join(f"{name} x{n}" if n > 1 else name for name, n in chunk.sightings.most_common(5))
    text = f"{chunk.start:%H:%M}-{chunk.end:%H:%M}: {chunk.frames} frames; seen: {seen or 'nothing'}."
    if chunk.alerts:
        worst = max(chunk.alerts, key=lambda a: a.severity)
        counts = Counter(a.severity.name for a in chunk.alerts)
        text += f" Alerts: {', '.join(f'{n} {s}' for s, n in counts.items())} (worst: {worst.message})"
    return text


class ContextManager:
    def __init__(
        self,
        config: PropertyConfig,
        tracker: EntityTracker,
        *,
        index=None,
        chunk_minutes: int = 60,
        recent_frames: int = 8,
        summarizer: Optional[Callable[[Chunk], str]] = None,
    ):
        self.config = config
        self.tracker = tracker
        self.index = index
        self.chunk_span = timedelta(minutes=chunk_minutes)
        self.summarizer = summarizer or template_summary
        self.recent: deque[FrameResult] = deque(maxlen=recent_frames)
        self.digests: list[tuple[datetime, datetime, str]] = []
        self.alerts: list[Alert] = []
        self.first_ts: Optional[datetime] = None
        self.last_ts: Optional[datetime] = None
        self.frames = 0
        self._chunk: Optional[Chunk] = None

    # ------------------------------------------------------------------ ingest
    def observe(self, result: FrameResult) -> None:
        at = result.frame.timestamp
        if self._chunk and at >= self._chunk.end:
            self._close_chunk()
        if self._chunk is None:
            start = at.replace(minute=0, second=0, microsecond=0)
            while start + self.chunk_span <= at:
                start += self.chunk_span
            self._chunk = Chunk(start, start + self.chunk_span)

        chunk = self._chunk
        chunk.frames += 1
        for det in result.detections:
            chunk.sightings[f"{det.display_name} @ {self.config.zone_name(det.zone)}"] += 1
        chunk.alerts.extend(result.alerts)
        chunk.logs.extend(log.message for log in result.logs)

        self.recent.append(result)
        self.alerts.extend(result.alerts)
        self.first_ts = self.first_ts or at
        self.last_ts = at
        self.frames += 1

    def close(self) -> None:
        if self._chunk:
            self._close_chunk()

    def _close_chunk(self) -> None:
        chunk, self._chunk = self._chunk, None
        try:
            text = self.summarizer(chunk)
        except Exception:  # an LLM summarizer failing must never stall the feed
            text = template_summary(chunk)
        self.digests.append((chunk.start, chunk.end, text))
        if self.index is not None:
            self.index.add_summary("chunk", to_ts(chunk.start), to_ts(chunk.end), text)

    # ------------------------------------------------------------------ render
    def render(self, budget_chars: int = 6000) -> str:
        """Working-memory block for the agent prompt, highest-priority sections first."""
        cfg = self.config
        serious = [a for a in self.alerts if a.severity >= Severity.HIGH]
        entities = sorted(self.tracker.entities.values(), key=lambda e: (len(e.visits), e.sightings), reverse=True)
        coverage = (f"{self.frames} frames from {self.first_ts:%Y-%m-%d %H:%M} to {self.last_ts:%Y-%m-%d %H:%M}."
                    if self.first_ts else "")
        return budgeted([
            ("Property", property_lines(cfg)),
            ("Coverage", [coverage] if coverage else []),
            ("High-severity alerts", [f"[{a.severity.name}] {a.message}" for a in serious[-10:]]),
            ("Key entities", [
                f"{e.entity_id} {e.display_name}: {len(e.visits)} visit(s), {e.sightings} sightings, "
                f"first {e.first_seen:%H:%M}, last {e.last_seen:%H:%M} at {cfg.zone_name(e.last_zone)}"
                for e in entities[:12]
            ]),
            ("Timeline digests (hourly, newest first)", [text for _, _, text in reversed(self.digests)]),
            ("Latest frames", [
                f"#{r.frame.frame_id} {r.frame.timestamp:%H:%M} [{cfg.zone_name(r.frame.telemetry.zone)}] {r.frame.description}"
                for r in reversed(self.recent)
            ]),
        ], budget_chars)


def render_from_index(index, config: PropertyConfig, budget_chars: int = 6000) -> str:
    """Rebuild the same working memory from a stored index (e.g. `ask` after the run has ended)."""
    stats = index.stats()
    serious = index.alerts(min_severity=Severity.HIGH)
    entities = index.entities()
    latest = list(reversed(index.query_frames(start=stats["last_ts"], limit=1))) if stats["frames"] else []
    coverage = f"{stats['frames']} frames from {stats['first_ts'][:16]} to {stats['last_ts'][:16]}." if stats["frames"] else ""
    return budgeted([
        ("Property", property_lines(config)),
        ("Coverage", [coverage] if coverage else []),
        ("High-severity alerts", [f"[{a['severity_name']}] {a['message']}" for a in serious[-10:]]),
        ("Key entities", [
            f"{e['entity_id']} {e['display_name']}: {e['visit_count']} visit(s), {e['sightings']} sightings, "
            f"first {e['first_seen'][11:16]}, last {e['last_seen'][11:16]} at {config.zone_name(e['last_zone'])}"
            for e in entities[:12]
        ]),
        ("Timeline digests (hourly, newest first)", [row["text"] for row in reversed(index.summaries("chunk"))]),
        ("Latest frames", [f"#{f['frame_id']} {f['ts'][11:16]} [{config.zone_name(f['zone'])}] {f['description']}"
                           for f in latest]),
    ], budget_chars)


def property_lines(cfg: PropertyConfig) -> list[str]:
    return [
        f"{cfg.property_name}; drone {cfg.drone_id}; night hours {cfg.night.start:%H:%M}-{cfg.night.end:%H:%M}; "
        f"business hours {cfg.business.start:%H:%M}-{cfg.business.end:%H:%M}.",
        "Zones: " + ", ".join(f"{z.id} ({z.name}{', perimeter' if z.perimeter else ''}{', restricted' if z.restricted else ''})"
                              for z in cfg.zones),
    ]


def budgeted(sections: list[tuple[str, list[str]]], budget_chars: int) -> str:
    """Join sections in priority order, truncating once the character budget is spent.

    Every non-empty section keeps at least its first line so the agent knows it exists
    and can fetch the rest with tools.
    """
    out: list[str] = []
    used = 0
    for title, lines in sections:
        if not lines:
            continue
        header = f"## {title}\n"
        kept: list[str] = []
        for line in lines:
            cost = len(line) + 1
            if kept and used + len(header) + cost > budget_chars:
                kept.append(f"... ({len(lines) - len(kept)} more - use the search tools)")
                break
            kept.append(line)
            used += cost
        used += len(header)
        out.append(header + "\n".join(kept))
    return "\n\n".join(out)
