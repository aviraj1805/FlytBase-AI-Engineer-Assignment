"""Entity tracking across frames: who/what is this, have we seen it before, for how long?

A docked drone revisits the same property every day, so the useful questions are
about *identities over time* ("the blue Ford F150 entered twice today") rather than
per-frame boxes. The tracker turns per-frame detections into persistent entities
using an appearance signature, splits sightings into visits with a time gap, and
measures dwell time per zone (the basis of loitering detection).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .models import Detection
from .perception.lexicon import CLOTHING

_CLOTHING = set(CLOTHING.values())
_PREFIX = {"vehicle": "V", "person": "P", "animal": "A", "object": "O", "infrastructure": "I"}


@dataclass
class Visit:
    start: datetime
    end: datetime
    zones: list[str] = field(default_factory=list)
    closed: bool = False  # an exit was observed


@dataclass
class Entity:
    entity_id: str
    signature: str
    category: str
    label: str
    display_name: str
    first_seen: datetime
    last_seen: datetime
    last_zone: str
    zone_since: datetime
    sightings: int = 0
    visits: list[Visit] = field(default_factory=list)

    def visits_on(self, day: date) -> list[Visit]:
        return [v for v in self.visits if v.start.date() == day]

    @property
    def dwell_seconds(self) -> float:
        return (self.last_seen - self.zone_since).total_seconds()


@dataclass
class TrackUpdate:
    entity: Entity
    is_new_entity: bool
    is_new_visit: bool
    zone_changed: bool
    dwell_seconds: float


class EntityTracker:
    """Re-identifies entities and segments their sightings into visits.

    Patrol frames are sparse (a pass every ~30 min), so a time gap alone is weak
    evidence that a vehicle left and came back. Visits therefore follow observed
    behaviour: a visit closes when the entity is seen leaving, and a new one opens
    when it is seen again after that, or seen *entering* after a gap, or not seen at
    all for `max_open_gap`. Dwell time only accumulates while the entity is observed
    continuously (consecutive sightings within `visit_gap`).
    """

    def __init__(self, visit_gap: timedelta = timedelta(minutes=15), max_open_gap: timedelta = timedelta(hours=6)):
        self.visit_gap = visit_gap
        self.max_open_gap = max_open_gap
        self.entities: dict[str, Entity] = {}
        self._counters: dict[str, int] = {}

    @staticmethod
    def signature(det: Detection) -> str:
        """Appearance key used for re-identification.

        Vehicles are keyed on colour/make/model/type. People are keyed on clothing,
        or on zone when no descriptor is available (two anonymous figures at
        different ends of the property should not merge into one person).
        """
        if det.category == "vehicle":
            return f"vehicle|{det.label}|{det.color or '?'}|{det.make or '?'}|{det.model or '?'}"
        if det.category == "person":
            clothing = sorted(a for a in det.attributes if a in _CLOTHING)
            if det.color or clothing:
                return f"person|{det.color or '?'}|{','.join(clothing)}|{'masked' if 'masked' in det.attributes else ''}"
            return f"person|anon|{det.zone}|{'masked' if 'masked' in det.attributes else ''}"
        return f"{det.category}|{det.label}|{det.color or '?'}|{det.zone}"

    def observe(self, det: Detection, at: datetime) -> TrackUpdate:
        sig = self.signature(det)
        entity = self.entities.get(sig)
        if entity is None:
            entity = Entity(
                entity_id=self._next_id(det.category),
                signature=sig,
                category=det.category,
                label=det.label,
                display_name=det.display_name,
                first_seen=at,
                last_seen=at,
                last_zone=det.zone,
                zone_since=at,
                sightings=1,
                visits=[Visit(at, at, [det.zone], closed=det.action == "exiting")],
            )
            self.entities[sig] = entity
            det.entity_id = entity.entity_id
            return TrackUpdate(entity, True, True, True, 0.0)

        gap = at - entity.last_seen
        current = entity.visits[-1]
        is_new_visit = (
            (current.closed and (gap > self.visit_gap or det.action == "entering"))
            or (det.action == "entering" and gap > self.visit_gap)  # came back in; the exit was missed
            or gap > self.max_open_gap
        )
        zone_changed = det.zone != entity.last_zone
        if is_new_visit:
            current = Visit(at, at, [det.zone])
            entity.visits.append(current)
        else:
            current.end = at
            if det.zone not in current.zones:
                current.zones.append(det.zone)
        current.closed = det.action == "exiting"
        if is_new_visit or zone_changed or gap > self.visit_gap:
            entity.zone_since = at

        entity.last_seen = at
        entity.last_zone = det.zone
        entity.sightings += 1
        det.entity_id = entity.entity_id
        return TrackUpdate(entity, False, is_new_visit, zone_changed, entity.dwell_seconds)

    def get(self, entity_id: str) -> Optional[Entity]:
        return next((e for e in self.entities.values() if e.entity_id == entity_id), None)

    def _next_id(self, category: str) -> str:
        prefix = _PREFIX.get(category, "X")
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]:03d}"
