"""Held-out perception test: the same day, every caption reworded by Gemini.

The lexicon parser was developed against the scenario script's wording, so its scores on
the script are optimistic. Here Gemini rewrites each distinct caption the way a different
security log writer (or a VLM) might describe the same scene, keeping the facts. Frame ids
and ground truth do not change, so both analyzers can be scored on unseen phrasing:

* rules analyzer on the reworded captions (offline once the rewrites exist),
* Gemini LLM analyzer on the reworded captions.

The rewrites are saved to data/eval/paraphrases.json, so the offline half of this experiment
re-runs without an API key and the test set stays fixed between runs.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ..config import PROJECT_ROOT, PropertyConfig
from ..scenario import generate_day, load_script
from .scenario_eval import evaluate_run, run_scenario

PARAPHRASES = PROJECT_ROOT / "data" / "eval" / "paraphrases.json"
PROMPT = """Rewrite each numbered security-drone caption the way a different person writing a security log would
describe the same scene. Keep every fact: colours, vehicle makes and models, number of people, clothing, carried
items, what is open, actions and places. Do not add facts and do not remove negations ("no one around").
Use different words and sentence structure from the original where you can. Return one rewrite per index.

{captions}"""


class Rewrite(BaseModel):
    index: int
    text: str


class Rewrites(BaseModel):
    rewrites: list[Rewrite]


def make_paraphrases(client, config: PropertyConfig, seed: int = 7, batch_size: int = 40) -> dict[str, str]:
    day = generate_day(load_script(), config, seed=seed)
    captions = list(dict.fromkeys(r["description"] for r in day.records))
    mapping: dict[str, str] = {}
    for i in range(0, len(captions), batch_size):
        batch = captions[i:i + batch_size]
        listing = "\n".join(f"{n}. {c}" for n, c in enumerate(batch))
        result = client.json(PROMPT.format(captions=listing), Rewrites, purpose="paraphrase")
        for item in result.rewrites:
            if 0 <= item.index < len(batch) and item.text.strip():
                mapping[batch[item.index]] = item.text.strip()
    PARAPHRASES.write_text(json.dumps({"seed": seed, "model": client.model, "rewrites": mapping}, indent=1),
                           encoding="utf-8")
    return mapping


def run_paraphrase_eval(config: PropertyConfig, analyzers: dict) -> dict:
    """analyzers: name -> factory() returning an analyzer (None = the rule parser)."""
    saved = json.loads(PARAPHRASES.read_text(encoding="utf-8"))
    seed, mapping = saved["seed"], saved["rewrites"]
    day = generate_day(load_script(), config, seed=seed)
    reworded = [{**r, "description": mapping.get(r["description"], r["description"])} for r in day.records]
    changed = sum(r["description"] != o["description"] for r, o in zip(reworded, day.records))
    results = {}
    for name, factory in analyzers.items():
        for feed_name, feed in (("original captions", day.records), ("reworded captions", reworded)):
            report = evaluate_run(run_scenario(config, seed=seed, analyzer=factory() if factory else None, records=feed))
            results[f"{name} | {feed_name}"] = {
                "alert_precision": report["alerts"]["precision"], "alert_recall": report["alerts"]["recall"],
                "alert_f1": report["alerts"]["f1"], "missed": report["alerts"]["missed"],
                "false_positives": report["alerts"]["false_positives"],
                "detection_precision": report["detections"]["precision"], "detection_recall": report["detections"]["recall"],
                "attribute_accuracy": report["detections"]["attribute_accuracy"],
                "reid_f1": report["reid"]["f1"], "f150_visits": report["reid"]["f150_visits"],
            }
    examples = [{"original": o, "reworded": n} for o, n in list(mapping.items())[:12] if o != n]
    return {"seed": seed, "paraphrase_model": saved.get("model"), "frames_reworded": changed, "frames": len(reworded),
            "results": results, "examples": examples}


def write_paraphrase_report(result: dict, out_dir: Path) -> Path:
    from .report import write_json

    write_json(result, out_dir, "paraphrase_eval", "python -m drone_security eval-paraphrase")
    lines = [
        "# Held-out wording: every caption reworded by Gemini",
        "",
        f"{result['frames_reworded']} of {result['frames']} frames reworded by `{result['paraphrase_model']}` "
        f"(data/eval/paraphrases.json); ground truth unchanged. Raw data: `paraphrase_eval.json`.",
        "",
        "| analyzer, captions | alert P | alert R | alert F1 | detection P | detection R | colour acc. | re-ID F1 | F150 visits |",
        "|---|---|---|---|---|---|---|---|---|",
        *[f"| {name} | {r['alert_precision']} | {r['alert_recall']} | {r['alert_f1']} | {r['detection_precision']} | "
          f"{r['detection_recall']} | {r['attribute_accuracy'].get('color', '-')} | {r['reid_f1']} | {r['f150_visits']} |"
          for name, r in result["results"].items()],
        "",
        "## Missed incidents on reworded captions",
        "",
        *[f"- {name}: {'; '.join(r['missed']) or 'none'}" for name, r in result["results"].items() if "reworded" in name],
        "",
        "## Example rewrites",
        "",
        *[f"- \"{e['original']}\" -> \"{e['reworded']}\"" for e in result["examples"]],
    ]
    path = out_dir / "paraphrase_eval.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = ["make_paraphrases", "run_paraphrase_eval", "write_paraphrase_report", "PARAPHRASES"]
