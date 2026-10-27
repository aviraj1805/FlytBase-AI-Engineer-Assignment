"""Frame index: structured queries by time / object / zone / entity, full text, persistence."""
from drone_security.indexing import FrameIndex
from drone_security.pipeline import SecurityPipeline


def test_every_frame_is_indexed_with_telemetry(scenario):
    day, pipeline, _ = scenario
    stats = pipeline.index.stats()
    assert stats["frames"] == len(day.records)
    row = pipeline.index.query_frames(start="03:08", end="03:08")[0]
    assert row["battery_pct"] == 24.8 and row["gps_hdop"] is not None


def test_show_all_truck_events(scenario):
    _, pipeline, _ = scenario
    rows = pipeline.index.query_detections(label="truck")
    names = {r["display_name"] for r in rows}
    assert names >= {"Blue Ford F150", "Blue Chevrolet Silverado"}
    assert all(r["track_confidence"] is not None for r in rows)


def test_query_by_time_range_zone_make_and_entity(scenario):
    _, pipeline, _ = scenario
    morning = pipeline.index.query_detections(label="truck", start="09:00", end="10:00")
    assert morning and all("09:00" <= r["ts"][11:16] <= "10:00" for r in morning)
    gate = pipeline.index.query_detections(make="ford", model="f-150", zone="Main Gate")
    assert {r["ts"][11:16] for r in gate} >= {"09:14", "16:45"}
    entity = gate[0]["entity_id"]
    assert all(r["entity_id"] == entity for r in pipeline.index.query_detections(entity_id=entity))


def test_full_text_search_uses_enrichment_tags(scenario):
    _, pipeline, _ = scenario
    rows = pipeline.index.search("pickup")  # captions say "F150"; the tags add truck/pickup
    assert rows and any("F150" in r["description"] or "F-150" in r["description"] for r in rows)


def test_alerts_store_explanation_and_evidence(scenario):
    _, pipeline, _ = scenario
    [repeat] = pipeline.index.alerts(rule_id="repeat_vehicle")
    assert len(repeat["evidence_frame_ids"]) >= 2 and "visits today" in repeat["explanation"]
    assert pipeline.index.alerts(min_severity="CRITICAL")


def test_entities_persist_visits_with_frame_ids(scenario):
    _, pipeline, _ = scenario
    f150 = next(e for e in pipeline.index.entities(category="vehicle") if "F150" in e["display_name"])
    assert f150["visit_count"] == 2 and all(v["frame_ids"] for v in f150["visits"])
    assert 0 < f150["mean_confidence"] <= 1


def test_telemetry_near_and_extremes(scenario):
    _, pipeline, _ = scenario
    assert pipeline.index.telemetry_near("03:09")["ts"].endswith(("03:08:00", "03:10:00"))
    extremes = pipeline.index.telemetry_extremes()
    assert extremes["lowest_battery"]["battery_pct"] < 15
    assert extremes["furthest"]["distance_from_home_m"] > 260


def test_index_persists_to_disk(tmp_path, config, make_frame):
    db = tmp_path / "index.db"
    pipeline = SecurityPipeline(config, index=FrameIndex(db))
    pipeline.process(make_frame("09:14", "Blue Ford F150 entering through the main gate"))
    pipeline.index.close()
    reopened = FrameIndex(db)
    assert reopened.query_detections(label="truck")[0]["make"] == "Ford"


def test_reindexing_a_frame_replaces_its_detections(config, make_frame):
    pipeline = SecurityPipeline(config)
    result = pipeline.process(make_frame("09:14", "Blue Ford F150 entering through the main gate"))
    pipeline.index.add(result)  # e.g. the frame was re-captioned by a better VLM and indexed again
    assert len(pipeline.index.query_detections(label="truck")) == 1
