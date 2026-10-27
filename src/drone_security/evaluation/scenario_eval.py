"""Score a pipeline run against the scenario's ground truth.

Three questions, each answered with standard metrics:

* **Alerts** (event-based): is every scripted incident alerted (recall), and is every
  raised alert about a real incident (precision)? An alert matches an incident when its
  rule (or a rule folded into it by correlation) is accepted for that incident, its time
  falls in the incident window and, when the incident names a zone, the zone agrees.
  Several alerts for one incident are all "correct" but counted as redundant.
* **Detections**: per frame, predicted objects are paired with true objects of the same
  category and label; unpaired ones are false positives / negatives. Paired objects are
  checked attribute by attribute (colour, make, model, action) where the truth has a value.
* **Re-identification**: for every pair of correctly detected people/vehicles, did the
  tracker put them in the same entity exactly when they are the same true entity?
  (pairwise precision/recall, the usual clustering metric).
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

from ..config import PropertyConfig
from ..ingest import FeedSanitizer
from ..models import Alert, FrameResult
from ..pipeline import SecurityPipeline
from ..scenario import generate_day, load_script
from ..timeutil import from_ts, to_ts


@dataclass
class ScenarioRun:
    seed: int
    results: list[FrameResult]
    truth: dict
    pipeline: SecurityPipeline
    fps: float


def run_scenario(config: PropertyConfig, *, seed: int = 7, analyzer=None, script: Optional[dict] = None,
                 records: Optional[list[dict]] = None) -> ScenarioRun:
    day = generate_day(script or load_script(), config, seed=seed)
    feed = records if records is not None else day.records
    if hasattr(analyzer, "prefetch"):  # LLM analyzers: a few batched requests instead of one per caption
        analyzer.prefetch(list(FeedSanitizer(config).process(feed)))
    pipeline = SecurityPipeline(config, analyzer=analyzer)
    results: list[FrameResult] = []
    stats = pipeline.run(feed, on_result=results.append)
    return ScenarioRun(seed, results, day.ground_truth, pipeline, stats.fps)


# ----------------------------------------------------------------------------------- alerts
def score_alerts(alerts: list[Alert], truth: dict) -> dict:
    incidents = truth["expected_alerts"]
    matched_by_incident: dict[str, list[Alert]] = defaultdict(list)
    false_positives = []
    for alert in alerts:
        hits = [g for g in incidents if _alert_matches(alert, g)]
        for g in hits:
            matched_by_incident[g["id"]].append(alert)
        if not hits:
            false_positives.append(alert)

    detected = [g for g in incidents if matched_by_incident[g["id"]]]
    missed = [g for g in incidents if not matched_by_incident[g["id"]]]
    correct = len(alerts) - len(false_positives)
    delays = []
    for g in detected:
        first = min(a.timestamp for a in matched_by_incident[g["id"]])
        delays.append((first - from_ts(g["from"])).total_seconds() / 60)
    return {
        "incidents": len(incidents),
        "alerts": len(alerts),
        **prf(tp_pred=correct, n_pred=len(alerts), tp_true=len(detected), n_true=len(incidents)),
        "redundant_alerts": sum(max(len(v) - 1, 0) for v in matched_by_incident.values()),
        "mean_minutes_to_alert": round(statistics.mean(delays), 2) if delays else None,
        "false_positives": [f"{a.timestamp:%H:%M} {a.rule_id}: {a.message}" for a in false_positives],
        "missed": [f"{g['id']} {g['from'][11:16]} {'/'.join(g['rules'])}: {g['why']}" for g in missed],
        "per_rule": _per_rule(alerts, incidents, matched_by_incident, false_positives),
    }


def _alert_matches(alert: Alert, incident: dict) -> bool:
    rules = {alert.rule_id, *alert.related_rules}
    if not rules & set(incident["rules"]):
        return False
    if not incident["from"] <= to_ts(alert.timestamp) <= incident["to"]:
        return False
    return not incident.get("zone") or alert.zone == incident["zone"]


def _per_rule(alerts, incidents, matched_by_incident, false_positives) -> dict:
    rows = {}
    for rule in sorted({r for g in incidents for r in g["rules"]} | {a.rule_id for a in alerts}):
        expected = [g for g in incidents if rule in g["rules"]]
        rows[rule] = {
            "expected": len(expected),
            "detected": sum(1 for g in expected if matched_by_incident[g["id"]]),
            "raised": sum(1 for a in alerts if a.rule_id == rule),
            "false_positives": sum(1 for a in false_positives if a.rule_id == rule),
        }
    return rows


# ----------------------------------------------------------------------------------- detections
def score_detections(results: list[FrameResult], truth: dict) -> tuple[dict, list[tuple[str, str, str]]]:
    """Returns (metrics, paired) where paired = [(category, predicted entity id, true entity)]."""
    labels = truth["frame_labels"]
    tp = fp = fn = 0
    attr_hits: Counter = Counter()
    attr_total: Counter = Counter()
    paired: list[tuple[str, str, str]] = []
    errors: list[str] = []
    for result in results:
        expected = list(labels.get(str(result.frame.frame_id), []))
        for det in result.detections:
            match = next((g for g in expected if g["category"] == det.category and g["label"] == det.label), None)
            if match is None:
                fp += 1
                if len(errors) < 15:
                    errors.append(f"FP f{result.frame.frame_id} '{result.frame.description}': {det.category}/{det.label}")
                continue
            expected.remove(match)
            tp += 1
            for attr in ("color", "make", "model", "action"):
                if match.get(attr):
                    attr_total[attr] += 1
                    attr_hits[attr] += _norm(getattr(det, attr)) == _norm(match[attr])
            if det.category in ("person", "vehicle") and det.entity_id:
                paired.append((det.category, det.entity_id, match["entity"]))
        fn += len(expected)
        for g in expected:
            if len(errors) < 30:
                errors.append(f"FN f{result.frame.frame_id} '{result.frame.description}': {g['category']}/{g['label']}")
    metrics = {
        **prf(tp_pred=tp, n_pred=tp + fp, tp_true=tp, n_true=tp + fn),
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "attribute_accuracy": {a: round(attr_hits[a] / attr_total[a], 3) for a in attr_total},
        "examples": errors,
    }
    return metrics, paired


def _norm(value) -> str:
    return str(value or "").lower().replace("-", "").replace(" ", "")


# ----------------------------------------------------------------------------------- re-identification
def score_reid(paired: list[tuple[str, str, str]], pipeline: SecurityPipeline, truth: dict) -> dict:
    tp = fp = fn = 0
    for (cat_a, pred_a, true_a), (cat_b, pred_b, true_b) in combinations(paired, 2):
        same_pred, same_true = pred_a == pred_b, true_a == true_b
        tp += same_pred and same_true
        fp += same_pred and not same_true
        fn += same_true and not same_pred

    # cluster view: which predicted ids each true entity was split into, and vice versa
    by_true: dict[str, set] = defaultdict(set)
    by_pred: dict[str, set] = defaultdict(set)
    for _, pred, true in paired:
        by_true[true].add(pred)
        by_pred[pred].add(true)
    splits = {t: sorted(p) for t, p in by_true.items() if len(p) > 1}
    merges = {p: sorted(t) for p, t in by_pred.items() if len(t) > 1}

    # the headline case from the brief: is the F150 one entity with two visits?
    f150_ids = by_true.get("f150", set())
    f150_visits = [len(pipeline.tracker.get(e).visits) for e in f150_ids if pipeline.tracker.get(e)]
    rows = pipeline.index.query_detections(limit=1_000_000)
    confidences = [r["track_confidence"] for r in rows if r["category"] in ("person", "vehicle") and r["track_confidence"] is not None]
    pairs = tp + fp + fn
    return {
        **prf(tp_pred=tp, n_pred=tp + fp, tp_true=tp, n_true=tp + fn),
        "pairs_evaluated": pairs,
        "true_entities": len(by_true),
        "predicted_entities": len(by_pred),
        "splits": splits,
        "merges": merges,
        "f150_entities": len(f150_ids),
        "f150_visits": f150_visits,
        "mean_track_confidence": round(statistics.mean(confidences), 3) if confidences else None,
    }


# ----------------------------------------------------------------------------------- whole run
def evaluate_run(run: ScenarioRun) -> dict:
    alerts = [a for r in run.results for a in r.alerts]
    detections, paired = score_detections(run.results, run.truth)
    return {
        "seed": run.seed,
        "frames": len(run.results),
        "frames_per_second": round(run.fps),
        "alerts": score_alerts(alerts, run.truth),
        "detections": detections,
        "reid": score_reid(paired, run.pipeline, run.truth),
    }


def evaluate_seeds(config: PropertyConfig, seeds: list[int], analyzer_factory=None) -> dict:
    """Run every seed and report the mean and spread of the headline metrics."""
    runs = []
    for seed in seeds:
        analyzer = analyzer_factory() if analyzer_factory else None
        runs.append(evaluate_run(run_scenario(config, seed=seed, analyzer=analyzer)))
    headline = {
        "alert_precision": [r["alerts"]["precision"] for r in runs],
        "alert_recall": [r["alerts"]["recall"] for r in runs],
        "alert_f1": [r["alerts"]["f1"] for r in runs],
        "detection_precision": [r["detections"]["precision"] for r in runs],
        "detection_recall": [r["detections"]["recall"] for r in runs],
        "reid_precision": [r["reid"]["precision"] for r in runs],
        "reid_recall": [r["reid"]["recall"] for r in runs],
        "reid_f1": [r["reid"]["f1"] for r in runs],
    }
    summary = {k: {"mean": round(statistics.mean(v), 3), "min": round(min(v), 3), "max": round(max(v), 3)}
               for k, v in headline.items()}
    return {"seeds": seeds, "summary": summary, "runs": runs}


def prf(*, tp_pred: int, n_pred: int, tp_true: int, n_true: int) -> dict:
    """Precision over predictions, recall over truths (they can differ for event matching)."""
    precision = tp_pred / n_pred if n_pred else 1.0
    recall = tp_true / n_true if n_true else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


__all__ = ["run_scenario", "evaluate_run", "evaluate_seeds", "score_alerts", "score_detections", "score_reid", "prf"]
