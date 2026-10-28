"""The per-frame processing pipeline.

    raw records
      -> sanitizer     : validate, drop duplicates, re-order (ingest.py)
      -> analyzer      : caption -> structured detections (perception/)
      -> tracker       : detections -> re-identified entities, visits, dwell time
      -> event logger  : human-readable logs for notable changes
      -> rule engine   : alerts from config/rules.yaml (cooldown + correlation)
      -> frame index   : SQLite + FTS5, queryable by time / object / zone / entity / text
      -> memory        : hierarchical summaries (minute -> hour -> day) for the agent
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from .config import PropertyConfig
from .indexing import FrameIndex
from .ingest import FeedSanitizer, IngestStats
from .memory import HierarchicalMemory
from .models import Detection, EventLog, Frame, FrameResult
from .perception import FrameAnalyzer, RuleBasedAnalyzer
from .rules import RuleContext, RuleEngine
from .tracking import EntityTracker, TrackUpdate


@dataclass
class RunStats:
    frames: int = 0
    detections: int = 0
    logs: int = 0
    alerts: int = 0
    late_frames: int = 0
    raw_rule_firings: int = 0  # before cooldown and correlation
    suppressed_alerts: int = 0
    elapsed_s: float = 0.0
    alerts_by_severity: dict[str, int] = field(default_factory=dict)
    ingest: Optional[IngestStats] = None
    frame_ms: list[float] = field(default_factory=list, repr=False)  # time from frame in to alerts out

    @property
    def fps(self) -> float:
        return self.frames / self.elapsed_s if self.elapsed_s else 0.0

    def latency_ms(self, percentile: float) -> float:
        """Per-frame processing latency at a percentile (50 = median, 99 = worst 1%)."""
        if not self.frame_ms:
            return 0.0
        ordered = sorted(self.frame_ms)
        return ordered[min(int(len(ordered) * percentile / 100), len(ordered) - 1)]


class SecurityPipeline:
    def __init__(
        self,
        config: PropertyConfig,
        *,
        analyzer: Optional[FrameAnalyzer] = None,
        index: Optional[FrameIndex] = None,
        tracker: Optional[EntityTracker] = None,
        rules: Optional[RuleEngine] = None,
        memory: Optional[HierarchicalMemory] = None,
    ):
        tracking = config.tracking
        self.config = config
        self.analyzer = analyzer or RuleBasedAnalyzer()
        self.index = index or FrameIndex()
        self.tracker = tracker or EntityTracker(
            visit_gap=timedelta(minutes=tracking.visit_gap_minutes),
            max_open_gap=timedelta(hours=tracking.max_open_visit_hours),
            reid_horizon=timedelta(days=tracking.reid_horizon_days),
        )
        self.rules = rules or RuleEngine(config)
        self.memory = memory or HierarchicalMemory(config, index=self.index)
        self.watermark: Optional[datetime] = None  # newest timestamp processed so far
        self.late_frames = 0
        self._prev_telemetry_frame: Optional[Frame] = None
        self._last_action: dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------ per frame
    def process(self, frame: Frame) -> FrameResult:
        detections = self.analyzer.analyze(frame)
        if self.watermark and frame.timestamp < self.watermark:
            # Arrived after newer frames were processed: the tracker and dwell timers cannot go
            # back in time, so the frame is indexed (searchable) but raises no alerts.
            self.late_frames += 1
            result = FrameResult(frame, detections, [], [])
            self.index.add(result, late=True)
            return result

        observations = [(det, self.tracker.observe(det, frame.timestamp, frame.frame_id)) for det in detections]
        logs = self._log(frame, observations)
        alerts = self.rules.evaluate(RuleContext(frame, observations, self.config, self._prev_telemetry_frame))
        if frame.telemetry is not None:
            self._prev_telemetry_frame = frame
        self.watermark = frame.timestamp

        result = FrameResult(frame, detections, logs, alerts)
        self.index.add(result)
        self.index.upsert_entities({u.entity.entity_id: u.entity for _, u in observations}.values())
        self.memory.observe(result)
        return result

    def run(self, records: Iterable, on_result: Optional[Callable[[FrameResult], None]] = None,
            reorder_window: int = 8) -> RunStats:
        """Process raw records (dicts) or Frames end to end, through the sanitizer."""
        stats = RunStats()
        sanitizer = FeedSanitizer(self.config, reorder_window=reorder_window)
        started = time.perf_counter()
        for frame in sanitizer.process(records):
            frame_started = time.perf_counter()
            result = self.process(frame)
            stats.frame_ms.append((time.perf_counter() - frame_started) * 1000)
            stats.frames += 1
            stats.detections += len(result.detections)
            stats.logs += len(result.logs)
            stats.alerts += len(result.alerts)
            for alert in result.alerts:
                stats.alerts_by_severity[alert.severity.name] = stats.alerts_by_severity.get(alert.severity.name, 0) + 1
            if on_result:
                on_result(result)
        self.memory.close()
        self.index.flush()
        stats.elapsed_s = time.perf_counter() - started
        stats.late_frames = self.late_frames
        stats.raw_rule_firings = self.rules.raised_before_noise_control
        stats.suppressed_alerts = self.rules.suppressed
        stats.ingest = sanitizer.stats
        return stats

    # ------------------------------------------------------------------ event logs
    def _log(self, frame: Frame, observations: list[tuple[Detection, TrackUpdate]]) -> list[EventLog]:
        """Log what changed, not every frame: a parked truck seen 20 times is one log line."""
        logs = []
        hhmm = frame.timestamp.strftime("%H:%M")
        for det, upd in observations:
            entity_id = upd.entity.entity_id
            action_changed = det.action and det.action != self._last_action.get(entity_id)
            self._last_action[entity_id] = det.action
            if upd.is_new_visit:
                kind = "visit"
            elif upd.zone_changed or (action_changed and det.action in _NOTABLE_ACTIONS):
                kind = "sighting"
            else:
                continue
            message = _describe(det, self.config.zone_name(det.zone), hhmm)
            if det.category == "vehicle":
                owner = self.config.is_known_vehicle(det.color, det.make, det.model, det.label)
                if owner:
                    message = message[:-1] + f" (known: {owner})."
            logs.append(EventLog(frame.timestamp, frame.frame_id, message, entity_id=entity_id, zone=det.zone, kind=kind))
        return logs


_NOTABLE_ACTIONS = {"entering", "exiting", "loitering", "climbing", "tampering", "peering", "running", "delivering"}


def _describe(det: Detection, zone_name: str, hhmm: str) -> str:
    name = det.display_name
    if det.category == "vehicle":
        if det.action == "entering":
            return f"{name} entered at {zone_name}, {hhmm}."
        if det.action == "exiting":
            return f"{name} leaving {zone_name}, {hhmm}."
        return f"{name} spotted at {zone_name}, {hhmm}."
    if det.category == "infrastructure":
        return f"{det.label.capitalize()} open at {zone_name}, {hhmm}."
    if det.category == "person" and det.action not in (None, "standing"):
        return f"{name} {det.action} at {zone_name}, {hhmm}."
    return f"{name} spotted at {zone_name}, {hhmm}."


__all__ = ["SecurityPipeline", "RunStats"]
