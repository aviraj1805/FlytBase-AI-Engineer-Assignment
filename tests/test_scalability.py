"""Scalability smoke test: thousands of frames ingest fast and queries stay in milliseconds.

Thresholds are deliberately loose (10x headroom over a laptop) so CI machines pass;
`python -m drone_security benchmark --frames 100000` gives real numbers.
"""
import time

from drone_security.indexing import FrameIndex
from drone_security.pipeline import SecurityPipeline
from drone_security.simulator import random_frames

N_FRAMES = 5000


def test_ingest_and_query_5000_frames(config, tmp_path):
    index = FrameIndex(tmp_path / "bench.db", batch_size=500)
    pipeline = SecurityPipeline(config, index=index)
    stats = pipeline.run(random_frames(N_FRAMES, config, seed=42))

    assert stats.frames == N_FRAMES
    assert stats.fps > 300, f"ingest too slow: {stats.fps:.0f} frames/s"

    for query in (
        lambda: index.query_detections(label="truck", limit=200),
        lambda: index.query_detections(category="person", zone="main_gate", start="00:00", end="03:00"),
        lambda: index.search("masked fence"),
        lambda: index.alerts(min_severity="HIGH"),
        lambda: index.telemetry_near("02:00"),
    ):
        started = time.perf_counter()
        assert query() is not None
        assert (time.perf_counter() - started) < 0.25


def test_alert_noise_is_bounded_under_load(config):
    """Random load repeats the same events constantly; cooldown must absorb most repeats."""
    pipeline = SecurityPipeline(config)
    stats = pipeline.run(random_frames(2000, config, seed=7))
    assert stats.suppressed_alerts > stats.alerts * 0.5
