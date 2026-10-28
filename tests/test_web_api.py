"""Web UI API: every endpoint answers from the real backend (processed day, live pipeline, saved results)."""
import json

import pytest
from fastapi.testclient import TestClient

from drone_security.web import create_app
from drone_security.web.api import _clean_sql, _humanize
from drone_security.web.state import AppState


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    state = AppState(db_path=tmp_path_factory.mktemp("ui") / "ui.db", use_llm=False)
    return TestClient(create_app(state))


def sse_events(text):
    events = []
    for block in text.strip().split("\n\n"):
        name = next(line[7:] for line in block.splitlines() if line.startswith("event: "))
        data = next(line[6:] for line in block.splitlines() if line.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


def test_status_reports_offline_mode_and_the_processed_day(client):
    s = client.get("/api/status").json()
    assert s["gemini"]["mode"] == "offline" and s["gemini"]["model"] is None
    assert s["index"]["frames"] == s["run"]["frames"] == 646
    assert s["run"]["alerts"] == sum(s["index"]["alerts"].values())


def test_property_has_the_zones_for_the_map(client):
    zones = {z["id"]: z for z in client.get("/api/property").json()["zones"]}
    assert zones["main_gate"]["perimeter"] and zones["warehouse"]["restricted"] and "garage" in zones


def test_timeline_has_every_frame_and_an_alert_marker_per_alert(client):
    t = client.get("/api/replay/timeline").json()
    assert len(t["frames"]) == 646
    assert len(t["markers"]) == len(client.get("/api/alerts").json()["alerts"])
    first = t["markers"][0]
    assert t["frames"][first["position"]]["frame_id"] == first["frame_id"]


def test_replay_stream_runs_the_pipeline_live_from_any_position(client):
    with client.stream("GET", "/api/replay/stream?start=66&speed=1000000") as r:
        text = ""
        for chunk in r.iter_text():
            text += chunk
            if text.count("event: frame") >= 2:
                break
    events = sse_events(text.rsplit("\n\n", 1)[0] if not text.endswith("\n\n") else text)
    name, snapshot = events[0]
    assert name == "snapshot" and snapshot["position"] == 66
    assert [a["frame_id"] for a in snapshot["alerts"]] == [1]  # only the 00:01 loitering alert happened before 02:14
    name, frame = events[1]
    assert name == "frame" and frame["frame"]["frame_id"] == 67
    assert frame["alerts"][0]["rule_id"] == "INTRUSION" and frame["alerts"][0]["severity_name"] == "CRITICAL"
    assert frame["frame"]["telemetry"]["battery_pct"] > 0 and frame["detections"][0]["category"] == "person"


def test_alert_filters(client):
    high = client.get("/api/alerts?min_severity=HIGH").json()["alerts"]
    assert high and all(a["severity"] >= 40 for a in high)
    assert all(a["rule_id"] == "LOITERING" for a in client.get("/api/alerts?rule_id=loitering").json()["alerts"])
    gate = client.get("/api/alerts?zone=main_gate").json()["alerts"]
    assert gate and all(a["zone"] == "main_gate" for a in gate)


def test_event_log_has_the_brief_style_lines_with_confidence(client):
    events = client.get("/api/events").json()["events"]
    line = next(e for e in events if e["message"] == "Blue Ford F150 spotted at garage, 09:16.")
    assert line["frame_id"] == 257 and line["entity_id"] == "V-002" and line["confidence"] > 0


def test_frame_detail_and_missing_frame(client):
    d = client.get("/api/frames/67").json()
    assert d["frame"]["zone"] == "back_fence" and d["detections"] and d["alerts_raised"][0]["rule_id"] == "INTRUSION"
    assert d["prev"] == 66 and d["next"] == 68
    assert client.get("/api/frames/999999").status_code == 404


def test_index_query_truck_preset_shows_the_sql_that_ran(client):
    r = client.get("/api/index/query", params={"object": "truck"}).json()
    assert r["count"] == 15 and {row["entity_id"] for row in r["rows"]} == {"V-002", "V-004"}
    assert any("FROM detections" in sql and "d.label = 'truck'" in sql for sql in r["sql"])


def test_index_query_main_gate_window_and_free_text(client):
    gate = client.get("/api/index/query", params={"zone": "main_gate", "start": "00:00", "end": "01:00"}).json()
    assert gate["rows"] and all(row["zone"] == "main_gate" and row["ts"][11:13] == "00" for row in gate["rows"])
    text = client.get("/api/index/query", params={"text": "person climbing the fence"}).json()
    assert {67, 80} <= {row["frame_id"] for row in text["rows"][:3]}
    assert any("frames_fts MATCH" in sql for sql in text["sql"]) and not any(sql.startswith("--") for sql in text["sql"])


def test_index_query_rejects_a_bad_time(client):
    r = client.get("/api/index/query", params={"start": "25:99"})
    assert r.status_code == 400 and "Invalid query" in r.json()["detail"]


def test_chat_streams_tool_calls_answer_and_grounding(client):
    client.post("/api/chat/reset")
    events = sse_events(client.post("/api/chat", json={"question": "Did the same truck enter twice?"}).text)
    names = [n for n, _ in events]
    assert names[0] == "status" and names[1] == "tools" and names[-1] == "done" and "token" in names
    tools, done = events[1][1], events[-1][1]
    assert tools["mode"] == "offline" and tools["calls"][0]["name"] == "entity_history"
    assert done["text"].startswith("Yes") and done["grounding"]["grounded"] and not done["grounding"]["unsupported"]
    follow = sse_events(client.post("/api/chat", json={"question": "When did it leave?"}).text)[-1][1]
    assert "09:42" in follow["text"] and "17:10" in follow["text"]


def test_chat_abstains_without_evidence_and_rejects_empty_questions(client):
    done = sse_events(client.post("/api/chat", json={"question": "What was the licence plate of the black SUV?"}).text)[-1][1]
    assert done["grounding"]["abstained"] and done["text"].startswith("Not enough evidence")
    assert client.post("/api/chat", json={"question": "  "}).status_code == 400


def test_entities_and_why_the_f150_was_matched(client):
    entities = {e["entity_id"]: e for e in client.get("/api/entities").json()["entities"]}
    assert entities["V-002"]["visit_count"] == 2 and entities["V-002"]["traits"]["make"] == "Ford"
    d = client.get("/api/entities/V-002").json()
    assert {t["trait"] for t in d["match"]["matched_on"]} == {"color", "make", "model"}
    assert len(d["sightings"]) == d["entity"]["sightings"] and any(a["rule_id"] == "REPEAT_VEHICLE" for a in d["alerts"])
    assert client.get("/api/entities/NOPE").status_code == 404


def test_summaries_have_every_level(client):
    d = client.get("/api/summaries").json()
    assert "[f67]" in d["one_sentence"] and d["briefing_by"] == "offline template"
    assert len(d["levels"]["day"]) == 1 and len(d["levels"]["hour"]) == 24 and d["levels"]["minute"]
    assert d["recommendations"][0]["priority"] == "P1"


def test_context_is_far_smaller_than_the_naive_prompt_and_respects_the_budget(client):
    small, large = client.get("/api/context?budget=800").json(), client.get("/api/context?budget=4000").json()
    assert small["hierarchical"]["tokens"] < large["hierarchical"]["tokens"] < large["naive"]["tokens"] / 5
    assert small["hierarchical"]["sections"][0]["title"] == "Property"


def test_saved_results_and_diagrams_are_served(client):
    results = client.get("/api/results").json()
    assert {"eval_scenario_rules", "benchmark", "ablation", "retrieval_eval"} <= set(results)
    names = client.get("/api/diagrams").json()["diagrams"]
    assert "architecture.svg" in names and client.get(f"/diagrams/{names[0]}").status_code == 200


def test_small_benchmark_runs_live(client):
    row = client.post("/api/benchmark?frames=500").json()["rows"][0]
    assert row["frames"] == 500 and row["ingest_fps"] > 0 and row["context_tokens_naive"] > row["context_tokens_hierarchical"]


def test_ui_page_and_static_assets_are_served(client):
    assert "<main id=\"view\"" in client.get("/").text
    assert client.get("/static/js/app.js").status_code == 200 and client.get("/static/vendor/chart.umd.js").status_code == 200
    assert client.get("/api/tests/last").json()["groups"] == []


def test_helpers_humanize_test_names_and_hide_sqlite_internals():
    assert _humanize("test_truck_logged_correctly") == "truck logged correctly"
    assert _humanize("test_x_y[seed-1]") == "x y [seed-1]"
    assert _clean_sql(["BEGIN", "-- SELECT pgno FROM x", "SELECT  1\n FROM t"]) == ["SELECT 1 FROM t"]


def test_badge_shows_the_gemini_model_or_why_it_fell_back(tmp_path, monkeypatch):
    import drone_security.gemini as gemini

    class FakeClient:
        model = "gemini-test-flash"

    monkeypatch.setattr(gemini, "GeminiClient", lambda *a, **k: FakeClient())
    state = AppState(db_path=tmp_path / "a.db", use_llm=True)
    assert state.gemini_status() == {"mode": "gemini", "model": "gemini-test-flash", "reason": None}

    def broken(*a, **k):
        raise RuntimeError("bad key")

    monkeypatch.setattr(gemini, "GeminiClient", broken)
    state = AppState(db_path=tmp_path / "b.db", use_llm=True)
    status = state.gemini_status()
    assert status["mode"] == "offline" and "RuntimeError" in status["reason"] and not state.use_llm
