"""Messy feeds: malformed, duplicate, out-of-order and late frames, missing telemetry,
midnight rollover, an empty day, and the brief's plain-text frame format."""
from datetime import date

from drone_security.agent import NOT_ENOUGH_EVIDENCE, SecurityAnalyst
from drone_security.analysis import summarize_video
from drone_security.ingest import FeedSanitizer, parse_text_lines, read_records
from drone_security.pipeline import SecurityPipeline


def record(frame_id, hhmm, description, day="2026-10-27", with_telemetry=True, **telemetry):
    rec = {"frame_id": frame_id, "timestamp": f"{day} {hhmm}:00", "description": description}
    if with_telemetry:
        rec["telemetry"] = {"lat": 18.5215, "lon": 73.8567, "altitude_m": 30, "battery_pct": 80, **telemetry}
    return rec


def test_malformed_records_are_quarantined_with_a_reason(config):
    sanitizer = FeedSanitizer(config)
    bad = [
        "not a dict",
        {"timestamp": "2026-10-27 00:01:00", "description": "no id"},
        {"frame_id": "x", "timestamp": "2026-10-27 00:01:00", "description": "bad id"},
        {"frame_id": 3, "timestamp": "yesterday", "description": "bad time"},
        {"frame_id": 4, "timestamp": "2026-10-27 00:01:00", "description": "   "},
        {"frame_id": -1, "timestamp": "2026-10-27 00:01:00", "description": "negative id"},
    ]
    frames = list(sanitizer.process(bad + [record(10, "00:02", "Main gate quiet")]))
    assert [f.frame_id for f in frames] == [10]
    reasons = [reason for _, reason in sanitizer.stats.quarantined]
    assert len(reasons) == 6 and "missing caption" in reasons and "missing or unparseable timestamp" in reasons


def test_broken_telemetry_is_dropped_but_the_frame_is_kept(config):
    frames = list(FeedSanitizer(config).process([
        record(1, "00:01", "Person at the main gate", lat="north"),
        record(2, "00:02", "Person at the main gate", lat=123.0),  # impossible latitude
        {**record(3, "00:03", "Person at the main gate"), "telemetry": None},
    ]))
    assert [f.telemetry for f in frames] == [None, None, None]
    assert all(f.zone == "unknown" for f in frames)


def test_missing_telemetry_still_produces_detections_and_security_alerts(config):
    pipeline = SecurityPipeline(config)
    stats = pipeline.run([record(1, "00:01", "Person loitering at the main gate", with_telemetry=False)])
    assert stats.ingest.missing_telemetry == 1 and stats.alerts == 1
    row = pipeline.index.query_frames()[0]
    assert row["battery_pct"] is None and row["zone"] == "unknown"
    assert pipeline.index.query_detections(label="person")[0]["zone"] == "main_gate"  # zone read from the caption


def test_duplicate_frames_are_processed_once(config):
    pipeline = SecurityPipeline(config)
    rec = record(1, "09:14", "Blue Ford F150 entering through the main gate")
    same_content_new_id = {**rec, "frame_id": 99}
    stats = pipeline.run([rec, rec, same_content_new_id])
    assert stats.frames == 1 and stats.ingest.duplicates == 2
    assert pipeline.tracker.entities["V-001"].sightings == 1


def test_out_of_order_frames_are_reordered_within_the_buffer(config):
    pipeline = SecurityPipeline(config)
    order = []
    records = [record(i, f"00:0{i}", "Person in dark hoodie standing at the main gate") for i in (1, 3, 2, 5, 4, 6)]
    stats = pipeline.run(records, on_result=lambda r: order.append(r.frame.frame_id))
    assert order == [1, 2, 3, 4, 5, 6] and stats.late_frames == 0
    loiter = [a for a in pipeline.index.alerts() if a["rule_id"] == "LOITERING"]
    assert loiter and loiter[0]["ts"].endswith("00:06:00")  # dwell measured in true time order


def test_frames_later_than_the_buffer_are_indexed_but_not_tracked(config):
    pipeline = SecurityPipeline(config)
    records = [record(i, f"01:{i:02d}", "Backyard clear") for i in range(10, 30)]
    records.append(record(5, "01:05", "Masked man climbing the back fence"))  # 20 frames late
    stats = pipeline.run(records, reorder_window=4)
    assert stats.late_frames == 1 and stats.alerts == 0
    late = pipeline.index.query_frames(frame_ids=[5])[0]
    assert late["late"] == 1
    assert pipeline.index.search("masked fence")[0]["frame_id"] == 5  # still searchable


def test_midnight_rollover_keeps_dwell_and_resets_daily_visit_counts(config):
    pipeline = SecurityPipeline(config)
    stats = pipeline.run([
        record(1, "23:57", "Person in dark hoodie standing at the main gate"),
        record(2, "23:59", "Person in dark hoodie standing at the main gate"),
        record(3, "00:01", "Person in dark hoodie standing at the main gate", day="2026-10-28"),
        record(4, "00:03", "Person in dark hoodie standing at the main gate", day="2026-10-28"),
    ])
    loiter = [a for a in pipeline.index.alerts() if a["rule_id"] == "LOITERING"]
    assert stats.frames == 4 and loiter
    assert "for 6 min since 23:57" in loiter[0]["explanation"]  # dwell carries across midnight

    vehicles = SecurityPipeline(config)
    vehicles.run([
        record(1, "23:50", "Blue Ford F150 entering through the main gate"),
        record(2, "23:55", "Blue Ford F150 leaving through the main gate"),
        record(3, "08:00", "Blue Ford F150 entering through the main gate", day="2026-10-28"),
    ])
    assert vehicles.index.alerts(rule_id="REPEAT_VEHICLE") == []  # one visit per day: not a repeat


def test_empty_day(config):
    pipeline = SecurityPipeline(config)
    stats = pipeline.run([])
    assert stats.frames == 0 and stats.alerts == 0
    assert summarize_video(pipeline.index).one_sentence == "No footage has been processed yet."
    analyst = SecurityAnalyst(pipeline.index, config, use_llm=False)
    assert analyst.ask("What happened today?").text == NOT_ENOUGH_EVIDENCE
    assert analyst.ask("Show all truck events").text == NOT_ENOUGH_EVIDENCE


def test_brief_text_format_has_no_telemetry_and_uses_the_stated_location(config, tmp_path):
    path = tmp_path / "frames.txt"
    path.write_text("Time: 00:01, Location: Gate, Frame 1: Person loitering at gate\n"
                    "this line is garbage\n"
                    "Time: 12:00, Location: Garage, Frame 3: Blue Ford F150 spotted at garage\n")
    records = read_records(path, day=date(2026, 10, 27))
    pipeline = SecurityPipeline(config)
    logs, alerts = [], []
    stats = pipeline.run(records, on_result=lambda r: (logs.extend(r.logs), alerts.extend(r.alerts)))
    assert stats.frames == 2 and len(stats.ingest.quarantined) == 1 and stats.ingest.missing_telemetry == 2
    assert [a.message for a in alerts] == ["Person loitering at main gate, 00:01."]
    assert "Blue Ford F150 spotted at garage, 12:00." in [log.message for log in logs]


def test_text_lines_without_frame_numbers_are_numbered_by_line():
    [a, b] = parse_text_lines("Time: 09:14, Location: Gate, Blue truck at gate\nTime: 09:15, Location: Gate, Gate quiet",
                              date(2026, 10, 27))
    assert (a["frame_id"], b["frame_id"]) == (1, 2) and a["description"] == "Blue truck at gate"
