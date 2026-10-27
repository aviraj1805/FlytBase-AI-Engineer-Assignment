"""Simulated drone feed: scripted scenarios and random load for benchmarks.

A scenario is a human-readable JSON script of captioned frames ("Time: 00:01,
Location: main gate, Frame: person loitering near main gate"). The simulator turns it
into a realistic stream: GPS fixes jittered around each zone, altitude, heading,
speed, wind, a battery model, and quiet background patrol frames between scripted
events. Telemetry fields can be overridden per frame to script flight-safety events.
"""
from __future__ import annotations

import json
import math
import random
import re
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .config import PropertyConfig
from .models import Frame, Telemetry

QUIET_CAPTIONS = {
    "main_gate": "Main gate closed, street outside quiet",
    "driveway": "Empty driveway, no vehicles",
    "garage": "Garage door closed, no activity",
    "front_door": "Front porch clear",
    "backyard": "Backyard clear, no movement",
    "back_fence": "Back fence intact, no one nearby",
    "warehouse": "Warehouse loading bay quiet",
}
PATROL_ROUTE = ["main_gate", "driveway", "garage", "warehouse", "back_fence", "backyard", "front_door"]
_TEXT_LINE = re.compile(
    r"time:\s*(?P<time>\d{1,2}:\d{2})\s*[,|]\s*location:\s*(?P<loc>[^,|]+?)\s*[,|]\s*"
    r"(?:frame\s*\d*\s*:\s*)?(?P<desc>.+)$",
    re.IGNORECASE,
)


class TelemetrySynthesizer:
    """Generates plausible telemetry for a sequence of (time, zone) waypoints."""

    def __init__(self, config: PropertyConfig, seed: int = 7):
        self.config = config
        self.rng = random.Random(seed)
        self._prev: Optional[tuple[datetime, float, float]] = None
        self._sortie_start: Optional[datetime] = None

    def next(self, at: datetime, zone_id: str, overrides: Optional[dict] = None) -> Telemetry:
        overrides = dict(overrides or {})
        zone = self.config.zone(zone_id)
        if "lat" in overrides and "lon" in overrides:
            lat, lon = overrides.pop("lat"), overrides.pop("lon")
        elif zone:
            lat = zone.lat + self.rng.gauss(0, 0.00004)  # ~4 m of jitter
            lon = zone.lon + self.rng.gauss(0, 0.00004)
        else:
            lat, lon = self.config.home_lat, self.config.home_lon

        heading, speed = 0.0, 0.0
        if self._prev:
            prev_at, prev_lat, prev_lon = self._prev
            dt = max((at - prev_at).total_seconds(), 1.0)
            dist = math.hypot((lat - prev_lat) * 111_000, (lon - prev_lon) * 111_000 * math.cos(math.radians(lat)))
            heading = (math.degrees(math.atan2(lon - prev_lon, lat - prev_lat)) + 360) % 360
            speed = min(dist / dt, 12.0) if dt < 600 else 4.0 + self.rng.uniform(-1, 1)
            if dt > 25 * 60:  # long gap: drone docked and recharged between sorties
                self._sortie_start = at
        if self._sortie_start is None:
            self._sortie_start = at
        self._prev = (at, lat, lon)

        mode = "docked" if zone_id == "dock" else "patrol"
        minutes_airborne = (at - self._sortie_start).total_seconds() / 60
        values = dict(
            timestamp=at,
            drone_id=self.config.drone_id,
            lat=round(lat, 7),
            lon=round(lon, 7),
            altitude_m=0.0 if mode == "docked" else round(30 + self.rng.uniform(-3, 3), 1),
            heading_deg=round(heading, 1),
            speed_mps=round(speed, 2),
            battery_pct=round(max(95 - 0.35 * minutes_airborne, 30), 1) if mode != "docked" else 100.0,
            zone=self.config.locate(lat, lon) if zone_id != "dock" else "dock",
            mode=mode,
            wind_mps=round(3.0 + self.rng.uniform(-1.5, 2.5), 1),
        )
        values.update(overrides)
        values["distance_from_home_m"] = round(self.config.distance_from_home(values["lat"], values["lon"]), 1)
        return Telemetry(**values)


def load_scenario(path: str | Path, config: PropertyConfig, *, seed: int = 7, background: bool = True) -> list[Frame]:
    """Load a JSON scenario (or a .txt file of 'Time: HH:MM, Location: X, Frame: ...' lines)."""
    path = Path(path)
    if path.suffix.lower() == ".txt":
        spec = {"date": date.today().isoformat(), "frames": parse_text_frames(path.read_text(encoding="utf-8"))}
    else:
        spec = json.loads(path.read_text(encoding="utf-8"))
    return build_frames(spec, config, seed=seed, background=background)


