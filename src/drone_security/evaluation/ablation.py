"""Offline ablations: what each design choice buys, measured against the ground truth.

1. Re-identification under degraded perception. Real VLM captions are often less specific
   than the script ("a blue pickup" instead of "a blue Ford F150"). `AttributeDropout`
   removes identity attributes (colour, make, model, clothing) from each detection with
   probability p. We compare the similarity-based tracker with the first version's
   exact-signature matcher (same attributes = same entity, nothing else).
2. Noise control. The same day with cooldown and correlation switched off: how many raw
   alerts an operator would get, and what that does to precision.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from ..config import PropertyConfig
from ..models import CLOTHING_WORDS, Detection, Frame
from ..perception import RuleBasedAnalyzer
from ..pipeline import SecurityPipeline
from ..rules import RuleEngine
from ..scenario import generate_day, load_script
from ..tracking import EntityTracker
from .scenario_eval import ScenarioRun, evaluate_run

DROPOUT_LEVELS = [0.0, 0.1, 0.25, 0.5]
SEEDS = [1, 2, 3, 4, 5]


class AttributeDropout:
    """Wraps an analyzer and forgets identity attributes at random (seeded)."""

    def __init__(self, analyzer, p: float, seed: int):
        self.analyzer = analyzer
        self.p = p
        self.rng = random.Random(seed)
        self.name = f"{analyzer.name}+dropout{p}"

    def analyze(self, frame: Frame) -> list[Detection]:
        out = []
        for det in self.analyzer.analyze(frame):
            changes = {key: None for key in ("color", "make", "model") if self.rng.random() < self.p}
            if self.rng.random() < self.p:
                changes["attributes"] = [a for a in det.attributes if a not in CLOTHING_WORDS]
            out.append(replace(det, **changes))
        return out


class ExactSignatureTracker(EntityTracker):
    """Baseline: the first version's matcher. Identical attribute signature = same entity."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._signatures: dict[str, str] = {}

    def similarity(self, det: Detection, entity, at) -> float:
        return 1.0 if self._signatures.get(entity.entity_id) == _v1_signature(det) else -1.0

    def _create(self, det, at, frame_id, confidence):
        entity = super()._create(det, at, frame_id, confidence)
        self._signatures[entity.entity_id] = _v1_signature(det)
        return entity


def _v1_signature(det: Detection) -> str:
    if det.category == "vehicle":
        return f"vehicle|{det.label}|{det.color}|{det.make}|{det.model}"
    if det.category == "person":
        clothing = ",".join(sorted(a for a in det.attributes if a in CLOTHING_WORDS))
        masked = "masked" in det.attributes
        if det.color or clothing:
            return f"person|{det.color}|{clothing}|{masked}|{det.count}"
        return f"person|anon|{det.zone}|{masked}|{det.count}"
    return f"{det.category}|{det.label}|{det.zone}"


def run_reid_ablation(config: PropertyConfig, levels: list[float] = DROPOUT_LEVELS, seeds: list[int] = SEEDS) -> dict:
    script = load_script()
    rows = []
    for p in levels:
        for tracker_name in ("similarity (this version)", "exact signature (v1)"):
            runs = []
            for seed in seeds:
                day = generate_day(script, config, seed=seed)
                tracking = config.tracking
                tracker_cls = EntityTracker if tracker_name.startswith("similarity") else ExactSignatureTracker
                tracker = tracker_cls(visit_gap=timedelta(minutes=tracking.visit_gap_minutes),
                                      max_open_gap=timedelta(hours=tracking.max_open_visit_hours))
                analyzer = AttributeDropout(RuleBasedAnalyzer(), p, seed)
                pipeline = SecurityPipeline(config, analyzer=analyzer, tracker=tracker)
                results = []
                pipeline.run(day.records, on_result=results.append)
                runs.append(evaluate_run(ScenarioRun(seed, results, day.ground_truth, pipeline, 0.0)))
            rows.append({
                "dropout": p,
                "tracker": tracker_name,
                "reid_precision": _mean(r["reid"]["precision"] for r in runs),
                "reid_recall": _mean(r["reid"]["recall"] for r in runs),
                "reid_f1": _mean(r["reid"]["f1"] for r in runs),
                "predicted_entities": _mean(r["reid"]["predicted_entities"] for r in runs),
                "true_entities": _mean(r["reid"]["true_entities"] for r in runs),
                "repeat_vehicle_recall": _mean(r["alerts"]["per_rule"]["REPEAT_VEHICLE"]["detected"] for r in runs),
                "repeat_vehicle_false_alerts": _mean(r["alerts"]["per_rule"]["REPEAT_VEHICLE"]["false_positives"] for r in runs),
                "alert_precision": _mean(r["alerts"]["precision"] for r in runs),
                "alert_recall": _mean(r["alerts"]["recall"] for r in runs),
            })
    return {"seeds": seeds, "levels": levels, "rows": rows}


