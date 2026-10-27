"""Entity tracking and re-identification across the day.

A docked drone revisits the same property every day, so the useful questions are about
*identities over time* ("the blue Ford F150 entered twice today"), not per-frame boxes.

Re-identification compares a new detection with every known entity of the same category
using its appearance attributes (colour, make, model, clothing, masked):

* an attribute both sides know adds its weight when it agrees and subtracts twice its
  weight when it conflicts (one contradiction outweighs one agreement);
* recent sightings add a continuity bonus (the same object a minute later, maybe less
  well described), larger when it is still in the same zone;
* an entity that left a while ago is only matched on strong identity evidence
  (e.g. make or model for vehicles), because "a blue pickup" alone is not enough to
  say the F150 came back.

The best score above the threshold wins. Track confidence is that score minus half the
runner-up's score, so an ambiguous match (two blue pickups on site) is reported as such.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .models import CLOTHING_WORDS, Detection

_PREFIX = {"vehicle": "V", "person": "P", "animal": "A", "object": "O", "infrastructure": "I"}
_STATIC = {"object", "infrastructure"}
_VEHICLE_WEIGHTS = (("color", 2.0), ("make", 2.0), ("model", 3.0))
_PERSON_WEIGHTS = (("color", 2.0), ("clothing", 2.0), ("masked", 2.0))
# share of the identity weight that must be comparable to re-identify an entity after it left
_STRONG_EVIDENCE = {"vehicle": 0.5, "person": 0.3}
ACCEPT_SCORE = 0.5
MAX_EVIDENCE_FRAMES = 50  # cap on stored evidence ids per stay, keeps memory flat for a parked car


@dataclass
class Visit:
    start: datetime
    end: datetime
    zones: list[str] = field(default_factory=list)
    frame_ids: list[int] = field(default_factory=list)
    closed: bool = False  # an exit was observed


@dataclass
class Entity:
    entity_id: str
    category: str
    label: str
    display_name: str
    first_seen: datetime
    last_seen: datetime
    last_zone: str
    zone_since: datetime
    sightings: int = 0
    visits: list[Visit] = field(default_factory=list)
    zone_frame_ids: list[int] = field(default_factory=list)  # frames since zone_since: dwell evidence
    traits: dict[str, Counter] = field(default_factory=dict)  # attribute votes across sightings
    confidences: list[float] = field(default_factory=list)

    def trait(self, name: str):
        votes = self.traits.get(name)
        return votes.most_common(1)[0][0] if votes else None

    def visits_on(self, day: date) -> list[Visit]:
        return [v for v in self.visits if v.start.date() == day]

    @property
    def dwell_seconds(self) -> float:
        return (self.last_seen - self.zone_since).total_seconds()

    @property
    def signature(self) -> str:
        """Readable summary of the identity, stored in the index."""
        keys = ("label", "color", "make", "model", "clothing", "masked")
        return "|".join([self.category] + [str(self.trait(k) or "?") for k in keys])

    @property
    def mean_confidence(self) -> float:
        return sum(self.confidences) / len(self.confidences) if self.confidences else 1.0


@dataclass
class TrackUpdate:
    entity: Entity
    is_new_entity: bool
    is_new_visit: bool
    zone_changed: bool
    dwell_seconds: float
    confidence: float


class EntityTracker:
    """Re-identifies entities, segments their sightings into visits and measures dwell time.

    Visits follow observed behaviour: a visit closes when the entity is seen leaving and a
    new one opens when it is seen again after that, or seen *entering* after a gap, or not
    seen at all for `max_open_gap`. Dwell time only accumulates while the entity is observed
    continuously (consecutive sightings within `visit_gap`).
    """

    def __init__(self, visit_gap: timedelta = timedelta(minutes=15), max_open_gap: timedelta = timedelta(hours=6),
                 reid_horizon: timedelta = timedelta(days=7)):
        self.visit_gap = visit_gap
        self.max_open_gap = max_open_gap
        self.reid_horizon = reid_horizon  # entities unseen for longer are no longer match candidates
        self.entities: dict[str, Entity] = {}
        self._active: dict[str, list[Entity]] = {}  # category -> candidates for matching
        self._counters: dict[str, int] = {}

    # ------------------------------------------------------------------ public
    def observe(self, det: Detection, at: datetime, frame_id: int = 0) -> TrackUpdate:
        candidates = self._candidates(det.category, at)
        scored = sorted(((self.similarity(det, e, at), e) for e in candidates), key=lambda s: s[0], reverse=True)
        best_score, best = scored[0] if scored else (-1.0, None)
        runner_up = scored[1][0] if len(scored) > 1 else 0.0

        if best is None or best_score < ACCEPT_SCORE:
            # how sure we are this is someone new: high when nothing came close
            confidence = round(min(1.0, 1.0 - max(best_score, 0.0)), 2)
            entity = self._create(det, at, frame_id, confidence)
            update = TrackUpdate(entity, True, True, True, 0.0, confidence)
        else:
            confidence = round(max(0.0, min(1.0, best_score - 0.5 * max(runner_up, 0.0))), 2)
            update = self._update(best, det, at, frame_id, confidence)

        det.entity_id = update.entity.entity_id
        det.track_confidence = update.confidence
        return update

    def get(self, entity_id: str) -> Optional[Entity]:
        return self.entities.get(entity_id)

    # ------------------------------------------------------------------ matching
    def similarity(self, det: Detection, entity: Entity, at: datetime) -> float:
        """Score in [-1, 1]; at or above ACCEPT_SCORE means 'same entity'."""
        if det.category != entity.category:
            return -1.0
        recent = at - entity.last_seen <= self.visit_gap
        same_zone = det.zone == entity.last_zone

        if det.category in ("object", "infrastructure"):
            # static things: same kind of thing in the same place is the same thing
            return 1.0 if det.label == entity.label and same_zone else -1.0
        if det.category == "animal":
            return 1.0 if det.label == entity.label and recent else -1.0

        if det.category == "vehicle":
            if not _labels_compatible(det.label, entity.trait("label")):
                return -1.0
            agreement, evidence = _weighted_agreement(_vehicle_traits(det), entity, _VEHICLE_WEIGHTS)
        else:
            agreement, evidence = _weighted_agreement(_person_traits(det), entity, _PERSON_WEIGHTS)
            if det.count != (entity.trait("count") or 1):
                return -1.0  # "two people" is not the same track as "a person"

        score = agreement
        if recent:
            score += 0.6 if same_zone else 0.2  # continuity: probably still the same object in view
        elif evidence < _STRONG_EVIDENCE[det.category]:
            score = min(score, 0.3)  # returning after a gap needs real identity evidence
        return max(-1.0, min(1.0, score))

    # ------------------------------------------------------------------ bookkeeping
    def _create(self, det: Detection, at: datetime, frame_id: int, confidence: float) -> Entity:
        entity = Entity(
            entity_id=self._next_id(det.category),
            category=det.category,
            label=det.label,
            display_name=det.display_name,
            first_seen=at,
            last_seen=at,
            last_zone=det.zone,
            zone_since=at,
            sightings=1,
            visits=[Visit(at, at, [det.zone], [frame_id], closed=det.action == "exiting")],
            zone_frame_ids=[frame_id],
        )
        self._vote(entity, det)
        entity.confidences.append(confidence)
        self.entities[entity.entity_id] = entity
        self._active.setdefault(det.category, []).append(entity)
        return entity

    def _update(self, entity: Entity, det: Detection, at: datetime, frame_id: int, confidence: float) -> TrackUpdate:
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
        _append_capped(current.frame_ids, frame_id)

        # people and vehicles may have left and come back while unobserved; a parcel does not wander off
        unobserved_gap = gap > self.visit_gap and entity.category not in _STATIC
        if is_new_visit or zone_changed or unobserved_gap:
            entity.zone_since = at
            entity.zone_frame_ids = []
        _append_capped(entity.zone_frame_ids, frame_id)

        entity.last_seen = at
        entity.last_zone = det.zone
        entity.sightings += 1
        entity.confidences.append(confidence)
        self._vote(entity, det)
        if len(det.display_name) > len(entity.display_name):
            entity.display_name = det.display_name  # keep the most specific description seen
        return TrackUpdate(entity, False, is_new_visit, zone_changed, entity.dwell_seconds, confidence)

    def _candidates(self, category: str, at: datetime) -> list[Entity]:
        """Entities of this category seen within the re-ID horizon (older ones are dropped)."""
        active = [e for e in self._active.get(category, []) if at - e.last_seen <= self.reid_horizon]
        self._active[category] = active
        return active

    def _vote(self, entity: Entity, det: Detection) -> None:
        traits = _vehicle_traits(det) if det.category == "vehicle" else _person_traits(det)
        traits["label"] = det.label
        traits["count"] = det.count
        for key, value in traits.items():
            if value is not None:
                entity.traits.setdefault(key, Counter())[value] += 1

    def _next_id(self, category: str) -> str:
        prefix = _PREFIX.get(category, "X")
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]:03d}"


# ------------------------------------------------------------------------------- helpers
def _vehicle_traits(det: Detection) -> dict:
    return {"color": det.color, "make": det.make, "model": det.model}


def _person_traits(det: Detection) -> dict:
    clothing = sorted(a for a in det.attributes if a in CLOTHING_WORDS)
    return {
        "color": det.color,
        "clothing": ",".join(clothing) or None,
        "masked": True if "masked" in det.attributes else None,  # absence of a mask is not evidence
    }


def _weighted_agreement(traits: dict, entity: Entity, weights) -> tuple[float, float]:
    """(agreement in [-1, 1], share of identity weight that both sides knew)."""
    score = known = 0.0
    total = sum(w for _, w in weights)
    for name, weight in weights:
        mine, theirs = traits.get(name), entity.trait(name)
        if mine is None or theirs is None:
            continue
        known += weight
        score += weight if _same(name, mine, theirs) else -2 * weight
    if not known:
        return 0.0, 0.0
    return max(-1.0, min(1.0, score / known)), known / total


def _same(name: str, mine, theirs) -> bool:
    if name == "clothing":  # "dark hoodie" vs "dark hoodie, backpack": any shared garment agrees
        return bool(set(str(mine).split(",")) & set(str(theirs).split(",")))
    return str(mine).lower() == str(theirs).lower()


def _labels_compatible(a: Optional[str], b: Optional[str]) -> bool:
    """A pickup can be called 'truck' or 'car' by a loose caption; a van is never a motorcycle."""
    if not a or not b or a == b:
        return True
    loose = {"car", "truck", "suv"}
    return a in loose and b in loose and "car" in (a, b)


def _append_capped(ids: list[int], frame_id: int) -> None:
    ids.append(frame_id)
    if len(ids) > MAX_EVIDENCE_FRAMES:
        del ids[1:-MAX_EVIDENCE_FRAMES + 1]  # keep the first frame (when it started) and the most recent ones


__all__ = ["EntityTracker", "Entity", "Visit", "TrackUpdate", "ACCEPT_SCORE"]
