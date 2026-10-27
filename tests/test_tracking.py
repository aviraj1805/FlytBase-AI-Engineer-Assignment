"""Re-identification and visit tracking."""
from datetime import datetime, timedelta

from drone_security.perception import RuleBasedAnalyzer
from drone_security.tracking import EntityTracker

parse = RuleBasedAnalyzer().parse
T0 = datetime(2026, 10, 27, 9, 0)


def see(tracker, minutes, caption, zone="main_gate", frame_id=0):
    det = parse(caption, default_zone=zone)[0]
    return tracker.observe(det, T0 + timedelta(minutes=minutes), frame_id)


def test_same_vehicle_is_one_entity_with_full_confidence():
    t = EntityTracker()
    a = see(t, 0, "Blue Ford F150 entering through the main gate")
    b = see(t, 2, "Blue F-150 pickup parked at the garage", zone="garage")
    assert a.entity is b.entity and b.confidence == 1.0


def test_less_detailed_sighting_shortly_after_joins_the_same_entity():
    t = EntityTracker()
    a = see(t, 0, "Blue Ford F150 parked at the garage", zone="garage")
    b = see(t, 3, "Blue pickup truck parked at the garage", zone="garage")
    assert a.entity is b.entity


def test_vague_description_after_the_vehicle_left_is_not_assumed_to_be_it():
    t = EntityTracker()
    f150 = see(t, 0, "Blue Ford F150 entering through the main gate")
    see(t, 20, "Blue Ford F150 leaving through the main gate")
    later = see(t, 120, "Blue pickup truck entering through the main gate")
    assert later.entity is not f150.entity  # colour + type is not enough evidence after a gap


def test_full_description_after_a_gap_is_recognised_as_a_return_visit():
    t = EntityTracker()
    see(t, 0, "Blue Ford F150 entering through the main gate")
    see(t, 20, "Blue Ford F150 leaving through the main gate")
    back = see(t, 450, "Blue Ford F150 pulls in at the main gate")
    assert not back.is_new_entity and back.is_new_visit and len(back.entity.visits) == 2


def test_conflicting_make_keeps_two_blue_pickups_apart():
    t = EntityTracker()
    f150 = see(t, 0, "Blue Ford F150 parked at the garage", zone="garage")
    chevy = see(t, 5, "Blue Chevrolet Silverado parked at the garage", zone="garage")
    assert chevy.entity is not f150.entity and chevy.is_new_entity


def test_ambiguous_match_reports_lower_confidence():
    t = EntityTracker()
    see(t, 0, "Blue Ford F150 parked at the garage", zone="garage")
    see(t, 1, "Blue Chevrolet Silverado parked at the garage", zone="garage")
    vague = see(t, 3, "Blue pickup truck parked at the garage", zone="garage")
    assert vague.confidence < 0.7  # both blue pickups are equally plausible


def test_people_with_different_clothing_colours_are_different():
    t = EntityTracker()
    a = see(t, 0, "Person in dark hoodie standing at the main gate")
    b = see(t, 2, "Person in gray hoodie standing at the main gate")
    assert a.entity is not b.entity


def test_anonymous_person_in_the_same_zone_moments_later_is_the_same_track():
    t = EntityTracker()
    a = see(t, 0, "Person in dark hoodie standing at the main gate")
    b = see(t, 2, "Person standing at the main gate")
    c = see(t, 3, "Person walking in the backyard", zone="backyard")
    assert a.entity is b.entity and c.entity is not a.entity


def test_groups_and_individuals_are_not_merged():
    t = EntityTracker()
    a = see(t, 0, "Two people walking toward the garage", zone="garage")
    b = see(t, 2, "Person walking at the garage", zone="garage")
    assert a.entity is not b.entity


def test_static_objects_match_by_label_and_place():
    t = EntityTracker()
    a = see(t, 0, "Black bag left by the main gate")
    b = see(t, 30, "Black bag still by the main gate")
    c = see(t, 31, "Package left at the front door", zone="front_door")
    assert a.entity is b.entity and c.entity is not a.entity
    assert b.dwell_seconds == 30 * 60


def test_dwell_resets_on_zone_change_and_after_an_unobserved_gap():
    t = EntityTracker()
    see(t, 0, "Person in dark hoodie standing at the main gate")
    assert see(t, 4, "Person in dark hoodie standing at the main gate").dwell_seconds == 240
    assert see(t, 6, "Person in dark hoodie walking in the driveway", zone="driveway").dwell_seconds == 0
    assert see(t, 40, "Person in dark hoodie walking in the driveway", zone="driveway").dwell_seconds == 0


def test_visit_evidence_frames_are_recorded():
    t = EntityTracker()
    see(t, 0, "Blue Ford F150 entering through the main gate", frame_id=10)
    upd = see(t, 2, "Blue Ford F150 parked at the garage", zone="garage", frame_id=11)
    assert upd.entity.visits[0].frame_ids == [10, 11] and upd.entity.zone_frame_ids == [11]


def test_entities_beyond_the_reid_horizon_are_not_matched():
    t = EntityTracker(reid_horizon=timedelta(days=1))
    a = see(t, 0, "Blue Ford F150 entering through the main gate")
    b = see(t, 3 * 24 * 60, "Blue Ford F150 entering through the main gate")
    assert a.entity is not b.entity