def run_noise_ablation(config: PropertyConfig, seeds: list[int] = SEEDS) -> dict:
    script = load_script()
    rows = []
    for setting in ("cooldown + correlation", "cooldown only", "neither"):
        runs = []
        for seed in seeds:
            day = generate_day(script, config, seed=seed)
            engine = RuleEngine(config)
            if setting == "neither":
                engine.rules = [replace(r, cooldown=timedelta(0)) for r in engine.rules]
                engine._by_id = {r.id: r for r in engine.rules}
            engine.correlate = setting == "cooldown + correlation"
            pipeline = SecurityPipeline(config, rules=engine)
            results = []
            pipeline.run(day.records, on_result=results.append)
            runs.append(evaluate_run(ScenarioRun(seed, results, day.ground_truth, pipeline, 0.0)))
        rows.append({
            "setting": setting,
            "alerts_per_day": _mean(r["alerts"]["alerts"] for r in runs),
            "alerts_per_incident": _mean(r["alerts"]["alerts"] / r["alerts"]["incidents"] for r in runs),
            "precision": _mean(r["alerts"]["precision"] for r in runs),
            "recall": _mean(r["alerts"]["recall"] for r in runs),
            "redundant_alerts": _mean(r["alerts"]["redundant_alerts"] for r in runs),
        })
    return {"seeds": seeds, "rows": rows}


def _mean(values) -> float:
    return round(statistics.mean(values), 3)


def write_ablation_report(reid: dict, noise: dict, out_dir: Path) -> Path:
    from .report import write_json

    write_json({"reid": reid, "noise": noise}, out_dir, "ablation", "python -m drone_security ablation")
    lines = [
        "# Ablations (offline)",
        "",
        f"Produced by `python -m drone_security ablation`; mean over seeds {reid['seeds']}. Raw data: `ablation.json`.",
        "",
        "## Re-identification when perception loses detail",
        "",
        "Each identity attribute (colour, make, model, clothing) of every detection is dropped with probability p.",
        "",
        "| p | tracker | re-ID precision | re-ID recall | re-ID F1 | entities (true) | F150 repeat alert found | false repeat alerts | alert precision | alert recall |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *[f"| {r['dropout']} | {r['tracker']} | {r['reid_precision']} | {r['reid_recall']} | {r['reid_f1']} | "
          f"{r['predicted_entities']} ({r['true_entities']}) | {r['repeat_vehicle_recall']} | {r['repeat_vehicle_false_alerts']} | "
          f"{r['alert_precision']} | {r['alert_recall']} |" for r in reid["rows"]],
        "",
        "## Noise control (cooldown and correlation)",
        "",
        "| setting | alerts per day | alerts per incident | precision | recall | redundant alerts |",
        "|---|---|---|---|---|---|",
        *[f"| {r['setting']} | {r['alerts_per_day']} | {r['alerts_per_incident']} | {r['precision']} | {r['recall']} | "
          f"{r['redundant_alerts']} |" for r in noise["rows"]],
    ]
    path = out_dir / "ablation.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = ["AttributeDropout", "ExactSignatureTracker", "run_reid_ablation", "run_noise_ablation", "write_ablation_report"]
