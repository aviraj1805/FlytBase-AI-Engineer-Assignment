"""Scene-by-scene demo for the recorded walkthrough (`python -m drone_security demo`).

Each scene prints a banner that matches docs/DEMO_SCRIPT.md, so the voiceover can follow the
console. `--pause` waits for Enter between scenes; `--fast` skips the streaming delay.
Everything runs offline; with a Gemini key the Q&A scene uses the Gemini agent instead.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .agent import SecurityAnalyst
from .analysis import daily_briefing, summarize_video
from .cli import RESULTS, SEVERITY_STYLE, out, table
from .config import PropertyConfig
from .evaluation.benchmark import run_benchmark
from .evaluation.scenario_eval import evaluate_seeds
from .indexing import FrameIndex
from .memory import estimate_tokens, render_from_index, render_naive
from .pipeline import SecurityPipeline
from .scenario import generate_day, load_script

QUESTIONS = [
    "What objects were in the video?",
    "How many times did the blue Ford F150 enter today?",
    "When did it leave?",
    "Was the blue Chevrolet Silverado the same vehicle as the blue Ford F150?",
    "What happened between 23:00 and 23:30?",
    "What was the licence plate of the black SUV?",
]


class Demo:
    def __init__(self, config: PropertyConfig, db: Path, *, pause: bool = False, fast: bool = False, use_llm=None):
        self.config = config
        self.db = db
        self.pause = pause
        self.delay = 0.0 if fast else 0.03
        self.use_llm = use_llm

    def scene(self, number: int, title: str, say: str) -> None:
        if self.pause and number > 1:
            input("\n[Enter for the next scene] ")
        out("")
        out("=" * 78, style="bold cyan")
        out(f" SCENE {number}: {title}", style="bold cyan")
        out("=" * 78, style="bold cyan")
        out(say, style="italic")

    def run(self) -> None:
        script = load_script()
        day = generate_day(script, self.config, seed=7)
        truth = day.ground_truth

        self.scene(1, "Simulated drone feed: frames + telemetry",
                   "One scripted day at a logistics yard, generated from a seed. Each frame is a caption plus telemetry; "
                   "the ground truth is written to a separate file the system never reads.")
        out(f"{len(day.records)} frames, {len(script['episodes'])} scripted episodes, "
            f"{len(truth['expected_alerts'])} expected alert incidents, {len(truth['benign'])} benign traps")
        for r in day.records[:6]:
            t = r["telemetry"]
            out(f"  f{r['frame_id']:<3} {r['timestamp'][11:16]}  lat {t['lat']:.5f} lon {t['lon']:.5f} alt {t['altitude_m']:>4.1f} m  "
                f"bat {t['battery_pct']:>5.1f}%  \"{r['description']}\"", style="dim")

        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.db}{suffix}").unlink(missing_ok=True)
        index = FrameIndex(self.db)
        pipeline = SecurityPipeline(self.config, index=index)

        self.scene(2, "Real-time processing: logs and alerts (night shift, 00:00-03:45)",
                   "Every frame is parsed into objects, tracked, logged, checked against the YAML rules and indexed.")
        night = [r for r in day.records if r["timestamp"][11:16] < "03:45"]
        rest = [r for r in day.records if r["timestamp"][11:16] >= "03:45"]
        explained = 0

        def show(result) -> None:
            nonlocal explained
            for log in result.logs:
                out(f"  [{result.frame.timestamp:%H:%M}] LOG    {log.message}")
            for alert in result.alerts:
                out(f"  [{result.frame.timestamp:%H:%M}] ALERT  {alert.severity.name:<8} {alert.message}",
                    style=SEVERITY_STYLE[alert.severity.name])
                if explained < 3 and alert.severity.name in ("CRITICAL", "HIGH"):
                    explained += 1
                    out(f"           why: {alert.explanation}", style="dim")
                    out(f"           evidence frames: {alert.evidence_frame_ids}", style="dim")
            if result.logs or result.alerts:
                time.sleep(self.delay)

        pipeline.run(night, on_result=show)

        self.scene(3, "The rest of the day: alerts only",
                   "Cooldown and correlation keep it to one alert per incident step.")

        def alerts_only(result) -> None:
            for alert in result.alerts:
                out(f"  [{result.frame.timestamp:%H:%M}] {alert.severity.name:<8} {alert.rule_id:<19} {alert.message}",
                    style=SEVERITY_STYLE[alert.severity.name])
                time.sleep(self.delay)

        stats = pipeline.run(rest, on_result=alerts_only)
        total_alerts = len(index.alerts())
        out(f"\n{index.stats()['frames']} frames indexed; {total_alerts} alerts; "
            f"{pipeline.rules.raised_before_noise_control} raw rule firings before cooldown/correlation; "
            f"{stats.fps:,.0f} frames/s")

        self.scene(4, "Frame index: queries by object, time, zone, entity and free text",
                   "SQLite with B-tree indexes and FTS5; free text is hybrid keyword + vector search.")
        trucks = index.query_detections(label="truck", limit=8)
        table("show all truck events", ["time", "frame", "object", "action", "zone", "entity"],
              [[r["ts"][11:16], r["frame_id"], r["display_name"], r["action"], r["zone"], r["entity_id"]] for r in trucks])
        night_people = index.query_detections(category="person", zone="main_gate", start="00:00", end="06:00", limit=6)
        table("people at the main gate between 00:00 and 06:00", ["time", "frame", "object", "action"],
              [[r["ts"][11:16], r["frame_id"], r["display_name"], r["action"]] for r in night_people])
        from .retrieval import HybridRetriever

        hits = HybridRetriever(index).search("person climbing the fence", limit=5)
        table("free text: 'person climbing the fence'", ["time", "frame", "zone", "caption"],
              [[r["ts"][11:16], r["frame_id"], r["zone"], r["description"]] for r in hits])

        analyst = SecurityAnalyst(index, self.config, use_llm=self.use_llm)
        self.scene(5, f"Analyst agent Q&A ({analyst.mode})",
                   "Five tools, answers cite frames as [fN], follow-ups keep context, and it abstains without evidence.")
        for question in QUESTIONS:
            answer = analyst.ask(question)
            out(f"\nyou> {question}", style="bold")
            calls = ", ".join(f"{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})" for name, args in answer.tool_calls)
            out(f"  tools: {calls or '-'}", style="dim")
            for line in answer.text.splitlines()[:12]:
                out(f"  {line}")

        self.scene(6, "Summaries: one sentence, daily briefing, hierarchical context",
                   "Facts come from the index, so a summary cannot mention an event that did not happen.")
        out(summarize_video(index).one_sentence, style="bold")
        out("")
        out(daily_briefing(index, self.config))
        context = render_from_index(index, self.config)
        naive = render_naive(index, self.config)
        out(f"\nAgent context: hierarchical memory ~{estimate_tokens(context):,} tokens vs every frame pasted "
            f"~{estimate_tokens(naive):,} tokens ({estimate_tokens(naive) / estimate_tokens(context):.0f}x smaller)")

        self.scene(7, "Measured against ground truth",
                   "Five seeds = five different paraphrase/jitter draws of the same day.")
        result = evaluate_seeds(self.config, [1, 2, 3, 4, 5])
        table("mean over seeds 1-5", ["metric", "mean", "min", "max"],
              [[k, v["mean"], v["min"], v["max"]] for k, v in result["summary"].items()])
        self._recorded_results()

        self.scene(8, "Scalability: 10,000 frames live", "Full results for 10k-200k frames are in docs/results/benchmark.md.")
        bench = run_benchmark(self.config, sizes=[10_000], repeats=10)["rows"][0]
        out(f"10,000 frames ({bench['days']} days) ingested in {bench['ingest_s']} s = {bench['ingest_fps']:,} frames/s; "
            f"DB {bench['db_mb']} MB; context {bench['context_tokens_hierarchical']:,} vs naive {bench['context_tokens_naive']:,} tokens")
        table("query latency at 10k frames (ms)", ["query", "median", "p95"],
              [[q, v["p50_ms"], v["p95_ms"]] for q, v in bench["latency"].items()])
        index.close()

    def _recorded_results(self) -> None:
        """Results of the Gemini experiments, read from docs/results (they need an API key to re-run)."""
        for name, title in (("vlm_check.json", "VLM on real images"), ("qa_gemini_subset_eval.json", "Context experiment")):
            path = RESULTS / name
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            generated = data["provenance"]["generated_at"]
            if name == "vlm_check.json":
                s = data["summary"]["vlm"]
                out(f"{title} ({data['images']} images, {data['summary'].get('model')}, recorded {generated}): object recall "
                    f"{s['object_recall']}, attribute accuracy {s['attribute_accuracy']}, "
                    f"alert agreement {data['summary']['alert_agreement']}")
            else:
                for cond, r in data["results"].items():
                    out(f"{title} [{cond}] accuracy {r['accuracy']}, mean prompt tokens {r['mean_prompt_tokens']:,} "
                        f"(recorded {generated})")


__all__ = ["Demo", "QUESTIONS"]
