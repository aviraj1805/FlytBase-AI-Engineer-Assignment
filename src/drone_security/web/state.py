"""Shared state for the web UI: the processed day, the frame index and the analyst agent.

At start-up the server processes the scenario day once through the normal pipeline into
data/runs/ui.db, exactly like `python -m drone_security run`. Every screen except the live
replay reads that index. The replay (replay.py) runs its own pipeline per connection.
"""
from __future__ import annotations

import argparse
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from ..config import PROJECT_ROOT, PropertyConfig
from ..gemini import gemini_available
from ..indexing import FrameIndex
from ..ingest import FeedSanitizer, read_records
from ..models import Alert, Detection, EventLog, Frame
from ..pipeline import RunStats, SecurityPipeline
from ..scenario import DEFAULT_DAY_DIR

log = logging.getLogger(__name__)

DEFAULT_UI_DB = PROJECT_ROOT / "data" / "runs" / "ui.db"
DEFAULT_FEED = DEFAULT_DAY_DIR / "frames.jsonl"


class AppState:
    def __init__(self, *, db_path: Path = DEFAULT_UI_DB, feed: Path = DEFAULT_FEED, use_llm: Optional[bool] = None):
        self.config = PropertyConfig.load()
        self.feed = Path(feed)
        if not self.feed.exists():  # fresh clone without the generated day: write it from the seeded script
            from ..cli import cmd_generate

            cmd_generate(argparse.Namespace(config=None, script=None, seed=7, out=self.feed.parent))
        self.records = read_records(self.feed)
        # the sanitized frames, in processing order: the replay timeline and the input of every replay pipeline
        self.frames: list[Frame] = list(FeedSanitizer(self.config).process(self.records))
        self.position_of = {f.frame_id: i for i, f in enumerate(self.frames)}

        self.db_path = Path(db_path)
        for suffix in ("", "-wal", "-shm"):  # rebuild from scratch each start, like `run`
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)
        self.index = FrameIndex(self.db_path)
        self.pipeline = SecurityPipeline(self.config, index=self.index)
        self.run_stats: RunStats = self.pipeline.run(self.records)

        self.use_llm = use_llm if use_llm is not None else gemini_available()
        self._analyst = None
        self._analyst_lock = threading.Lock()
        self._gemini_status: Optional[dict] = None
        self.tests_lock = threading.Lock()
        self.last_test_run: Optional[dict] = None

    # ------------------------------------------------------------------ Gemini / agent
    def gemini_status(self) -> dict:
        """Which mode the UI badge shows. Listing models is a network call, so it is done once and cached."""
        if self._gemini_status is None:
            if not self.use_llm:
                self._gemini_status = {"mode": "offline", "model": None, "reason": "no GEMINI_API_KEY (or DSA_OFFLINE=1)"}
            else:
                try:
                    from ..gemini import GeminiClient

                    self._gemini_status = {"mode": "gemini", "model": GeminiClient().model, "reason": None}
                except Exception as exc:  # bad key, no network: stay useful offline and say why
                    self._gemini_status = {"mode": "offline", "model": None,
                                           "reason": f"Gemini unavailable ({type(exc).__name__})"}
                    self.use_llm = False
        return self._gemini_status

    def analyst(self):
        from ..agent import SecurityAnalyst

        with self._analyst_lock:
            if self._analyst is None:
                model = self.gemini_status()["model"]
                self._analyst = SecurityAnalyst(self.index, self.config, use_llm=self.use_llm, model=model)
            return self._analyst

    def reset_analyst(self) -> None:
        with self._analyst_lock:
            self._analyst = None

    # ------------------------------------------------------------------ SQL capture
    @contextmanager
    def capture_sql(self) -> Iterator[list[str]]:
        """Record every SQL statement the index runs inside the block (shown in the Frame Index Explorer).

        Holding the index lock for the whole block keeps other requests' SQL out of the capture."""
        statements: list[str] = []
        with self.index.lock:
            self.index.conn.set_trace_callback(statements.append)
            try:
                yield statements
            finally:
                self.index.conn.set_trace_callback(None)


# ---------------------------------------------------------------------- serialisers
def frame_dict(frame: Frame) -> dict[str, Any]:
    t = frame.telemetry
    out = {"frame_id": frame.frame_id, "ts": frame.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
           "description": frame.description, "zone": frame.zone, "source": frame.source, "telemetry": None}
    if t:
        out["telemetry"] = {"lat": t.lat, "lon": t.lon, "altitude_m": t.altitude_m, "battery_pct": t.battery_pct,
                            "heading_deg": t.heading_deg, "speed_mps": t.speed_mps, "wind_mps": t.wind_mps,
                            "gps_hdop": t.gps_hdop, "mode": t.mode, "zone": t.zone,
                            "distance_from_home_m": round(t.distance_from_home_m, 1)}
    return out


def detection_dict(d: Detection) -> dict[str, Any]:
    return {**d.to_dict(), "display_name": d.display_name}


def alert_dict(a: Alert) -> dict[str, Any]:
    return {"ts": a.timestamp.strftime("%Y-%m-%d %H:%M:%S"), "frame_id": a.frame_id, "rule_id": a.rule_id,
            "severity": int(a.severity), "severity_name": a.severity.name, "message": a.message,
            "explanation": a.explanation, "evidence_frame_ids": list(a.evidence_frame_ids), "zone": a.zone,
            "entity_id": a.entity_id, "recommended_action": a.recommended_action, "related_rules": list(a.related_rules)}


def log_dict(e: EventLog) -> dict[str, Any]:
    return {"ts": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"), "frame_id": e.frame_id, "message": e.message,
            "entity_id": e.entity_id, "zone": e.zone, "kind": e.kind}


__all__ = ["AppState", "frame_dict", "detection_dict", "alert_dict", "log_dict", "DEFAULT_UI_DB"]
