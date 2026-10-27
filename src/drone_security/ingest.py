"""Feed ingestion: raw records -> validated, de-duplicated, time-ordered Frames.

A real drone link is messy: packets arrive twice, out of order, or with the telemetry
part missing, and a bad record must never crash the analyst. Three defences:

* **Validation**: a record without a usable id, time or caption is quarantined with a
  reason. Broken or missing telemetry is dropped, but the caption is still processed
  (the camera saw something even if the GPS packet was lost).
* **De-duplication**: the same frame id (or the same time + caption) is processed once.
* **Reorder buffer**: frames are held in a small min-heap and released in time order,
  which absorbs the usual network jitter. Anything later than the buffer can absorb is
  handled by the pipeline's watermark (indexed, but not fed to the stateful tracker).
"""
from __future__ import annotations

import heapq
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .config import PropertyConfig
from .models import Frame, Telemetry
from .timeutil import TS_FORMAT

_TELEMETRY_REQUIRED = ("lat", "lon", "altitude_m", "battery_pct")
_TELEMETRY_OPTIONAL = ("heading_deg", "speed_mps", "wind_mps", "gps_hdop")
_TEXT_LINE = re.compile(
    r"time:\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s*[,|]\s*location:\s*(?P<loc>[^,|]+?)\s*[,|]\s*"
    r"(?:frame\s*(?P<id>\d+)?\s*:\s*)?(?P<desc>.+)$",
    re.IGNORECASE,
)


class MalformedRecord(ValueError):
    """The record cannot be turned into a frame (no id, no time or no caption)."""


@dataclass
class IngestStats:
    received: int = 0
    emitted: int = 0
    duplicates: int = 0
    missing_telemetry: int = 0
    quarantined: list[tuple[str, str]] = field(default_factory=list)  # (record excerpt, reason)


def parse_record(raw: dict, config: PropertyConfig) -> Frame:
    """Validate one record and build a Frame. Raises MalformedRecord for unusable input."""
    if not isinstance(raw, dict):
        raise MalformedRecord("record is not an object")
    try:
        frame_id = int(raw["frame_id"])
    except (KeyError, TypeError, ValueError):
        raise MalformedRecord("missing or non-integer frame_id") from None
    if frame_id <= 0:
        raise MalformedRecord("frame_id must be positive")
    timestamp = _parse_time(raw.get("timestamp"))
    if timestamp is None:
        raise MalformedRecord("missing or unparseable timestamp")
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise MalformedRecord("missing caption")

    location = raw.get("location")
    caption_zone = _zone_from_text(location, config) if isinstance(location, str) and location.strip() else None
    return Frame(
        frame_id=frame_id,
        timestamp=timestamp,
        description=" ".join(description.split()),
        telemetry=_telemetry(raw.get("telemetry"), timestamp, config),
        source=raw.get("source", "simulated"),
        image_path=raw.get("image_path"),
        caption_zone=caption_zone,
    )


def _telemetry(raw: Optional[dict], timestamp: datetime, config: PropertyConfig) -> Optional[Telemetry]:
    """Telemetry is optional: return None when absent or unusable instead of failing the frame."""
    if not isinstance(raw, dict):
        return None
    try:
        values = {key: float(raw[key]) for key in _TELEMETRY_REQUIRED}
        values.update({key: float(raw[key]) for key in _TELEMETRY_OPTIONAL if raw.get(key) is not None})
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= values["lat"] <= 90 and -180 <= values["lon"] <= 180):
        return None
    return Telemetry(
        timestamp=timestamp,
        drone_id=str(raw.get("drone_id") or config.drone_id),
        mode=str(raw.get("mode") or "patrol"),
        zone="dock" if raw.get("mode") == "docked" else config.locate(values["lat"], values["lon"]),
        distance_from_home_m=round(config.distance_from_home(values["lat"], values["lon"]), 1),
        **values,
    )


class FeedSanitizer:
    """Validates, de-duplicates and re-orders a stream of raw records."""

    def __init__(self, config: PropertyConfig, *, reorder_window: int = 8):
        self.config = config
        self.window = reorder_window
        self.stats = IngestStats()
        self._seen_ids: set[int] = set()
        self._seen_content: set[tuple[datetime, str]] = set()

    def process(self, records: Iterable[dict]) -> Iterator[Frame]:
        heap: list[tuple[datetime, int, Frame]] = []
        for raw in records:
            self.stats.received += 1
            try:
                frame = parse_record(raw, self.config)
            except MalformedRecord as exc:
                self.stats.quarantined.append((str(raw)[:120], str(exc)))
                continue
            content = (frame.timestamp, frame.description)
            if frame.frame_id in self._seen_ids or content in self._seen_content:
                self.stats.duplicates += 1
                continue
            self._seen_ids.add(frame.frame_id)
            self._seen_content.add(content)
            if frame.telemetry is None:
                self.stats.missing_telemetry += 1
            heapq.heappush(heap, (frame.timestamp, frame.frame_id, frame))
            if len(heap) > self.window:
                yield self._emit(heap)
        while heap:
            yield self._emit(heap)

    def _emit(self, heap: list) -> Frame:
        self.stats.emitted += 1
        return heapq.heappop(heap)[2]


# ----------------------------------------------------------------------------------- file formats
def read_records(path: str | Path, *, day: Optional[date] = None) -> list[dict]:
    """Raw records from a .jsonl feed (one record per line) or the brief's text format.

    Text lines look like "Time: 00:01, Location: Gate, Frame 1: Person loitering at gate";
    they carry no telemetry. Unparseable lines become records the sanitizer will quarantine.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".txt":
        return parse_text_lines(text, day or date.today())
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"unparseable_line": line[:120]})
    return records


def parse_text_lines(text: str, day: date) -> list[dict]:
    records = []
    for number, line in enumerate((ln.strip() for ln in text.splitlines() if ln.strip()), start=1):
        match = _TEXT_LINE.search(line)
        if not match:
            records.append({"unparseable_line": line[:120]})
            continue
        clock = match["time"] if match["time"].count(":") == 2 else match["time"] + ":00"
        records.append({
            "frame_id": int(match["id"]) if match["id"] else number,
            "timestamp": f"{day.isoformat()} {clock.zfill(8)}",
            "location": match["loc"].strip(),
            "description": match["desc"].strip(),
        })
    return records


def _parse_time(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().replace("T", " ")
    for fmt in (TS_FORMAT, "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _zone_from_text(location: str, config: PropertyConfig) -> str:
    """'Gate' -> 'main_gate', 'Front door' -> 'front_door' (exact id, then name, then partial match)."""
    key = "_".join(location.lower().split())
    if config.zone(key):
        return key
    for zone in config.zones:
        if zone.name.lower() == location.strip().lower():
            return zone.id
    partial = next((z.id for z in config.zones if key in z.id), None)
    return partial or key


__all__ = ["FeedSanitizer", "IngestStats", "MalformedRecord", "parse_record", "read_records", "parse_text_lines"]
