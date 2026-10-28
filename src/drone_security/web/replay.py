"""Live replay of the scenario day as Server-Sent Events.

Each connection gets a fresh SecurityPipeline (in-memory index) and feeds it the day's frames
one by one, so every alert on the Mission Control and Live Alerts screens is raised by the
rule engine at that moment, not read from a stored list.

Seeking to position N re-processes frames 0..N-1 without delays first (the whole day takes
well under a second), because the tracker and rule cooldowns depend on everything before N.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Callable

from ..indexing import FrameIndex
from ..pipeline import SecurityPipeline
from .state import AppState, alert_dict, detection_dict, frame_dict, log_dict

MAX_GAP_S = 2.0  # never wait longer than this between two frames (the drone docks for hours)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def frame_payload(state: AppState, position: int, result) -> dict:
    frame = result.frame
    return {
        "position": position,
        "frame": frame_dict(frame),
        "detections": [detection_dict(d) for d in result.detections],
        "logs": [log_dict(e) for e in result.logs],
        "alerts": [alert_dict(a) for a in result.alerts],
    }


async def replay_stream(state: AppState, start: int, speed: float,
                        is_disconnected: Callable[[], "asyncio.Future[bool]"]) -> AsyncIterator[str]:
    start = max(0, min(start, len(state.frames) - 1))
    speed = max(1.0, speed)
    pipeline = SecurityPipeline(state.config, index=FrameIndex(":memory:"))

    # catch up to the start position (no delays), keeping what the operator would already have seen
    alerts, logs, last = [], [], None
    for position in range(start):
        result = pipeline.process(state.frames[position])
        alerts += [alert_dict(a) for a in result.alerts]
        logs += [log_dict(e) for e in result.logs]
        last = frame_payload(state, position, result)
    yield sse("snapshot", {"position": start, "alerts": alerts, "logs": logs[-200:], "last": last,
                           "raw_rule_firings": pipeline.rules.raised_before_noise_control})

    for position in range(start, len(state.frames)):
        if await is_disconnected():
            return
        frame = state.frames[position]
        result = pipeline.process(frame)
        payload = frame_payload(state, position, result)
        payload["raw_rule_firings"] = pipeline.rules.raised_before_noise_control
        yield sse("frame", payload)
        if position + 1 < len(state.frames):
            gap = (state.frames[position + 1].timestamp - frame.timestamp).total_seconds()
            await asyncio.sleep(min(max(gap, 0) / speed, MAX_GAP_S))
    yield sse("end", {"position": len(state.frames) - 1})


__all__ = ["replay_stream", "sse"]
