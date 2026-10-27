"""Property profile: zones, schedules, whitelists and alert thresholds."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "property.json"


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    lat: float
    lon: float
    radius_m: float
    perimeter: bool = False
    restricted: bool = False


@dataclass(frozen=True)
class Thresholds:
    loiter_minutes: float = 5
    visit_gap_minutes: float = 15
    repeat_vehicle_visits: int = 2
    alert_cooldown_minutes: float = 15
    battery_low_pct: float = 25
    battery_critical_pct: float = 15
    min_patrol_altitude_m: float = 12
    max_wind_mps: float = 11


@dataclass(frozen=True)
class TimeWindow:
    start: time
    end: time

    def contains(self, moment: datetime | time) -> bool:
        t = moment.time() if isinstance(moment, datetime) else moment
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end  # window wraps past midnight


@dataclass
class PropertyConfig:
    property_name: str
    drone_id: str
    home_lat: float
    home_lon: float
    geofence_radius_m: float
    night: TimeWindow
    business: TimeWindow
    zones: list[Zone]
    known_vehicles: list[dict] = field(default_factory=list)
    thresholds: Thresholds = field(default_factory=Thresholds)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> "PropertyConfig":
        raw = json.loads(Path(path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "PropertyConfig":
        return cls(
            property_name=raw["property_name"],
            drone_id=raw.get("drone_id", "DRONE-01"),
            home_lat=raw["home"]["lat"],
            home_lon=raw["home"]["lon"],
            geofence_radius_m=raw.get("geofence_radius_m", 300),
            night=_window(raw["night_hours"]),
            business=_window(raw["business_hours"]),
            zones=[Zone(**z) for z in raw["zones"]],
            known_vehicles=raw.get("known_vehicles", []),
            thresholds=Thresholds(**raw.get("thresholds", {})),
        )

    # ------------------------------------------------------------------ lookups
    def zone(self, zone_id: Optional[str]) -> Optional[Zone]:
        return next((z for z in self.zones if z.id == zone_id), None)

    def zone_name(self, zone_id: Optional[str]) -> str:
        zone = self.zone(zone_id)
        return zone.name if zone else (zone_id or "unknown area").replace("_", " ")

    def locate(self, lat: float, lon: float) -> str:
        """Map a GPS fix to the nearest named zone (or 'open_area' if none is in range)."""
        best_id, best_dist = "open_area", float("inf")
        for zone in self.zones:
            dist = haversine_m(lat, lon, zone.lat, zone.lon)
            if dist <= zone.radius_m and dist < best_dist:
                best_id, best_dist = zone.id, dist
        return best_id

    def distance_from_home(self, lat: float, lon: float) -> float:
        return haversine_m(lat, lon, self.home_lat, self.home_lon)

    def is_night(self, moment: datetime) -> bool:
        return self.night.contains(moment)

    def is_business_hours(self, moment: datetime) -> bool:
        return self.business.contains(moment)

    def is_known_vehicle(self, color: Optional[str], make: Optional[str], model: Optional[str], label: str) -> Optional[str]:
        """Return the owner note if the vehicle matches a whitelist entry."""
        for entry in self.known_vehicles:
            checks = {
                "color": color,
                "make": make,
                "model": model,
                "label": label,
            }
            if all((checks[key] or "").lower() == str(value).lower() for key, value in entry.items() if key in checks):
                return entry.get("owner", "known vehicle")
        return None


def _window(raw: dict) -> TimeWindow:
    return TimeWindow(time.fromisoformat(raw["start"]), time.fromisoformat(raw["end"]))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
