"""Seeded scenario generator: day script (YAML) -> frame records + separate ground truth.

The script (data/scenarios/day_script.yaml) describes one day as data: patrol route,
dock windows, scenes (things that stay put), scripted episodes and the alert incidents
a correct system must raise. This module turns it into

* frame records exactly as the drone would send them (time, caption, telemetry), and
* ground truth: the true identity behind every object in every frame, plus the
  expected alerts with absolute time windows.

Everything random (which paraphrase, GPS jitter, wind) comes from one seeded RNG,
so the same (script, seed) always produces byte-identical output.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

import yaml

from .config import PROJECT_ROOT, PropertyConfig
from .timeutil import TS_FORMAT

DEFAULT_SCRIPT = PROJECT_ROOT / "data" / "scenarios" / "day_script.yaml"
DEFAULT_DAY_DIR = PROJECT_ROOT / "data" / "scenarios" / "generated_day"
METERS_PER_DEG_LAT = 111_320.0


@dataclass
class GeneratedDay:
    records: list[dict]  # what the drone sends; no labels
    ground_truth: dict


@dataclass
class _Slot:
    """One frame to emit, before telemetry is attached."""

    at: datetime
    zone: str
    caption: str
    objects: list[dict]
    overrides: dict
    kind: str  # patrol | beat | dock


def load_script(path: str | Path = DEFAULT_SCRIPT) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def generate_day(script: dict, config: PropertyConfig, *, seed: int = 7, day_offset: int = 0,
                 first_frame_id: int = 1, cadence_minutes: Optional[float] = None) -> GeneratedDay:
    """Build one day. `day_offset` shifts the date (multi-day benchmarks reuse the same script)."""
    rng = random.Random(f"{seed}-{day_offset}")  # str seeds are hashed deterministically by Random
    day = date.fromisoformat(script["date"]) + timedelta(days=day_offset)
    patrol = script["patrol"]
    cadence = timedelta(minutes=cadence_minutes or patrol["cadence_minutes"])
    docks = [(_at(day, a), _at(day, b)) for a, b in patrol["dock_windows"]]

    beats = [
        _Slot(_at(day, b["at"]), b["zone"], rng.choice(b["captions"]), b.get("objects", []), b.get("telemetry", {}), "beat")
        for episode in script["episodes"] for b in episode["beats"]
    ]
    slots = beats + _dock_slots(docks) + _patrol_slots(script, day, cadence, docks, beats, rng)
    slots.sort(key=lambda s: (s.at, s.kind != "beat"))

    records, labels = [], {}
    telemetry = _TelemetryModel(config, patrol, docks, rng)
    for frame_id, slot in enumerate(slots, start=first_frame_id):
        records.append({
            "frame_id": frame_id,
            "timestamp": slot.at.strftime(TS_FORMAT),
            "description": slot.caption,
            "telemetry": telemetry.at(slot),
        })
        labels[str(frame_id)] = [_label(obj, slot.zone, script["entities"]) for obj in slot.objects]

    truth = {
        "date": day.isoformat(),
        "seed": seed,
        "frames": len(records),
        "entities": script["entities"],
        "frame_labels": labels,
        "expected_alerts": [_absolute(a, day) for a in script["expected_alerts"]],
        "benign": script.get("benign", []),
    }
    return GeneratedDay(records, truth)


def generate_frames(n_frames: int, config: PropertyConfig, *, script: Optional[dict] = None, seed: int = 7,
                    cadence_minutes: Optional[float] = None) -> Iterator[dict]:
    """Stream at least `n_frames` records by repeating the scripted day on consecutive dates.

    Used by the scalability benchmark: same property, same regular visitors, fresh
    paraphrases and jitter every day (the seed changes with the day offset).
    """
    script = script or load_script()
    emitted, day_offset = 0, 0
    while emitted < n_frames:
        day = generate_day(script, config, seed=seed, day_offset=day_offset, first_frame_id=emitted + 1,
                           cadence_minutes=cadence_minutes)
        for record in day.records[: n_frames - emitted]:
            yield record
        emitted += min(len(day.records), n_frames - emitted)
        day_offset += 1


def write_day(day: GeneratedDay, out_dir: str | Path = DEFAULT_DAY_DIR) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames_path, truth_path = out / "frames.jsonl", out / "ground_truth.json"
    frames_path.write_text("".join(json.dumps(r) + "\n" for r in day.records), encoding="utf-8")
    truth_path.write_text(json.dumps(day.ground_truth, indent=1), encoding="utf-8")
    return frames_path, truth_path


def load_ground_truth(path: str | Path = DEFAULT_DAY_DIR / "ground_truth.json") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------- slots
def _patrol_slots(script: dict, day: date, cadence: timedelta, docks: list, beats: list[_Slot],
                  rng: random.Random) -> list[_Slot]:
    """Round-robin patrol frames. Scenes replace the quiet caption while they are visible."""
    patrol = script["patrol"]
    route, quiet = patrol["route"], patrol["quiet_captions"]
    scenes = [(s["zone"], _at(day, s["from"]), _at(day, s["to"]), s) for s in script.get("scenes", [])]
    beat_times = [b.at for b in beats]

    slots, at, step = [], datetime.combine(day, datetime.min.time()), 0
    end = at + timedelta(days=1)
    while at < end:
        docked = any(start <= at <= stop for start, stop in docks)
        busy = any(abs((at - t).total_seconds()) <= 60 for t in beat_times)  # drone is on a scripted beat
        if not docked and not busy:
            zone = route[step % len(route)]
            visible = [s for z, start, stop, s in scenes if z == zone and start <= at <= stop]
            if visible:
                caption = "; ".join(rng.choice(s["captions"]) for s in visible)
                objects = [obj for s in visible for obj in s["objects"]]
            else:
                caption, objects = rng.choice(quiet[zone]), []
            slots.append(_Slot(at, zone, caption, objects, {}, "patrol"))
            step += 1
        at += cadence
    return slots


def _dock_slots(docks: list) -> list[_Slot]:
    slots = []
    for start, stop in docks:
        slots.append(_Slot(start, "dock", "Drone landing on the dock to recharge", [], {"mode": "docked"}, "dock"))
        slots.append(_Slot(stop, "dock", "Drone taking off from the dock, battery full", [], {}, "dock"))
    return slots


# ----------------------------------------------------------------------------------- telemetry
class _TelemetryModel:
    """Drone state for each slot: position jittered around the zone, battery drain per sortie."""

    def __init__(self, config: PropertyConfig, patrol: dict, docks: list, rng: random.Random):
        self.config = config
        self.rng = rng
        self.altitude = patrol["altitude_m"]
        self.drain = patrol["battery_drain_pct_per_min"]
        self.docks = docks
        self.prev: Optional[tuple[datetime, float, float]] = None

    def at(self, slot: _Slot) -> dict:
        overrides = dict(slot.overrides)
        lat, lon = self._position(slot.zone, overrides)
        docked = overrides.pop("mode", None) == "docked"
        heading, speed = self._motion(slot.at, lat, lon)
        values = {
            "drone_id": self.config.drone_id,
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "altitude_m": 0.0 if docked else round(self.altitude + self.rng.uniform(-2, 2), 1),
            "battery_pct": self._battery(slot.at, docked),
            "heading_deg": round(heading, 1),
            "speed_mps": round(speed, 2),
            "wind_mps": round(3.0 + self.rng.uniform(-1.5, 2.5), 1),
            "gps_hdop": round(0.7 + self.rng.uniform(0, 0.4), 2),
            "mode": "docked" if docked else "patrol",
        }
        values.update(overrides)
        return values

    def _position(self, zone_id: str, overrides: dict) -> tuple[float, float]:
        if "lat" in overrides and "lon" in overrides:
            return overrides.pop("lat"), overrides.pop("lon")
        zone = self.config.zone(zone_id)
        lat, lon = (zone.lat, zone.lon) if zone else (self.config.home_lat, self.config.home_lon)
        if zone_id != "dock":
            lat += self.rng.gauss(0, 4) / METERS_PER_DEG_LAT  # ~4 m of normal GPS jitter
            lon += self.rng.gauss(0, 4) / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))
        # scripted GPS drift: the reported fix is pushed away from the true position
        lat += overrides.pop("lat_offset_m", 0) / METERS_PER_DEG_LAT
        lon += overrides.pop("lon_offset_m", 0) / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))
        return lat, lon

    def _motion(self, at: datetime, lat: float, lon: float) -> tuple[float, float]:
        heading = speed = 0.0
        if self.prev:
            prev_at, prev_lat, prev_lon = self.prev
            dt = max((at - prev_at).total_seconds(), 1.0)
            dy = (lat - prev_lat) * METERS_PER_DEG_LAT
            dx = (lon - prev_lon) * METERS_PER_DEG_LAT * math.cos(math.radians(lat))
            heading = (math.degrees(math.atan2(dx, dy)) + 360) % 360
            speed = min(math.hypot(dx, dy) / dt, 12.0)  # cruise speed cap; sparse frames hide the path
        self.prev = (at, lat, lon)
        return heading, speed

    def _battery(self, at: datetime, docked: bool) -> float:
        """Linear drain since the last take-off; the drone leaves the dock at 100%."""
        if docked:
            sortie_start = max((stop for start, stop in self.docks if stop < at), default=None)
        else:
            sortie_start = max((stop for start, stop in self.docks if stop <= at), default=None)
        sortie_start = sortie_start or datetime.combine(at.date(), datetime.min.time())
        minutes = (at - sortie_start).total_seconds() / 60
        return round(max(100 - self.drain * minutes, 5.0), 1)


# ----------------------------------------------------------------------------------- labels
def _label(obj: dict, beat_zone: str, entities: dict) -> dict:
    entity = entities[obj["entity"]]
    return {
        "entity": obj["entity"],
        "category": entity["category"],
        "label": entity["label"],
        "color": entity.get("color"),
        "make": entity.get("make"),
        "model": entity.get("model"),
        "count": entity.get("count", 1),
        "action": obj.get("action"),
        "state": obj.get("state"),
        "zone": obj.get("zone", beat_zone),
    }


def _absolute(alert: dict, day: date) -> dict:
    return {**alert, "from": _at(day, alert["from"]).strftime(TS_FORMAT), "to": _at(day, alert["to"]).strftime(TS_FORMAT)}


def _at(day: date, hhmm: str) -> datetime:
    fmt = "%H:%M:%S" if hhmm.count(":") == 2 else "%H:%M"
    return datetime.combine(day, datetime.strptime(hhmm, fmt).time())


__all__ = ["GeneratedDay", "generate_day", "generate_frames", "load_script", "load_ground_truth", "write_day",
           "DEFAULT_SCRIPT", "DEFAULT_DAY_DIR"]
