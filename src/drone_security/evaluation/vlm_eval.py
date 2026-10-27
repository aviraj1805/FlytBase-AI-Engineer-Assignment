"""VLM check on real photographs (data/images): is the Gemini vision path genuinely useful?

Each image is sent to the Gemini VLM, which returns a caption and structured objects. The
image is then replayed as a drone frame (zone and time from manifest.json) through the
normal pipeline, so the same rules run on real-image output. Scored against hand labels:

* object recall: expected objects found (category + label), with attribute accuracy
  (colour, make, action) on the found ones;
* false alarms: categories that must not appear (e.g. a person in an empty lane);
* alert agreement: the expected rule fires (or nothing fires when none is expected).

The rule parser is also run on the VLM's own caption, which shows whether the cheap
caption-only path still works when captions come from a real VLM instead of the script.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import PROJECT_ROOT, PropertyConfig
from ..ingest import parse_record
from ..models import Detection, Frame
from ..perception import RuleBasedAnalyzer
from ..pipeline import SecurityPipeline

IMAGES_DIR = PROJECT_ROOT / "data" / "images"


class PrecomputedAnalyzer:
    """Returns detections computed earlier (by the VLM call) for each frame id."""

    name = "vlm:gemini"

    def __init__(self):
        self.detections: dict[int, list[Detection]] = {}

    def analyze(self, frame: Frame) -> list[Detection]:
        return self.detections.get(frame.frame_id, [])


def run_vlm_check(config: PropertyConfig, vision, images_dir: Path = IMAGES_DIR, client=None) -> dict:
    manifest = json.loads((images_dir / "manifest.json").read_text(encoding="utf-8"))
    precomputed = PrecomputedAnalyzer()
    pipeline = SecurityPipeline(config, analyzer=precomputed)
    parser = RuleBasedAnalyzer()
    rows = []
    for frame_id, item in enumerate(manifest["images"], start=1):
        image = (images_dir / item["file"]).read_bytes()
        started = time.perf_counter()
        caption, detections = vision.describe(image, "image/jpeg", item["zone"])
        latency = time.perf_counter() - started
        frame = _frame(frame_id, manifest["date"], item, caption, config)
        precomputed.detections[frame_id] = detections
        result = pipeline.process(frame)
        parsed = parser.analyze(frame)
        rows.append({
            "file": item["file"],
            "zone": item["zone"],
            "time": item["time"],
            "caption": caption,
            "latency_s": round(latency, 2),
            "vlm_objects": [_short(d) for d in detections],
            "vlm": _score(detections, item),
            "parser_on_vlm_caption": _score(parsed, item),
            "alerts": [a.rule_id for a in result.alerts],
            "expected_alert": item["alert"],
            "alert_ok": (item["alert"] in [a.rule_id for a in result.alerts]) if item["alert"] else not result.alerts,
        })
    summary = {name: _aggregate([r[name] for r in rows]) for name in ("vlm", "parser_on_vlm_caption")}
    summary["alert_agreement"] = round(sum(r["alert_ok"] for r in rows) / len(rows), 3)
    summary["mean_latency_s"] = round(sum(r["latency_s"] for r in rows) / len(rows), 2)
    if client is not None:
        summary["model"] = client.model
        summary["usage"] = client.usage.by_purpose.get("vlm")
    return {"images": len(rows), "summary": summary, "rows": rows}


def _frame(frame_id: int, day: str, item: dict, caption: str, config: PropertyConfig) -> Frame:
    zone = config.zone(item["zone"])
    record = {
        "frame_id": frame_id, "timestamp": f"{day} {item['time']}:00", "description": caption,
        "source": "vlm:gemini", "image_path": f"data/images/{item['file']}",
        "telemetry": {"lat": zone.lat, "lon": zone.lon, "altitude_m": 30, "battery_pct": 80, "mode": "patrol"},
    }
    return parse_record(record, config)


def _score(detections: list[Detection], item: dict) -> dict:
    found = attr_ok = attr_total = 0
    for expected in item["must"]:
        match = next((d for d in detections if d.category == expected["category"]
                      and ("label" not in expected or d.label == expected["label"])), None)
        if match is None:
            continue
        found += 1
        for key in ("color", "make", "action"):
            if key in expected:
                attr_total += 1
                attr_ok += (getattr(match, key) or "").lower() == expected[key].lower()
    violations = [c for c in item["must_not"] if any(d.category == c for d in detections)]
    vehicles = sum(d.count for d in detections if d.category == "vehicle")
    return {
        "expected": len(item["must"]), "found": found, "attributes_ok": attr_ok, "attributes_checked": attr_total,
        "false_alarms": violations, "min_vehicles_ok": vehicles >= item.get("min_vehicles", 0),
    }


def _aggregate(scores: list[dict]) -> dict:
    expected = sum(s["expected"] for s in scores)
    checked = sum(s["attributes_checked"] for s in scores)
    return {
        "object_recall": round(sum(s["found"] for s in scores) / expected, 3) if expected else None,
        "attribute_accuracy": round(sum(s["attributes_ok"] for s in scores) / checked, 3) if checked else None,
        "images_with_false_alarms": sum(bool(s["false_alarms"]) for s in scores),
        "vehicle_count_ok": sum(s["min_vehicles_ok"] for s in scores),
    }


def _short(d: Detection) -> str:
    parts = [d.display_name, d.action or "", f"x{d.count}" if d.count > 1 else ""]
    return " ".join(p for p in parts if p)


def write_vlm_report(result: dict, out_dir: Path) -> Path:
    from .report import write_json

    write_json(result, out_dir, "vlm_check", "python -m drone_security vlm")
    s = result["summary"]
    lines = [
        "# VLM check on real images",
        "",
        f"Produced by `python -m drone_security vlm`. Model: `{s.get('model')}`. Raw data: `vlm_check.json`.",
        "Images and licences: data/images/ATTRIBUTION.md; hand labels: data/images/manifest.json.",
        "",
        "| | object recall | attribute accuracy | images with false alarms |",
        "|---|---|---|---|",
        f"| Gemini VLM (structured output) | {s['vlm']['object_recall']} | {s['vlm']['attribute_accuracy']} | "
        f"{s['vlm']['images_with_false_alarms']} / {result['images']} |",
        f"| Rule parser on the VLM caption | {s['parser_on_vlm_caption']['object_recall']} | "
        f"{s['parser_on_vlm_caption']['attribute_accuracy']} | {s['parser_on_vlm_caption']['images_with_false_alarms']} / {result['images']} |",
        "",
        f"Expected alert behaviour matched on {s['alert_agreement'] * 100:.0f}% of images. "
        f"Mean VLM latency {s['mean_latency_s']} s per image.",
        "",
        "| image | zone, time | VLM caption | VLM objects | alerts (expected) |",
        "|---|---|---|---|---|",
    ]
    for r in result["rows"]:
        lines.append(f"| {r['file']} | {r['zone']} {r['time']} | {r['caption']} | {'; '.join(r['vlm_objects'])} | "
                     f"{', '.join(r['alerts']) or '-'} ({r['expected_alert'] or 'none'}) |")
    path = out_dir / "vlm_check.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = ["run_vlm_check", "write_vlm_report", "PrecomputedAnalyzer"]
