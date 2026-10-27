"""Declarative rules (config/rules.yaml): loading, every rule, explanations, evidence, noise control."""
import pytest
import yaml

from drone_security.models import Severity
from drone_security.rules import RuleConfigError, load_rules


def alerts_for(pipeline, *frames):
    return [a for f in frames for a in pipeline.process(f).alerts]


def rule_ids(alerts):
    return [a.rule_id for a in alerts]


# ------------------------------------------------------------------ the rules file itself
def test_rules_file_loads_and_every_rule_is_explained(config):
    rules = load_rules()
    assert len(rules) == 14
    assert all(r.title and r.message and r.action for r in rules)


@pytest.mark.parametrize("bad, message", [
    ({"id": "X", "scope": "detection", "severity": "high", "message": "m", "when": {"colour": "red"}}, "unknown detection condition"),
    ({"id": "X", "scope": "telemetry", "severity": "high", "message": "m",
      "when": {"telemetry": [{"field": "battery_pct", "op": "~", "value": 1}]}}, "telemetry checks need"),
    ({"id": "X", "scope": "radar", "severity": "high", "message": "m", "when": {}}, "scope must be"),
    ({"id": "X", "scope": "detection", "message": "m", "when": {}}, "missing field"),
])
def test_invalid_rules_are_rejected_with_a_clear_message(tmp_path, bad, message):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"rules": [bad]}))
    with pytest.raises(RuleConfigError, match=message):
        load_rules(path)


# ------------------------------------------------------------------ security rules
def test_loitering_caption_matches_the_brief_example(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("00:01", "Person loitering near main gate"))
    assert alert.message == "Person loitering at main gate, 00:01."
    assert alert.rule_id == "LOITERING" and alert.severity == Severity.HIGH  # raised at night
    assert "AFTER_HOURS_PERSON" in alert.related_rules  # same person, same frame: folded by correlation
    assert "the caption describes loitering" in alert.explanation
    assert alert.evidence_frame_ids == [alert.frame_id]


def test_loitering_is_detected_from_measured_dwell_time(pipeline, make_frame):
    frames = [make_frame(t, "Person in dark hoodie standing at main gate") for t in ("00:01", "00:03", "00:05", "00:07")]
    alerts = alerts_for(pipeline, *frames)
    [loiter] = [a for a in alerts if a.rule_id == "LOITERING"]
    assert loiter.timestamp.strftime("%H:%M") == "00:07"
    assert "present at the main gate for 6 min since 00:01 across 4 frames (threshold 5 min)" in loiter.explanation
    assert loiter.evidence_frame_ids == [f.frame_id for f in frames]


def test_daytime_loitering_is_medium(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("14:00", "Person loitering near main gate"))
    assert alert.severity == Severity.MEDIUM


def test_uniformed_staff_at_night_is_not_an_alert(pipeline, make_frame):
    assert alerts_for(pipeline, make_frame("02:00", "Security guard in uniform at the main gate"),
                      make_frame("02:05", "Security guard in uniform at the warehouse", zone="warehouse")) == []


def test_intrusion_is_critical_and_new_access_point_is_a_new_alert(pipeline, make_frame):
    alerts = alerts_for(pipeline,
                        make_frame("02:14", "Masked man in black jacket climbing the back fence", zone="back_fence"),
                        make_frame("02:16", "Masked man in black jacket forcing the warehouse side door", zone="warehouse"))
    intrusions = [a for a in alerts if a.rule_id == "INTRUSION"]
    assert [a.zone for a in intrusions] == ["back_fence", "warehouse"]
    assert all(a.severity == Severity.CRITICAL for a in intrusions)


def test_restricted_zone_outside_business_hours_only(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("20:00", "Person walking at the warehouse", zone="warehouse"))
    assert alert.rule_id == "RESTRICTED_ZONE" and "the warehouse is a restricted zone" in alert.explanation
    assert alerts_for(pipeline, make_frame("12:00", "Two workers loading boxes at the warehouse", zone="warehouse")) == []


def test_repeat_vehicle_entry_cites_each_visit(pipeline, make_frame):
    first = make_frame("09:14", "Blue Ford F150 entering through main gate")
    alerts = alerts_for(pipeline, first,
                        make_frame("09:40", "Blue Ford F150 leaving through the main gate"),
                        make_frame("16:45", "Blue Ford F150 pulls in at the main gate"))
    [repeat] = [a for a in alerts if a.rule_id == "REPEAT_VEHICLE"]
    assert repeat.message == "Blue Ford F150 entered twice today (09:14, 16:45), 16:45."
    assert first.frame_id in repeat.evidence_frame_ids and repeat.frame_id in repeat.evidence_frame_ids
    assert "2 visits today at 09:14, 16:45" in repeat.explanation


def test_known_vehicle_never_alerts_even_at_night(pipeline, make_frame):
    assert alerts_for(pipeline, make_frame("04:50", "White Toyota Camry arrives in driveway", zone="driveway")) == []


def test_unknown_vehicle_at_night(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("23:10", "Black SUV with headlights off pulls into the driveway", zone="driveway"))
    assert alert.rule_id == "AFTER_HOURS_VEHICLE" and "not on the known-vehicle list" in alert.explanation


