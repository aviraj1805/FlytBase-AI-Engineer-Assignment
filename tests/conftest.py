from __future__ import annotations

import itertools
from datetime import date, datetime

import pytest

from drone_security.config import PropertyConfig
from drone_security.ingest import parse_record
from drone_security.models import Frame
from drone_security.pipeline import SecurityPipeline
from drone_security.scenario import generate_day, load_script

DEMO_DAY = date(2026, 10, 27)


@pytest.fixture(scope="session")
def config() -> PropertyConfig:
    return PropertyConfig.load()


@pytest.fixture
def pipeline(config) -> SecurityPipeline:
    return SecurityPipeline(config)


@pytest.fixture
def make_frame(config):
    """make_frame("00:01", "Person loitering near main gate", zone="main_gate", battery_pct=20).

    Builds a raw record the way the drone sends it (GPS at the zone centre) and parses it,
    so tests exercise the same ingest path as real feeds. telemetry=None drops the packet.
    """
    ids = itertools.count(1)

    def _make(hhmm: str, description: str, zone: str = "main_gate", day: date = DEMO_DAY, telemetry="default",
              **overrides) -> Frame:
        fmt = "%H:%M:%S" if hhmm.count(":") == 2 else "%H:%M"
        at = datetime.combine(day, datetime.strptime(hhmm, fmt).time())
        record = {"frame_id": next(ids), "timestamp": at.strftime("%Y-%m-%d %H:%M:%S"), "description": description}
        if telemetry is not None:
            z = config.zone(zone)
            record["telemetry"] = {"lat": z.lat, "lon": z.lon, "altitude_m": 30, "battery_pct": 80, "wind_mps": 3,
                                   "gps_hdop": 0.8, "mode": "patrol", **overrides}
        return parse_record(record, config)

    return _make


@pytest.fixture(scope="module")
def scenario():
    """The seed-7 scenario day processed once per test module: (day, pipeline, results)."""
    config = PropertyConfig.load()
    day = generate_day(load_script(), config, seed=7)
    pipeline = SecurityPipeline(config)
    results = []
    pipeline.run(day.records, on_result=results.append)
    return day, pipeline, results