def parse_text_frames(text: str) -> list[dict]:
    frames = []
    for line in text.splitlines():
        match = _TEXT_LINE.search(line.strip())
        if match:
            frames.append({
                "time": match["time"].zfill(5),
                "location": "_".join(match["loc"].lower().split()),
                "description": match["desc"].strip(),
            })
    return frames


def build_frames(spec: dict, config: PropertyConfig, *, seed: int = 7, background: bool = True) -> list[Frame]:
    day = date.fromisoformat(spec.get("date", date.today().isoformat()))
    scripted = []
    for item in spec["frames"]:
        at = datetime.combine(day, datetime.strptime(item["time"], "%H:%M:%S" if item["time"].count(":") == 2 else "%H:%M").time())
        scripted.append((at, _zone_id(item.get("location"), config), item["description"], item.get("telemetry", {})))

    if background:
        scripted += _background_patrol(day, scripted, spec.get("patrol_interval_minutes", 30))
    scripted.sort(key=lambda s: s[0])

    synth = TelemetrySynthesizer(config, seed)
    return [
        Frame(frame_id=i, timestamp=at, description=desc, telemetry=synth.next(at, zone, overrides))
        for i, (at, zone, desc, overrides) in enumerate(scripted, start=1)
    ]


def _background_patrol(day: date, scripted: list, interval_min: int) -> list:
    """Quiet patrol frames on a round-robin route, skipping slots near scripted events."""
    busy = [s[0] for s in scripted]
    frames, slot, step = [], datetime.combine(day, datetime.min.time()), 0
    end = slot + timedelta(days=1)
    while slot < end:
        if all(abs((slot - b).total_seconds()) > 10 * 60 for b in busy):
            zone = PATROL_ROUTE[step % len(PATROL_ROUTE)]
            frames.append((slot, zone, QUIET_CAPTIONS[zone], {}))
        step += 1
        slot += timedelta(minutes=interval_min)
    return frames


def _zone_id(location: Optional[str], config: PropertyConfig) -> str:
    if not location:
        return "open_area"
    key = "_".join(location.lower().split())
    if config.zone(key):
        return key
    by_name = next((z.id for z in config.zones if z.name.lower() == location.lower()), None)
    if by_name:
        return by_name
    gate_like = next((z.id for z in config.zones if key in z.id), None)  # "gate" -> "main_gate"
    return gate_like or "open_area"


def stream(frames: Iterable[Frame], speed: float = 0.0) -> Iterator[Frame]:
    """Replay frames as a live feed. speed=60 plays one minute of footage per second; 0 = no delay."""
    prev: Optional[Frame] = None
    for frame in frames:
        if prev and speed > 0:
            _time.sleep(min((frame.timestamp - prev.timestamp).total_seconds() / speed, 2.0))
        prev = frame
        yield frame


# ------------------------------------------------------------------------------- load testing
_RANDOM_SUBJECTS = [
    "{color} {vtype} {action_v} at {zone}", "{color} Ford F150 {action_v} at {zone}",
    "{color} Toyota Camry {action_v} in {zone}", "Person in {color} {cloth} {action_p} near {zone}",
    "Two people {action_p} at {zone}", "{animal} {action_a} in {zone}", "{zone} clear, no movement",
    "Courier drops off a package at front door", "Masked man climbing the back fence",
]
_RANDOM_FILL = {
    "color": ["blue", "white", "black", "red", "silver", "gray", "green", "dark"],
    "vtype": ["truck", "sedan", "van", "SUV", "pickup truck", "motorcycle"],
    "action_v": ["parked", "entering", "leaving", "idling"],
    "cloth": ["hoodie", "jacket", "hi-vis vest", "cap", "coat"],
    "action_p": ["walking", "standing", "loitering", "running", "waiting"],
    "animal": ["Dog", "Cat", "Deer", "Fox"],
    "action_a": ["walking", "grazing", "running"],
    "zone": ["main gate", "driveway", "garage", "front door", "backyard", "back fence", "warehouse"],
}


def random_frames(n: int, config: PropertyConfig, *, seed: int = 1, start: Optional[datetime] = None,
                  interval_s: float = 3.0) -> list[Frame]:
    """n synthetic frames for scalability tests (one every `interval_s` seconds of footage)."""
    rng = random.Random(seed)
    synth = TelemetrySynthesizer(config, seed)
    at = start or datetime.combine(date.today(), datetime.min.time())
    frames = []
    for i in range(1, n + 1):
        template = rng.choice(_RANDOM_SUBJECTS)
        fill = {k: rng.choice(v) for k, v in _RANDOM_FILL.items()}
        desc = template.format(**fill)
        zone_id = "_".join(fill["zone"].split())
        frames.append(Frame(frame_id=i, timestamp=at, description=desc[0].upper() + desc[1:],
                            telemetry=synth.next(at, zone_id)))
        at += timedelta(seconds=interval_s)
    return frames


__all__ = ["load_scenario", "build_frames", "parse_text_frames", "random_frames", "stream"]
