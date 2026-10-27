"""Frame-by-frame index: structured queries, full-text search, persistence."""
from drone_security.indexing import FrameIndex
from drone_security.pipeline import SecurityPipeline


def test_every_frame_is_indexed_with_telemetry(demo_run):
    pipeline, results, _ = demo_run
    frames = pipeline.index.query_frames(limit=1000)
    assert len(frames) == len(results)
    assert all(f["battery_pct"] is not None and f["zone"] for f in frames)


def test_show_all_truck_events(demo_run):
    index = demo_run[0].index
    rows = index.query_detections(label="truck")
    assert len(rows) == 7 and {r["display_name"] for r in rows} == {"Blue Ford F150"}
    assert [r["ts"][11:16] for r in rows][:3] == ["09:14", "09:16", "09:40"]


def test_query_by_time_range(demo_run):
    index = demo_run[0].index
    rows = index.query_detections(category="person", start="00:00", end="00:10")
    assert {r["ts"][11:16] for r in rows} == {"00:01", "00:03", "00:05", "00:07", "00:09"}


def test_query_by_zone_and_make_is_case_insensitive(demo_run):
    index = demo_run[0].index
    assert index.query_detections(zone="Main Gate", make="ford")
    assert index.query_detections(model="f-150")


def test_full_text_search_uses_enrichment_tags(demo_run):
    """'truck' matches a frame captioned 'Blue Ford F150 parked at garage' via the analyzer's tags."""
    rows = demo_run[0].index.search("truck at garage")
    assert any(r["description"] == "Blue Ford F150 parked at garage" for r in rows)


def test_full_text_falls_back_to_any_term(demo_run):
    rows = demo_run[0].index.search("masked xylophone")
    assert rows and "Masked" in rows[0]["description"]


def test_alerts_filtered_by_severity_and_time(demo_run):
    index = demo_run[0].index
    critical = index.alerts(min_severity="CRITICAL")
    assert len(critical) == 4
    assert all("23:00" <= a["ts"][11:16] for a in index.alerts(start="23:00"))


def test_telemetry_near_returns_closest_frame(demo_run):
    row = demo_run[0].index.telemetry_near("03:33")
    assert row["ts"].endswith("03:34:00") and row["battery_pct"] == 14


def test_entities_persist_visits(demo_run):
    [f150] = demo_run[0].index.entities(text="blue f150")
    assert f150["visit_count"] == 3 and [v["start"][11:16] for v in f150["visits"]] == ["09:14", "12:00", "16:45"]


def test_index_persists_to_disk(tmp_path, config, make_frame):
    db = tmp_path / "frames.db"
    pipeline = SecurityPipeline(config, index=FrameIndex(db))
    pipeline.process(make_frame("12:00", "Blue Ford F150 parked at garage", zone="garage"))
    pipeline.index.close()

    reopened = FrameIndex(db)
    assert reopened.query_detections(label="truck")[0]["display_name"] == "Blue Ford F150"
    assert reopened.search("ford")


def test_reindexing_a_frame_replaces_its_detections(config, make_frame):
    pipeline = SecurityPipeline(config)
    frame = make_frame("12:00", "Blue Ford F150 parked at garage", zone="garage")
    pipeline.process(frame)
    pipeline.process(frame)
    assert len(pipeline.index.query_detections(label="truck")) == 1
    assert len(pipeline.index.search("ford")) == 1
