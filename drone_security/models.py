"""Core data types shared by every stage of the pipeline.

Plain dataclasses keep the core dependency-free so the same code can run on the
dock's edge computer and in the cloud.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Optional


class Severity(IntEnum):
    INFO = 10
    LOW = 20
    MEDIUM = 30
    HIGH = 40
    CRITICAL = 50

    @classmethod
    def parse(cls, value: "str | int | Severity") -> "Severity":
        if isinstance(value, Severity):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[value.strip().upper()]


@dataclass(frozen=True)
class Telemetry:
    timestamp: datetime
    drone_id: str
    lat: float
    lon: float
    altitude_m: float
    heading_deg: float
    speed_mps: float
    battery_pct: float
    zone: str
    mode: str = "patrol"  # patrol | hover | rtl (return to launch) | docked
    wind_mps: float = 0.0
    distance_from_home_m: float = 0.0


@dataclass(frozen=True)
class Frame:
    frame_id: int
    timestamp: datetime
    description: str  # simulated caption, or a VLM caption of a real image
    telemetry: Telemetry
    source: str = "simulated"  # simulated | vlm:claude | vlm:blip
    image_path: Optional[str] = None


@dataclass
class Detection:
    category: str  # person | vehicle | animal | object | infrastructure
    label: str  # truck, car, person, dog, package, gate ...
    zone: str
    color: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    attributes: list[str] = field(default_factory=list)
    action: Optional[str] = None
    count: int = 1
    confidence: float = 0.9
    entity_id: Optional[str] = None  # assigned by the tracker

    @property
    def display_name(self) -> str:
        if self.category == "vehicle":
            if self.make or self.model:
                return _title([self.color, self.make, self.model])
            label = "SUV" if self.label == "suv" else self.label
            return f"{self.color.capitalize()} {label}" if self.color else label[0].upper() + label[1:]
        if self.category == "person":
            noun = "Person" if self.count == 1 else f"{self.count} people"
            if "masked" in self.attributes:
                noun = "Masked person" if self.count == 1 else f"{self.count} masked people"
            clothing = next((a for a in self.attributes if a in _CLOTHING_HINT), None)
            if clothing:
                return f"{noun} in {self.color + ' ' if self.color else ''}{clothing}"
            return noun
        prefix = f"{self.color} " if self.color else ""
        return (prefix + self.label).capitalize()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Clothing words used when naming people; kept here to avoid a circular import with the lexicon.
_CLOTHING_HINT = {
    "hoodie", "jacket", "coat", "vest", "hi-vis vest", "uniform", "cap", "hat",
    "helmet", "raincoat", "t-shirt", "shirt", "overalls",
}


def _title(parts: list[Optional[str]]) -> str:
    words: list[str] = []
    for part in parts:
        if not part:
            continue
        # keep model names such as "F150" or "CR-V" as written
        words.append(part if any(ch.isdigit() for ch in part) or part.isupper() else part.capitalize())
    return " ".join(words)


@dataclass
class EventLog:
    timestamp: datetime
    frame_id: int
    message: str  # e.g. "Blue Ford F150 spotted at garage, 12:00."
    entity_id: Optional[str] = None
    zone: Optional[str] = None
    kind: str = "sighting"  # sighting | visit | status


@dataclass
class Alert:
    timestamp: datetime
    frame_id: int
    rule_id: str
    severity: Severity
    message: str  # e.g. "Person loitering at main gate, 00:01."
    zone: Optional[str] = None
    entity_id: Optional[str] = None
    recommended_action: str = ""
    related_rules: list[str] = field(default_factory=list)  # rules folded into this alert by correlation
    alert_id: Optional[int] = None
    dedupe_key: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("dedupe_key")
        data["severity"] = self.severity.name
        data["timestamp"] = self.timestamp.isoformat(sep=" ")
        return data


@dataclass
class FrameResult:
    """Everything the pipeline produced for one frame."""

    frame: Frame
    detections: list[Detection]
    logs: list[EventLog]
    alerts: list[Alert]
