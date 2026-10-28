"""The assignment brief's own examples, end to end on the scenario day (named so the QA dashboard reads like the brief)."""


def test_truck_logged_correctly(scenario):
    # "Log each object/event (e.g., 'Blue Ford F150 spotted at garage, 12:00')"
    _, pipeline, results = scenario
    logs = [log.message for r in results for log in r.logs]
    assert "Blue Ford F150 entered at main gate, 09:14." in logs
    assert "Blue Ford F150 spotted at garage, 09:16." in logs
    trucks = pipeline.index.query_detections(label="truck")
    assert {t["entity_id"] for t in trucks if t["make"] == "Ford"} == {"V-002"}  # one identity, every sighting


def test_alert_triggered_at_midnight(scenario):
    # "Person loitering at the main gate at midnight" -> a real-time alert on that very frame
    _, _, results = scenario
    first = results[0]
    assert first.frame.timestamp.strftime("%H:%M") == "00:01"
    [alert] = first.alerts
    assert alert.rule_id == "LOITERING" and alert.severity.name == "HIGH"  # night raises the severity
    assert alert.message == "Person in dark hoodie loitering at main gate, 00:01."
    assert alert.evidence_frame_ids == [first.frame.frame_id] and alert.explanation


def test_same_truck_entering_twice_raises_a_repeat_alert(scenario):
    _, pipeline, _ = scenario
    [alert] = pipeline.index.alerts(rule_id="REPEAT_VEHICLE")
    assert alert["entity_id"] == "V-002" and alert["ts"][11:16] == "16:45"
    assert set(alert["evidence_frame_ids"]) >= {256, 454}
