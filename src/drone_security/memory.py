"""Hierarchical memory: frame -> minute -> hour -> day summaries for long footage.

A day of patrol footage is hundreds to thousands of frames; months of it are millions.
Putting every frame into the prompt does not scale (see the context experiment in
docs/results). Instead the memory keeps a pyramid that is built while frames stream in:

* **minute** windows (`minute_span` minutes, default 10): the events and alerts in that
  window, each with the frame id that shows it ("[f123]"); quiet windows are one line.
* **hour** summaries, built from the minute summaries of that hour.
* **day** summaries, built from the hour summaries of that day.

The agent's prompt gets the coarse levels plus a rolling window of the latest minute
summaries and raw frames, within a token budget. Anything finer is one tool call away
(`summarize_window` drills down a level, `query_frames` reaches the raw frames). Every
summary line keeps its frame citations, so answers built on summaries stay checkable.

Minute summaries are always written by the deterministic template (cheap, many). An LLM
summarizer, when configured, writes only the hour and day levels (24 + 1 calls a day).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from .config import PropertyConfig
from .models import FrameResult
from .timeutil import to_ts


@dataclass
class Summary:
    level: str  # minute | hour | day
    start: datetime
    end: datetime
    text: str
    frame_ids: list[int] = field(default_factory=list)  # frames cited in the text
    frames: int = 0
    alerts: int = 0

    @property
    def quiet(self) -> bool:
        return not self.frame_ids


@dataclass
class _Window:
    start: datetime
    end: datetime
    frames: int = 0
    items: list[tuple[datetime, str, int, int]] = field(default_factory=list)  # (time, text, frame id, severity)
    alerts: int = 0
    children: list[Summary] = field(default_factory=list)


# summarizer(level, start, end, children_or_items_text) -> text; must keep [fN] citations
Summarizer = Callable[[str, datetime, datetime, list[str]], str]


def gemini_summarizer(client) -> Summarizer:
    """Hour/day summaries written by Gemini from the finer summaries (citations must be kept)."""

    def summarize(level: str, start: datetime, end: datetime, children: list[str]) -> str:
        span = f"{start:%Y-%m-%d}" if level == "day" else f"{start:%H:%M}-{end:%H:%M}"
        prompt = (f"Summarise these security-drone notes for the {level} {span} in at most 3 sentences for an operator. "
                  "Use only these facts, lead with the most serious alert, and keep the [fN] frame citation next to "
                  "every fact you mention.\n\n" + "\n".join(children))
        return f"{span}: " + client.text(prompt, purpose=f"summary_{level}")
    return summarize


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English text (used when no tokenizer is available)."""
    return (len(text) + 3) // 4


