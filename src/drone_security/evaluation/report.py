"""Write experiment results to docs/results as raw JSON plus a readable Markdown table.

Every number quoted in the README and the report comes from one of these files, and each
file records when and how it was produced.
"""
from __future__ import annotations

import json
import platform
import sys
from datetime import datetime
from pathlib import Path


def provenance(command: str) -> dict:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "command": command,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
    }


def write_json(data: dict, out_dir: Path, name: str, command: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps({"provenance": provenance(command), **data}, indent=1, default=str), encoding="utf-8")
    return path


def write_scenario_report(result: dict, out_dir: Path, name: str = "eval_scenario") -> Path:
    analyzer = name.rsplit("_", 1)[-1]
    command = f"python -m drone_security eval --seeds {len(result['seeds'])} --analyzer {analyzer}"
    write_json(result, out_dir, name, command)
    run = result["runs"][-1]
    lines = [
        f"# Scenario evaluation ({analyzer} analyzer)",
        "",
        f"Produced by `{command}` on {datetime.now():%Y-%m-%d %H:%M}. Raw data: `{name}.json`.",
        "",
        f"Ground truth: {run['alerts']['incidents']} scripted alert incidents and per-frame object labels "
        f"(data/scenarios/day_script.yaml). Seeds change caption paraphrases, GPS jitter and wind.",
        "",
        "## Mean over seeds",
        "",
        "| metric | mean | min | max |",
        "|---|---|---|---|",
        *[f"| {k} | {v['mean']} | {v['min']} | {v['max']} |" for k, v in result["summary"].items()],
        "",
        f"## Per-rule results (seed {run['seed']})",
        "",
        "| rule | expected incidents | detected | alerts raised | false positives |",
        "|---|---|---|---|---|",
        *[f"| {r} | {v['expected']} | {v['detected']} | {v['raised']} | {v['false_positives']} |"
          for r, v in run["alerts"]["per_rule"].items()],
        "",
        "## Errors across all seeds",
        "",
    ]
    for r in result["runs"]:
        errors = r["alerts"]["false_positives"] + r["alerts"]["missed"]
        lines.append(f"- seed {r['seed']}: alert FP {len(r['alerts']['false_positives'])}, missed {len(r['alerts']['missed'])}"
                     + (": " + "; ".join(errors) if errors else "")
                     + f" | detection FP {r['detections']['false_positives']}, FN {r['detections']['false_negatives']}"
                     + f" | re-ID splits {r['reid']['splits'] or '-'}, merges {r['reid']['merges'] or '-'}")
    lines += ["", f"## Detection error examples (seed {run['seed']})", ""]
    lines += [f"- {e}" for e in run["detections"]["examples"]] or ["- none"]
    path = out_dir / f"{name}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = ["write_json", "write_scenario_report", "provenance"]
