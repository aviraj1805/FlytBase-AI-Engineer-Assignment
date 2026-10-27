"""Scenario generator and end-to-end scoring against its ground truth."""
import json

from drone_security.evaluation import evaluate_run, run_scenario, score_alerts
from drone_security.scenario import generate_day, generate_frames, load_script, write_day


def test_generation_is_deterministic_per_seed(config):
    script = load_script()
    a, b = generate_day(script, config, seed=7), generate_day(script, config, seed=7)
    c = generate_day(script, config, seed=8)
    assert a.records == b.records and a.ground_truth == b.ground_truth
    assert [r["description"] for r in a.records] != [r["description"] for r in c.records]


def test_frames_carry_no_labels_and_truth_is_written_separately(config, tmp_path):
    day = generate_day(load_script(), config, seed=7)
    frames_path, truth_path = write_day(day, tmp_path)
    first = json.loads(frames_path.read_text().splitlines()[0])
    assert set(first) == {"frame_id", "timestamp", "description", "telemetry"}
    truth = json.loads(truth_path.read_text())
    assert len(truth["expected_alerts"]) == 20 and len(truth["frame_labels"]) == len(day.records)


def test_scripted_events_are_all_in_the_day(config):
    day = generate_day(load_script(), config, seed=7)
    labels = day.ground_truth["frame_labels"]
    f150_frames = [fid for fid, objs in labels.items() if any(o["entity"] == "f150" and o["action"] == "entering" for o in objs)]
    assert len(f150_frames) == 2  # the same blue Ford F150 enters twice
    by_time = {r["timestamp"][11:16]: r for r in day.records}
    assert "loitering" in by_time["00:01"]["description"]


def test_simulated_battery_crosses_the_thresholds_inside_the_truth_windows(config):
    day = generate_day(load_script(), config, seed=7)
    flying = [r for r in day.records if r["telemetry"]["mode"] != "docked"]
    first_low = next(r["timestamp"] for r in flying if r["telemetry"]["battery_pct"] <= 25)
    first_critical = next(r["timestamp"] for r in flying if r["telemetry"]["battery_pct"] <= 15)
    windows = {g["id"]: g for g in day.ground_truth["expected_alerts"]}
    assert windows["GT-11"]["from"] <= first_low <= windows["GT-11"]["to"]
    assert windows["GT-12"]["from"] <= first_critical <= windows["GT-12"]["to"]


def test_multi_day_stream_has_unique_ids_and_advancing_dates(config):
    records = list(generate_frames(1500, config))
    assert len(records) == 1500 and len({r["frame_id"] for r in records}) == 1500
    assert records[0]["timestamp"][:10] == "2026-10-27" and records[-1]["timestamp"][:10] == "2026-10-29"


def test_alerts_match_ground_truth_for_the_demo_seed(config):
    report = evaluate_run(run_scenario(config, seed=7))
    alerts = report["alerts"]
    assert alerts["recall"] == 1.0, alerts["missed"]
    assert alerts["precision"] == 1.0, alerts["false_positives"]


def test_the_f150_is_one_entity_with_two_visits_and_the_silverado_is_separate(config):
    report = evaluate_run(run_scenario(config, seed=7))
    reid = report["reid"]
    assert reid["f150_entities"] == 1 and reid["f150_visits"] == [2]
    assert not reid["merges"]  # no tracked entity mixes two real ones (e.g. F150 + Silverado)
    assert reid["precision"] >= 0.95 and reid["recall"] >= 0.95


def test_benign_situations_raise_no_security_alerts(scenario):
    _, pipeline, _ = scenario
    times = {a["ts"][11:16] for a in pipeline.index.alerts()}
    for benign in ("05:50", "05:56", "07:50", "10:05", "10:17", "11:00", "11:02", "12:30", "15:40", "15:42"):
        assert benign not in times


def test_scoring_counts_a_wrong_zone_as_a_false_positive_and_a_miss(scenario):
    day, _, results = scenario
    alerts = [a for r in results for a in r.alerts]
    loiter = next(a for a in alerts if a.rule_id == "LOITERING")
    loiter.zone = "backyard"
    report = score_alerts(alerts, day.ground_truth)
    assert any("GT-01" in m for m in report["missed"]) and report["precision"] < 1.0
    loiter.zone = "main_gate"