class HierarchicalMemory:
    def __init__(self, config: PropertyConfig, *, index=None, minute_span: int = 10, recent_frames: int = 5,
                 summarizer: Optional[Summarizer] = None, max_items_per_summary: int = 8):
        self.config = config
        self.index = index
        self.span = timedelta(minutes=minute_span)
        self.summarizer = summarizer
        self.max_items = max_items_per_summary
        self.recent: deque[FrameResult] = deque(maxlen=recent_frames)
        self.minutes: list[Summary] = []
        self.hours: list[Summary] = []
        self.days: list[Summary] = []
        self.frames = 0
        self.first_ts: Optional[datetime] = None
        self.last_ts: Optional[datetime] = None
        self._minute: Optional[_Window] = None
        self._hour: Optional[_Window] = None
        self._day: Optional[_Window] = None

    # ------------------------------------------------------------------ ingest
    def observe(self, result: FrameResult) -> None:
        at = result.frame.timestamp
        self._roll(at)
        window = self._minute
        window.frames += 1
        for log in result.logs:
            window.items.append((log.timestamp, log.message.rstrip("."), log.frame_id, 0))
        for alert in result.alerts:
            window.alerts += 1
            window.items.append((alert.timestamp, f"ALERT {alert.severity.name} {alert.rule_id}: {alert.message.rstrip('.')}",
                                 alert.frame_id, int(alert.severity)))
        self.recent.append(result)
        self.frames += 1
        self.first_ts = self.first_ts or at
        self.last_ts = at

    def close(self) -> None:
        """Flush the open windows (end of footage); safe to call more than once."""
        self._close_minute()
        self._close_hour()
        self._close_day()

    def _roll(self, at: datetime) -> None:
        if self._minute and at >= self._minute.end:
            self._close_minute()
        if self._hour and at >= self._hour.end:
            self._close_hour()
        if self._day and at >= self._day.end:
            self._close_day()
        if self._day is None:
            start = at.replace(hour=0, minute=0, second=0, microsecond=0)
            self._day = _Window(start, start + timedelta(days=1))
        if self._hour is None:
            start = at.replace(minute=0, second=0, microsecond=0)
            self._hour = _Window(start, start + timedelta(hours=1))
        if self._minute is None:
            start = self._hour.start
            while start + self.span <= at:
                start += self.span
            self._minute = _Window(start, min(start + self.span, self._hour.end))

    # ------------------------------------------------------------------ summarising
    def _close_minute(self) -> None:
        window, self._minute = self._minute, None
        if window is None or not window.frames:
            return
        summary = self._summarize_items("minute", window)
        self.minutes.append(summary)
        self._hour.children.append(summary)
        self._store(summary)

    def _close_hour(self) -> None:
        self._close_minute()
        window, self._hour = self._hour, None
        if window is None or not window.children:
            return
        summary = self._summarize_children("hour", window)
        self.hours.append(summary)
        self._day.children.append(summary)
        self._store(summary)

    def _close_day(self) -> None:
        self._close_hour()
        window, self._day = self._day, None
        if window is None or not window.children:
            return
        summary = self._summarize_children("day", window)
        self.days.append(summary)
        self._store(summary)

    def _summarize_items(self, level: str, w: _Window) -> Summary:
        span = f"{w.start:%H:%M}-{w.end:%H:%M}"
        if not w.items:
            return Summary(level, w.start, w.end, f"{span} quiet ({w.frames} frames).", [], w.frames, 0)
        items = _top_items(w.items, self.max_items)
        text = f"{span} ({w.frames} frames): " + "; ".join(f"{t:%H:%M} {msg} [f{fid}]" for t, msg, fid, _ in items)
        if len(w.items) > len(items):
            text += f"; +{len(w.items) - len(items)} more"
        return Summary(level, w.start, w.end, text + ".", sorted({fid for _, _, fid, _ in items}), w.frames, w.alerts)

    def _summarize_children(self, level: str, w: _Window) -> Summary:
        frames = sum(c.frames for c in w.children)
        alerts = sum(c.alerts for c in w.children)
        cited = sorted({fid for c in w.children for fid in c.frame_ids})
        span = f"{w.start:%Y-%m-%d}" if level == "day" else f"{w.start:%H:%M}-{w.end:%H:%M}"
        busy = [c for c in w.children if not c.quiet]
        if not busy:
            return Summary(level, w.start, w.end, f"{span} quiet ({frames} frames, no events).", [], frames, 0)

        if self.summarizer is not None:
            try:
                text = self.summarizer(level, w.start, w.end, [c.text for c in busy])
                return Summary(level, w.start, w.end, text, cited, frames, alerts)
            except Exception:  # an LLM summarizer failing must never stall the feed
                pass
        if level == "hour":
            body = " | ".join(c.text.split(": ", 1)[1].rstrip(".") for c in busy)
            return Summary(level, w.start, w.end, f"{span} ({frames} frames, {alerts} alerts): {body}.", cited, frames, alerts)
        quiet_hours = [f"{c.start:%H}" for c in w.children if c.quiet]
        lines = [f"{span}: {frames} frames, {alerts} alerts."]
        lines += [c.text for c in busy]
        if quiet_hours:
            lines.append(f"Quiet hours: {', '.join(quiet_hours)}.")
        return Summary(level, w.start, w.end, "\n".join(lines), cited, frames, alerts)

    def _store(self, summary: Summary) -> None:
        if self.index is not None:
            self.index.add_summary(summary.level, to_ts(summary.start), to_ts(summary.end), summary.text, summary.frame_ids)

    # ------------------------------------------------------------------ rendering
    def render(self, budget_tokens: int = 2500) -> str:
        """Prompt context: property, coverage, days, hours, rolling window, latest frames."""
        rolling_from = (self.last_ts - timedelta(hours=1)) if self.last_ts else None
        current_day = self.last_ts.date() if self.last_ts else None
        return render_sections(
            self.config,
            coverage=f"{self.frames} frames from {self.first_ts:%Y-%m-%d %H:%M} to {self.last_ts:%Y-%m-%d %H:%M}."
            if self.first_ts else "No footage yet.",
            days=[d.text for d in self.days if d.start.date() != current_day],
            hours=[h.text for h in self.hours if h.start.date() == current_day and not h.quiet],
            quiet_hours=[f"{h.start:%H}" for h in self.hours if h.start.date() == current_day and h.quiet],
            rolling=[m.text for m in self.minutes if rolling_from and m.end > rolling_from and not m.quiet],
            latest=[f"[f{r.frame.frame_id}] {r.frame.timestamp:%H:%M} {self.config.zone_name(r.frame.zone)}: "
                    f"{r.frame.description}" for r in self.recent],
            budget_tokens=budget_tokens,
        )


