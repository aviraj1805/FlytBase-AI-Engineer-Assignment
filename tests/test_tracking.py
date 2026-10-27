"""Re-identification, visit segmentation and dwell time."""
from datetime import datetime, timedelta

from drone_security.models import Detection
from drone_security.tracking import EntityTracker

T0 = datetime(2026, 10, 27, 9, 0)


def truck(action=None, zone="garage"):
    return Detection("vehicle", "truck", zone, color="blue", make="Ford", model="F150", action=action)


def person(zone="main_gate", clothing="hoodie", color="dark"):
    return Detection("person", "person", zone, color=color, attributes=[clothing] if clothing else [])


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def test_same_vehicle_is_one_entity():
    tracker = EntityTracker()
    first = tracker.observe(truck("entering"), at(0))
    second = tracker.observe(truck("parked"), at(2))
    assert first.is_new_entity and not second.is_new_entity
    assert first.entity.entity_id == second.entity.entity_id == "V-001"


def test_exit_then_return_counts_two_visits():
    tracker = EntityTracker()
    tracker.observe(truck("entering", "main_gate"), at(0))
    tracker.observe(truck("exiting"), at(25))
    update = tracker.observe(truck("parked"), at(180))
    assert update.is_new_visit and len(update.entity.visits) == 2


def test_sparse_patrol_sightings_without_exit_stay_one_visit():
    """A truck parked for an hour is seen on two patrol passes 40 min apart: still one visit."""
    tracker = EntityTracker()
    tracker.observe(truck("entering", "main_gate"), at(0))
    tracker.observe(truck("parked"), at(40))
    update = tracker.observe(truck("parked"), at(80))
    assert not update.is_new_visit and len(update.entity.visits) == 1


def test_entering_after_a_gap_starts_new_visit_even_if_exit_was_missed():
    tracker = EntityTracker()
    tracker.observe(truck("parked"), at(0))
    update = tracker.observe(truck("entering", "main_gate"), at(120))
    assert update.is_new_visit


def test_dwell_time_accumulates_while_continuously_observed():
    tracker = EntityTracker(visit_gap=timedelta(minutes=15))
    for minute in (0, 2, 4, 6):
        update = tracker.observe(person(), at(minute))
    assert update.dwell_seconds == 6 * 60


def test_dwell_resets_on_zone_change_and_after_unobserved_gap():
    tracker = EntityTracker(visit_gap=timedelta(minutes=15))
    tracker.observe(person(), at(0))
    assert tracker.observe(person(zone="driveway"), at(5)).dwell_seconds == 0
    assert tracker.observe(person(zone="driveway"), at(60)).dwell_seconds == 0  # not seen for 55 min


def test_different_clothing_means_different_people():
    tracker = EntityTracker()
    a = tracker.observe(person(clothing="hoodie"), at(0))
    b = tracker.observe(person(clothing="jacket", color="red"), at(1))
    assert a.entity.entity_id != b.entity.entity_id


def test_anonymous_people_are_kept_apart_by_zone():
    tracker = EntityTracker()
    a = tracker.observe(person(zone="main_gate", clothing=None, color=None), at(0))
    b = tracker.observe(person(zone="back_fence", clothing=None, color=None), at(0))
    assert a.entity.entity_id != b.entity.entity_id
