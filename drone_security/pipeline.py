"""The per-frame processing pipeline.

    frame (caption + telemetry)
      -> analyzer      : caption -> structured detections
      -> tracker       : detections -> persistent entities, visits, dwell time
      -> event logger  : human-readable logs for notable changes
      -> rule engine   : alerts (with cooldown + correlation)
      -> frame index   : SQLite/FTS5, queryable by time / object / text
      -> context       : rolling working memory for the analyst agent
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Iterable, Optional

from .config import PropertyConfig
from .context import ContextManager
from .indexing import FrameIndex
from .models import Detection, EventLog, Frame, FrameResult
from .perception import RuleBasedAnalyzer
from .rules import RuleContext, RuleEngine
from .tracking import EntityTracker, TrackUpdate


@dataclass
class RunStats:
    frames: int = 0
    detections: int = 0
    logs: int = 0
    alerts: int = 0
    suppressed_alerts: int = 0
    elapsed_s: float = 0.0
    alerts_by_severity: dict[str, int] = field(default_factory=dict)

    @property
    def fps(self) -> float:
        return self.frames / self.elapsed_s if self.elapsed_s else 0.0


class SecurityPipeline:
    def __init__(
        self,
        config: PropertyConfig,
        *,
        analyzer=None,
        index: Optional[FrameIndex] = None,
        tracker: Optional[EntityTracker] = None,
        rules: Optional[RuleEngine] = None,
        context: Optional[ContextManager] = None,
    ):
        self.config = config
        self.analyzer = analyzer or RuleBasedAnalyzer()
        self.index = index or FrameIndex()
        self.tracker = tracker or EntityTracker(timedelta(minutes=config.thresholds.visit_gap_minutes))
        self.rules = rules or RuleEngine(config)
        self.context = context or ContextManager(config, self.tracker, index=self.index)
        self._last_action: dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------ per frame
    def process(self, frame: Frame) -> FrameResult:
        detections = self.analyzer.analyze(frame)
        observations = [(det, self.tracker.observe(det, frame.timestamp)) for det in detections]
        logs = self._log(frame, observations)
        alerts = self.rules.evaluate(RuleContext(frame, observations, self.config, self.tracker))

        result = FrameResult(frame, detections, logs, alerts)
        self.index.add(result)
        self.index.upsert_entities({id(u.entity): u.entity for _, u in observations}.values())
        self.context.observe(result)
        return result

    def run(self, frames: Iterable[Frame], on_result: Optional[Callable[[FrameResult], None]] = None) -> RunStats:
        stats = RunStats()
        started = time.perf_counter()
        for frame in frames:
            result = self.process(frame)
            stats.frames += 1
            stats.detections += len(result.detections)
            stats.logs += len(result.logs)
            stats.alerts += len(result.alerts)
            for alert in result.alerts:
                stats.alerts_by_severity[alert.severity.name] = stats.alerts_by_severity.get(alert.severity.name, 0) + 1
            if on_result:
                on_result(result)
        self.context.close()
        self.index.flush()
        stats.elapsed_s = time.perf_counter() - started
        stats.suppressed_alerts = self.rules.suppressed
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