def render_from_index(index, config: PropertyConfig, budget_tokens: int = 2500) -> str:
    """Same prompt context, rebuilt from the stored summaries (e.g. for `ask` after the run)."""
    stats = index.stats()
    if not stats["frames"]:
        return render_sections(config, coverage="No footage yet.", days=[], hours=[], quiet_hours=[], rolling=[],
                               latest=[], budget_tokens=budget_tokens)
    last_day = stats["last_ts"][:10]
    hours = [s for s in index.summaries("hour") if s["period_start"][:10] == last_day]
    last_ts = datetime.strptime(stats["last_ts"], "%Y-%m-%d %H:%M:%S")
    rolling_from = to_ts(last_ts - timedelta(hours=1))
    latest = index.query_frames(start=to_ts(last_ts - timedelta(minutes=30)), limit=1000)[-5:]
    return render_sections(
        config,
        coverage=f"{stats['frames']} frames from {stats['first_ts'][:16]} to {stats['last_ts'][:16]}.",
        days=[s["text"] for s in index.summaries("day") if s["period_start"][:10] != last_day],
        hours=[s["text"] for s in hours if s["frame_ids"]],
        quiet_hours=[s["period_start"][11:13] for s in hours if not s["frame_ids"]],
        rolling=[s["text"] for s in index.summaries("minute") if s["period_end"] > rolling_from and s["frame_ids"]],
        latest=[f"[f{f['frame_id']}] {f['ts'][11:16]} {config.zone_name(f['zone'])}: {f['description']}" for f in latest],
        budget_tokens=budget_tokens,
    )


def render_sections(config: PropertyConfig, *, coverage: str, days: list[str], hours: list[str],
                    quiet_hours: list[str], rolling: list[str], latest: list[str], budget_tokens: int) -> str:
    """Join sections in priority order; once the budget is spent, sections keep a pointer line."""
    hour_lines = list(hours)
    if quiet_hours:
        hour_lines.append(f"Quiet hours (no events): {', '.join(quiet_hours)}.")
    sections = [
        ("Property", property_lines(config)),
        ("Coverage", [coverage]),
        ("Today, hour by hour", hour_lines),
        ("Last hour (rolling window, 10-minute summaries)", rolling),
        ("Latest frames", latest),
        ("Earlier days (newest first)", list(reversed(days))),
    ]
    budget = budget_tokens * 4  # characters
    out: list[str] = []
    used = 0
    for title, lines in sections:
        if not lines:
            continue
        kept: list[str] = []
        for line in lines:
            if kept and used + len(line) + 1 > budget:
                kept.append(f"... ({len(lines) - len(kept)} more; use summarize_window / query_frames)")
                break
            kept.append(line)
            used += len(line) + 1
        out.append(f"## {title}\n" + "\n".join(kept))
    return "\n\n".join(out)


def property_lines(cfg: PropertyConfig) -> list[str]:
    return [
        f"{cfg.property_name}; drone {cfg.drone_id}; night hours {cfg.night.start:%H:%M}-{cfg.night.end:%H:%M}; "
        f"business hours {cfg.business.start:%H:%M}-{cfg.business.end:%H:%M}.",
        "Zones: " + ", ".join(f"{z.id} ({z.name}{', perimeter' if z.perimeter else ''}{', restricted' if z.restricted else ''})"
                              for z in cfg.zones),
    ]


def render_naive(index, config: PropertyConfig) -> str:
    """Baseline for the context experiment: every frame and every alert, verbatim."""
    lines = property_lines(config) + [""]
    for f in index.query_frames(limit=10_000_000):
        tele = (f" | alt {f['altitude_m']:.0f} m, battery {f['battery_pct']:.0f}%, wind {f['wind_mps']:.1f} m/s, "
                f"hdop {f['gps_hdop']:.1f}, {f['distance_from_home_m']:.0f} m from dock" if f["battery_pct"] is not None else "")
        lines.append(f"[f{f['frame_id']}] {f['ts']} {f['zone']}: {f['description']}{tele}")
    lines.append("")
    lines += [f"ALERT [f{a['frame_id']}] {a['ts']} {a['severity_name']} {a['rule_id']}: {a['message']}" for a in index.alerts(limit=100_000)]
    return "\n".join(lines)


def _top_items(items: list, limit: int) -> list:
    """Keep the most severe items (alerts first), then restore time order."""
    if len(items) <= limit:
        return items
    ranked = sorted(items, key=lambda it: (-it[3], it[0]))[:limit]
    return sorted(ranked, key=lambda it: it[0])


__all__ = ["HierarchicalMemory", "Summary", "gemini_summarizer", "render_from_index", "render_naive", "render_sections", "estimate_tokens",
           "property_lines"]
