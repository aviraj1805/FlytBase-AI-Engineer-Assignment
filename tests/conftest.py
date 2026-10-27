from __future__ import annotations

import itertools
from datetime import date, datetime

import pytest

from drone_security.config import PROJECT_ROOT, PropertyConfig
from drone_security.models import Frame
from drone_security.pipeline import SecurityPipeline
from drone_security.simulator import TelemetrySynthesizer, load_scenario

DEMO_DAY = date(2026, 10, 27)
DEMO_SCENARIO = PROJECT_ROOT / "data" / "scenarios" / "demo_day.json"


@pytest.fixture(scope="session")
def config() -> PropertyConfig:
    return PropertyConfig.load()


@pytest.fixture
def pipeline(config) -> SecurityPipeline:
    return SecurityPipeline(config)


@pytest.fixture
def make_frame(config):
    """make_frame("00:01", "Person loitering near main gate", zone="main_gate", battery_pct=20)"""
    synth = TelemetrySynthesizer(config, seed=3)
    ids = itertools.count(1)

    def _make(hhmm: str, description: str, zone: str = "main_gate", day: date = DEMO_DAY, **telemetry) -> Frame:
        at = datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time())
        return Frame(frame_id=next(ids), timestamp=at, description=description,
                     telemetry=synth.next(at, zone, telemetry))

    return _make


@pytest.fixture(scope="module")
def demo_run():
    """The full demo day processed once per test module: (pipeline, results, stats)."""
    config = PropertyConfig.load()
    pipeline = SecurityPipeline(config)
    results = []
    stats = pipeline.run(load_scenario(DEMO_SCENARIO, config), on_result=results.append)
    return pipeline, results, stats
