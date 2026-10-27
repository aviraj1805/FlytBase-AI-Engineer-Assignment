"""Alert rules, cooldown and correlation. Each test feeds frames through a fresh pipeline."""
from drone_security.models import Severity


def alerts_for(pipeline, *frames):
    return [a for f in frames for a in pipeline.process(f).alerts]


def rule_ids(alerts):
    return [a.rule_id for a in alerts]


# ------------------------------------------------------------------ night / perimeter
def test_alert_triggered_at_midnight_for_person_at_gate(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("00:01", "Person standing at the main gate"))
    assert alert.rule_id == "R-NIGHT-PERSON" and alert.severity == Severity.HIGH
    assert alert.message == "Person standing at main gate during night hours, 00:01."


def test_same_person_at_noon_raises_no_alert(pipeline, make_frame):
    assert alerts_for(pipeline, make_frame("12:00", "Person standing at the main gate")) == []


def test_uniformed_staff_at_night_is_not_an_intrusion(pipeline, make_frame):
    assert alerts_for(pipeline, make_frame("02:00", "Security guard in uniform at the main gate")) == []


def test_person_inside_property_at_night_is_medium(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("01:00", "Person walking in the backyard", zone="backyard"))
    assert alert.severity == Severity.MEDIUM


# ------------------------------------------------------------------ loitering
def test_loitering_caption_matches_expected_sample_output(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("00:01", "Person loitering near main gate"))
    assert alert.message == "Person loitering at main gate, 00:01."
    assert alert.severity == Severity.HIGH


def test_dwell_rule_detects_loitering_from_repeated_sightings(pipeline, make_frame):
    frames = [make_frame(t, "Person in dark hoodie standing at main gate") for t in ("00:01", "00:03", "00:05", "00:07")]
    alerts = alerts_for(pipeline, *frames)
    dwell = [a for a in alerts if a.rule_id == "R-DWELL"]
    assert len(dwell) == 1 and "for 6 min (since 00:01)" in dwell[0].message


def test_daytime_dwell_inside_work_areas_is_ignored(pipeline, make_frame):
    frames = [make_frame(t, "Worker in hi-vis vest standing at warehouse", zone="warehouse")
              for t in ("11:00", "11:04", "11:08")]
    assert "R-DWELL" not in rule_ids(alerts_for(pipeline, *frames))


# ------------------------------------------------------------------ intrusion
def test_climbing_fence_is_critical(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("02:14", "Masked man climbing the back fence", zone="back_fence"))
    assert alert.severity == Severity.CRITICAL and alert.rule_id == "R-SUSPICIOUS"


def test_restricted_zone_outside_business_hours(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("20:00", "Person walking at the warehouse", zone="warehouse"))
    assert alert.rule_id == "R-RESTRICTED" and alert.severity == Severity.HIGH


# ------------------------------------------------------------------ vehicles
def test_repeat_vehicle_entered_twice_today(pipeline, make_frame):
    alerts = alerts_for(
        pipeline,
        make_frame("09:14", "Blue Ford F150 entering through main gate"),
        make_frame("09:40", "Blue Ford F150 leaving the garage", zone="garage"),
        make_frame("12:00", "Blue Ford F150 parked at garage", zone="garage"),
    )
    [repeat] = [a for a in alerts if a.rule_id == "R-REPEAT-VEHICLE"]
    assert repeat.message == "Blue Ford F150 entered twice today (09:14, 12:00), 12:00."


def test_whitelisted_vehicle_at_night_is_silent(pipeline, make_frame):
    assert alerts_for(pipeline, make_frame("04:50", "White Toyota Camry arrives in driveway", zone="driveway")) == []


def test_unknown_vehicle_at_night_is_flagged(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("23:10", "Black SUV pulls into the driveway", zone="driveway"))
    assert alert.rule_id == "R-VEHICLE-NIGHT" and alert.severity == Severity.MEDIUM


def test_gate_left_open_after_hours_but_not_during_business_hours(pipeline, make_frame):
    assert rule_ids(alerts_for(pipeline, make_frame("22:45", "Main gate left open"))) == ["R-ACCESS-OPEN"]
    assert alerts_for(pipeline, make_frame("10:00", "Main gate left open")) == []


# ------------------------------------------------------------------ drone safety
def test_battery_low_then_critical_escalates(pipeline, make_frame):
    alerts = alerts_for(pipeline, make_frame("03:30", "Backyard clear", zone="backyard", battery_pct=22),
                        make_frame("03:32", "Backyard clear", zone="backyard", battery_pct=21),
                        make_frame("03:34", "Front porch clear", zone="front_door", battery_pct=14))
    assert [(a.rule_id, a.severity) for a in alerts] == [("R-BATTERY", Severity.MEDIUM), ("R-BATTERY", Severity.HIGH)]


def test_geofence_altitude_and_wind(pipeline, make_frame):
    geofence = alerts_for(pipeline, make_frame("03:10", "Field", lat=18.5170, lon=73.8565))
    altitude = alerts_for(pipeline, make_frame("13:00", "Sprinklers", zone="backyard", altitude_m=8))
    wind = alerts_for(pipeline, make_frame("15:10", "Returning", zone="garage", wind_mps=12.4))
    assert rule_ids(geofence) == ["R-GEOFENCE"] and rule_ids(altitude) == ["R-ALTITUDE"] and rule_ids(wind) == ["R-WIND"]


# ------------------------------------------------------------------ noise control
def test_cooldown_suppresses_repeats_then_allows_after_window(pipeline, make_frame):
    caption = "Person in red jacket loitering near main gate"
    first = alerts_for(pipeline, make_frame("00:01", caption))
    repeat = alerts_for(pipeline, make_frame("00:03", caption))
    later = alerts_for(pipeline, make_frame("00:40", caption))
    assert len(first) == 1 and repeat == [] and len(later) == 1
    assert pipeline.rules.suppressed >= 1


def test_correlation_folds_overlapping_rules_into_one_alert(pipeline, make_frame):
    [alert] = alerts_for(pipeline, make_frame("00:01", "Person loitering near main gate"))
    assert alert.rule_id == "R-LOITER" and alert.related_rules == ["R-NIGHT-PERSON"]


def test_every_alert_carries_a_recommended_action(pipeline, make_frame):
    alerts = alerts_for(pipeline, make_frame("02:14", "Masked man climbing the back fence", zone="back_fence"),
                        make_frame("03:34", "Front porch clear", zone="front_door", battery_pct=14))
    assert alerts and all(a.recommended_action for a in alerts)
