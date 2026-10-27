"""End-to-end: the demo day must reproduce the assignment's sample outputs and nothing noisy."""
from drone_security.simulator import build_frames, parse_text_frames


def all_logs(results):
    return [log.message for r in results for log in r.logs]


def all_alerts(results):
    return [a for r in results for a in r.alerts]


def test_expected_sample_log_truck_spotted_at_garage(demo_run):
    assert "Blue Ford F150 spotted at garage, 12:00." in all_logs(demo_run[1])


def test_expected_sample_alert_person_loitering_at_main_gate(demo_run):
    messages = [a.message for a in all_alerts(demo_run[1])]
    assert "Person in dark hoodie loitering at main gate, 00:01." in messages


def test_expected_repeat_vehicle_alert(demo_run):
    messages = [a.message for a in all_alerts(demo_run[1])]
    assert "Blue Ford F150 entered twice today (09:14, 12:00), 12:00." in messages


def test_alert_profile_of_demo_day(demo_run):
    stats = demo_run[2]
    assert stats.alerts_by_severity == {"HIGH": 4, "CRITICAL": 4, "MEDIUM": 7, "LOW": 2}
    assert stats.suppressed_alerts > 0  # cooldown is doing its job


def test_known_vehicle_never_alerts(demo_run):
    assert not [a for a in all_alerts(demo_run[1]) if "Camry" in a.message]
    assert "White Toyota Camry entered at driveway, 04:50 (known: Property manager)." in all_logs(demo_run[1])


def test_parked_truck_is_logged_once_per_visit_not_every_frame(demo_run):
    f150_logs = [m for m in all_logs(demo_run[1]) if m.startswith("Blue Ford F150")]
    assert len(f150_logs) == 7  # enter/park/leave x2 + enter/park on the third visit


def test_quiet_patrol_frames_produce_no_detections_or_security_alerts(demo_run):
    """Empty scenes stay silent; only flight-safety rules may fire on them (03:30 is scripted low battery)."""
    quiet = [r for r in demo_run[1] if r.frame.description.startswith(("Backyard clear", "Empty driveway"))]
    assert quiet and all(not r.detections for r in quiet)
    assert {a.rule_id for r in quiet for a in r.alerts} <= {"R-BATTERY", "R-GEOFENCE", "R-ALTITUDE", "R-WIND"}


def test_text_frame_format_from_the_brief(config, pipeline):
    """'Time: 00:01, Location: Gate, Frame 1: ...' lines work as input too."""
    spec = {"date": "2026-10-27", "frames": parse_text_frames(
        "Time: 00:01, Location: Gate, Frame 1: Person loitering at gate\n"
        "Time: 12:00, Location: Garage, Frame 2: Blue Ford F150 spotted at garage\n")}
    frames = build_frames(spec, config, background=False)
    results = [pipeline.process(f) for f in frames]
    assert [f.telemetry.zone for f in frames] == ["main_gate", "garage"]
    assert results[0].alerts[0].message == "Person loitering at main gate, 00:01."
    assert results[1].logs[0].message == "Blue Ford F150 spotted at garage, 12:00."