def test_unattended_object_needs_time_and_nobody_nearby(pipeline, make_frame):
    alerts = alerts_for(pipeline,
                        make_frame("14:32", "Person leaves a black bag by the main gate and walks away"),
                        make_frame("14:40", "Black bag lying at the main gate, nobody around"),
                        make_frame("14:50", "Black bag lying at the main gate, nobody around"))
    [alert] = [a for a in alerts if a.rule_id == "UNATTENDED_OBJECT"]
    assert alert.timestamp.strftime("%H:%M") == "14:50" and "for 18 min" in alert.message
    assert "no person is near it in this frame" in alert.explanation


def test_object_with_a_person_next_to_it_is_not_unattended(pipeline, make_frame):
    alerts = alerts_for(pipeline,
                        make_frame("10:05", "Package left at the front door", zone="front_door"),
                        make_frame("10:25", "Woman picks up the package at the front door", zone="front_door"))
    assert "UNATTENDED_OBJECT" not in rule_ids(alerts)


def test_gate_left_open_after_hours_reminds_only_every_four_hours(pipeline, make_frame):
    alerts = alerts_for(pipeline, *[make_frame(t, "Main gate left open, no one around") for t in ("22:45", "23:15", "23:45")])
    assert rule_ids(alerts) == ["ACCESS_OPEN"]
    assert alerts_for(pipeline, make_frame("12:00", "Main gate open, delivery expected")) == []


# ------------------------------------------------------------------ drone health
def test_battery_low_then_critical_escalates_through_cooldown(pipeline, make_frame):
    alerts = alerts_for(pipeline,
                        make_frame("03:08", "Backyard clear", zone="backyard", battery_pct=24),
                        make_frame("03:10", "Backyard clear", zone="backyard", battery_pct=23),
                        make_frame("03:34", "Front porch clear", zone="front_door", battery_pct=14))
    assert rule_ids(alerts) == ["BATTERY_LOW", "BATTERY_CRITICAL"]
    assert "battery 14% (<= 15)" in alerts[1].explanation


def test_docked_drone_never_raises_battery_alerts(pipeline, make_frame):
    assert alerts_for(pipeline, make_frame("03:40", "Drone landing on the dock", zone="dock", battery_pct=12, mode="docked")) == []


def test_geofence_threshold_is_read_from_the_property_config(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("03:00", "Field beyond the back fence", zone="back_fence",
                                              lat=18.51700, lon=73.85650))
    assert alert.rule_id == "GEOFENCE" and "(> 260)" in alert.explanation


def test_gps_drift_from_hdop(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("02:50", "Dark field, nothing visible", zone="back_fence", gps_hdop=4.8))
    assert alert.rule_id == "GPS_DRIFT" and "GPS HDOP 4.8 (> 3)" in alert.explanation


def test_gps_drift_from_an_impossible_jump_between_fixes(pipeline, make_frame, config):
    gate = config.zone("main_gate")
    a = make_frame("02:50:00", "Main gate quiet", lat=gate.lat, lon=gate.lon)
    b = make_frame("02:50:02", "Main gate quiet", lat=gate.lat + 0.0009, lon=gate.lon)  # ~100 m in 2 s
    [alert] = alerts_for(pipeline, a, b)
    assert alert.rule_id == "GPS_DRIFT" and alert.evidence_frame_ids == [a.frame_id, b.frame_id]
    assert "GPS fix moved at 50 m/s" in alert.explanation


def test_low_altitude_and_high_wind(pipeline, make_frame):
    alerts = alerts_for(pipeline, make_frame("13:00", "Backyard sprinklers", zone="backyard", altitude_m=8),
                        make_frame("15:10", "Backyard clear", zone="backyard", wind_mps=12.4))
    assert rule_ids(alerts) == ["LOW_ALTITUDE", "HIGH_WIND"]


def test_no_telemetry_means_no_drone_alerts_but_security_rules_still_run(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("02:14", "Masked man climbing the back fence", zone="back_fence", telemetry=None))
    assert alert.rule_id == "INTRUSION"


# ------------------------------------------------------------------ noise control
def test_cooldown_suppresses_repeats_then_allows_after_window(pipeline, make_frame):
    times = ("15:10", "15:12", "15:20", "15:26")
    alerts = alerts_for(pipeline, *[make_frame(t, "Backyard clear", zone="backyard", wind_mps=12.5) for t in times])
    assert [a.timestamp.strftime("%H:%M") for a in alerts] == ["15:10", "15:26"]
    assert pipeline.rules.suppressed == 2


def test_a_person_lingering_at_night_is_one_loitering_alert_not_a_stream(pipeline, make_frame):
    times = ("01:00", "01:05", "01:10", "01:20")
    alerts = alerts_for(pipeline, *[make_frame(t, "Person walking in the backyard", zone="backyard") for t in times])
    assert [(a.timestamp.strftime("%H:%M"), a.rule_id) for a in alerts] == [
        ("01:00", "AFTER_HOURS_PERSON"), ("01:05", "LOITERING"), ("01:20", "LOITERING")]
    assert alerts[-1].related_rules == ["AFTER_HOURS_PERSON"]  # folded by correlation, not a second alert


def test_every_alert_has_explanation_evidence_and_action(scenario):
    _, pipeline, results = scenario
    alerts = [a for r in results for a in r.alerts]
    assert alerts and all(a.explanation and a.evidence_frame_ids and a.recommended_action for a in alerts)
    assert all(a.frame_id in a.evidence_frame_ids for a in alerts)
