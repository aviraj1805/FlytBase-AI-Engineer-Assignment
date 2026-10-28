"""The measurement code itself: grading, retrieval metrics, VLM scoring, ablations, paraphrases, benchmark."""
import json
from types import SimpleNamespace

from drone_security.evaluation import paraphrase_eval
from drone_security.evaluation.ablation import AttributeDropout, ExactSignatureTracker
from drone_security.evaluation.benchmark import run_benchmark
from drone_security.evaluation.qa_eval import grade, run_qa_eval
from drone_security.evaluation.retrieval_eval import relevant_frames, score_ranking
from drone_security.evaluation.vlm_eval import IMAGES_DIR, run_vlm_check
from drone_security.memory import HierarchicalMemory, gemini_summarizer
from drone_security.models import Detection
from drone_security.perception import RuleBasedAnalyzer
from drone_security.pipeline import SecurityPipeline


def test_grading_needs_all_patterns_and_one_of_any():
    q = {"all": ["SUV"], "any": ["23:1[0-2]"]}
    assert grade("A black SUV arrived at 23:10 [f623].", q)
    assert not grade("A black SUV arrived at 23:45.", q)
    assert grade("Not enough evidence in the footage to answer that.", {"abstain": True})
    assert not grade("Not enough evidence in the footage to answer that.", {"any": ["SUV"]})


def test_ranking_metrics():
    perfect = score_ranking([1, 2, 3], {1, 2, 3})
    assert perfect == {"hit_at_k": 1.0, "mrr": 1.0, "ndcg": 1.0}
    late = score_ranking([9, 8, 1], {1})
    assert late["mrr"] == 1 / 3 and late["hit_at_k"] == 1.0 and late["ndcg"] < 1.0


def test_relevance_comes_from_ground_truth_entities(scenario):
    day, _, _ = scenario
    climbing = relevant_frames({"entities": ["intruder"], "actions": ["climbing"]}, day.ground_truth)
    by_time = {r["frame_id"]: r["timestamp"][11:16] for r in day.records}
    assert sorted(by_time[f] for f in climbing) == ["02:14", "02:38"]


def test_offline_qa_condition_runs_and_never_cites_unseen_frames(config):
    result = run_qa_eval(config, conditions=["offline"])
    offline = result["results"]["offline"]
    assert offline["answers_with_unsupported_citations"] == 0
    assert result["context_sizes"]["naive"]["estimated_tokens"] > 10 * result["context_sizes"]["hierarchical"]["estimated_tokens"]


class FakeVision:
    """Pretends to be the VLM: returns a fixed caption per image and the parser's reading of it."""

    def __init__(self, captions_by_file):
        self.captions = {(IMAGES_DIR / name).read_bytes(): caption for name, caption in captions_by_file.items()}

    def describe(self, image, mime, zone):
        caption = self.captions[image]
        return caption, RuleBasedAnalyzer().parse(caption, default_zone=zone)


def test_vlm_check_scores_objects_false_alarms_and_alerts(config):
    captions = {
        "01_blue_f150_traffic.jpg": "Blue Ford F150 pickup, a red pickup truck and a dark sedan on the road",
        "02_parcel_doorstep.jpg": "Package left on a doorstep, no people visible",
        "03_fence_climbers.jpg": "Two men climbing over a fence, one carrying a black bag",
        "04_black_suv_driveway.jpg": "Black SUV parked in a driveway",
        "05_fox_lawn.jpg": "Fox standing on a lawn",
        "06_forklift_loading_dock.jpg": "Worker driving a forklift at a loading dock beside a container truck",
        "07_empty_lane.jpg": "Empty lane between hedges, no people or vehicles",
    }
    result = run_vlm_check(config, FakeVision(captions))
    assert result["images"] == 7
    assert result["summary"]["vlm"]["images_with_false_alarms"] == 0
    assert result["summary"]["alert_agreement"] == 1.0  # INTRUSION at 02:14, AFTER_HOURS_VEHICLE at 23:10, nothing else


def test_gemini_summarizer_writes_hour_and_day_levels(config, make_frame):
    calls = []
    fake = SimpleNamespace(text=lambda prompt, purpose: calls.append(purpose) or "Loitering at the gate [f1].")
    memory = HierarchicalMemory(config, summarizer=gemini_summarizer(fake))
    pipeline = SecurityPipeline(config, memory=memory)
    pipeline.run([make_frame("00:01", "Person loitering near main gate")])
    assert calls == ["summary_hour", "summary_day"]  # minute windows stay on the template
    assert memory.hours[0].text.startswith("00:00-01:00: Loitering at the gate [f1]")


def test_attribute_dropout_is_seeded_and_removes_identity_attributes(make_frame):
    frame = make_frame("09:14", "Blue Ford F150 entering through the main gate")
    a = AttributeDropout(RuleBasedAnalyzer(), 1.0, seed=3).analyze(frame)[0]
    assert (a.color, a.make, a.model, a.label) == (None, None, None, "truck")
    b1 = AttributeDropout(RuleBasedAnalyzer(), 0.5, seed=3).analyze(frame)[0]
    b2 = AttributeDropout(RuleBasedAnalyzer(), 0.5, seed=3).analyze(frame)[0]
    assert b1 == b2


def test_v1_tracker_splits_on_any_attribute_difference():
    from datetime import datetime

    tracker = ExactSignatureTracker()
    at = datetime(2026, 10, 27, 9, 0)
    full = Detection("vehicle", "truck", "garage", color="blue", make="Ford", model="F150")
    vague = Detection("vehicle", "truck", "garage", color="blue")
    assert tracker.observe(full, at).entity is not tracker.observe(vague, at).entity


def test_paraphrase_experiment_scores_reworded_captions(config, tmp_path, monkeypatch):
    path = tmp_path / "paraphrases.json"
    path.write_text(json.dumps({"seed": 7, "model": "fake", "rewrites": {
        "Main gate left open, no one around": "The main gate was left wide open; nobody nearby",
        "Main gate standing open, nobody nearby": "The main gate was left wide open; nobody nearby",
    }}))
    monkeypatch.setattr(paraphrase_eval, "PARAPHRASES", path)
    result = paraphrase_eval.run_paraphrase_eval(config, {"rules": None})
    assert result["frames_reworded"] >= 1
    assert set(result["results"]) == {"rules | original captions", "rules | reworded captions"}
    assert result["results"]["rules | original captions"]["alert_recall"] == 1.0


def test_benchmark_smoke():
    from drone_security.config import PropertyConfig

    result = run_benchmark(PropertyConfig.load(), sizes=[1500], repeats=2)
    row = result["rows"][0]
    assert row["frames"] == 1500 and row["ingest_fps"] > 0 and row["db_mb"] > 0
    assert row["context_tokens_naive"] > row["context_tokens_hierarchical"]
    assert set(row["latency"]) >= {"object: all truck detections", "hybrid text (BM25 + TF-IDF)"}
