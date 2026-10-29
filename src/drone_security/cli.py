"""Command-line interface: python -m drone_security <command> ...

  generate   write the seeded scenario day (frames.jsonl + ground_truth.json)
  run        process a feed as a live stream (logs + alerts), build the frame index
  query      structured / hybrid text queries over the index ("show all truck events")
  alerts     list alerts with their explanation and evidence frames
  ask        one question to the analyst agent;  chat: interactive Q&A with follow-ups
  summary    one-sentence video summary, daily briefing and recommendations
  context    print the agent's hierarchical working memory
  eval       score the pipeline against ground truth over several seeds
  eval-retrieval  keyword vs semantic vs hybrid search on labelled queries
  ablation   re-ID under degraded perception (vs the v1 matcher); noise control on/off
  eval-paraphrase  held-out wording: parser vs Gemini analyzer on Gemini-reworded captions
  eval-qa    context experiment: naive prompt vs hierarchical memory vs ablations
  vlm        Gemini VLM on the real images in data/images, scored against hand labels
  benchmark  scalability: 10k / 50k / 100k / 200k frames
  demo       scene-by-scene walkthrough of the system
  models     list the Gemini models this key can use and the ones chosen
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PROJECT_ROOT, PropertyConfig
from .models import FrameResult, Severity
from .scenario import DEFAULT_DAY_DIR

DEFAULT_DB = PROJECT_ROOT / "data" / "runs" / "latest.db"
DEFAULT_FEED = DEFAULT_DAY_DIR / "frames.jsonl"
RESULTS = PROJECT_ROOT / "docs" / "results"

try:
    from rich.console import Console
    from rich.table import Table

    _console = Console(highlight=False)
except ImportError:  # rich is optional; plain output works everywhere
    _console = None
    Table = None

SEVERITY_STYLE = {"CRITICAL": "bold white on red", "HIGH": "bold red", "MEDIUM": "yellow", "LOW": "cyan", "INFO": "dim"}


def out(text: str = "", style: str | None = None) -> None:
    if _console:
        _console.print(text, style=style, markup=False)
    else:
        print(text)


def heading(text: str) -> None:
    out(f"\n== {text} ==", style="bold")


def table(title: str, columns: list[str], rows: list[list]) -> None:
    if _console and Table:
        t = Table(title=title, show_lines=False, title_justify="left")
        for c in columns:
            t.add_column(c, overflow="fold")
        for r in rows:
            t.add_row(*[str(v) if v is not None else "" for v in r])
        _console.print(t)
        return
    heading(title)
    print(" | ".join(columns))
    for r in rows:
        print(" | ".join(str(v) if v is not None else "" for v in r))


# ----------------------------------------------------------------------------------------- commands
def cmd_generate(args) -> int:
    from .scenario import generate_day, load_script, write_day

    config = PropertyConfig.load(args.config)
    day = generate_day(load_script(args.script) if args.script else load_script(), config, seed=args.seed)
    frames_path, truth_path = write_day(day, args.out)
    out(f"{len(day.records)} frames -> {frames_path}")
    out(f"ground truth ({len(day.ground_truth['expected_alerts'])} expected alert incidents) -> {truth_path}")
    return 0


def cmd_run(args) -> int:
    from .analysis import recommend, summarize_video
    from .indexing import FrameIndex
    from .ingest import read_records
    from .pipeline import SecurityPipeline

    config = PropertyConfig.load(args.config)
    feed = Path(args.feed)
    if not feed.exists() and feed == DEFAULT_FEED:
        cmd_generate(argparse.Namespace(config=args.config, script=None, seed=7, out=DEFAULT_DAY_DIR))
    db = Path(args.db)
    reset_db(db)
    index = FrameIndex(db)
    analyzer = make_analyzer(args.analyzer, config)
    memory = None
    if args.gemini_summaries:
        from .gemini import GeminiClient
        from .memory import HierarchicalMemory, gemini_summarizer

        memory = HierarchicalMemory(config, index=index, summarizer=gemini_summarizer(GeminiClient()))
    pipeline = SecurityPipeline(config, analyzer=analyzer, index=index, memory=memory)
    records = read_records(feed)
    if hasattr(analyzer, "prefetch"):
        from .ingest import FeedSanitizer

        analyzer.prefetch(list(FeedSanitizer(config).process(records)))
    heading(f"Processing {len(records)} records from {feed.name} (analyzer: {analyzer.name})")

    def show(result: FrameResult) -> None:
        f = result.frame
        if args.verbose:
            t = f.telemetry
            tele = f"alt {t.altitude_m:>4.0f}m bat {t.battery_pct:>3.0f}%" if t else "no telemetry"
            out(f"[{f.timestamp:%H:%M}] f{f.frame_id:<4} {f.zone:<11} {tele}  \"{f.description}\"", style="dim")
        for log in result.logs:
            out(f"  LOG    {log.message}")
        for alert in result.alerts:
            out(f"  ALERT  {alert.severity.name:<8} {alert.rule_id:<19} {alert.message}", style=SEVERITY_STYLE[alert.severity.name])
            if args.explain:
                out(f"         why: {alert.explanation}  evidence: {alert.evidence_frame_ids}", style="dim")

    if args.speed:
        records = replay(records, args.speed)
    stats = pipeline.run(records, on_result=None if args.quiet else show)
    heading("Run statistics")
    by_sev = ", ".join(f"{k} {v}" for k, v in sorted(stats.alerts_by_severity.items(), key=lambda kv: -Severity[kv[0]]))
    out(f"{stats.frames} frames, {stats.detections} detections, {stats.logs} log entries, {stats.alerts} alerts ({by_sev})")
    out(f"noise control: {stats.raw_rule_firings} raw rule firings -> {stats.alerts} alerts "
        f"({stats.suppressed_alerts} suppressed by cooldown, the rest folded by correlation)")
    ingest = stats.ingest
    out(f"ingest: {ingest.duplicates} duplicates dropped, {len(ingest.quarantined)} malformed quarantined, "
        f"{ingest.missing_telemetry} without telemetry, {stats.late_frames} late")
    out(f"{stats.elapsed_s * 1000:.0f} ms total, {stats.fps:,.0f} frames/s; per-frame latency p50 "
        f"{stats.latency_ms(50):.2f} ms, p99 {stats.latency_ms(99):.2f} ms; index: {db}")
    if hasattr(analyzer, "stats"):
        out(f"Gemini analyzer: {analyzer.stats}")
    heading("Video summary")
    out(summarize_video(index).one_sentence)
    if not args.quiet:
        heading("Recommendations")
        for rec in recommend(index, config)[:8]:
            out(f"[{rec.priority}] {rec.text}")
    index.close()
    return 0


def cmd_query(args) -> int:
    index = open_index(args.db)
    if args.text:
        from .retrieval import HybridRetriever

        retriever = HybridRetriever(index, embedder=make_embedder(args.embedder, index), mode=args.mode)
        rows = retriever.search(args.text, start=args.start, end=args.end, zone=args.zone, limit=args.limit)
        table(f"{retriever.mode} search: '{args.text}' ({len(rows)} frames)", ["time", "frame", "zone", "caption"],
              [[r["ts"][11:16], r["frame_id"], r["zone"], r["description"]] for r in rows])
        return 0
    if not any([args.label, args.category, args.color, args.make, args.model, args.action, args.entity]):
        rows = index.query_frames(start=args.start, end=args.end, zone=args.zone, limit=args.limit)
        table(f"Frames ({len(rows)})", ["time", "frame", "zone", "battery %", "caption"],
              [[r["ts"][11:16], r["frame_id"], r["zone"], "" if r["battery_pct"] is None else f"{r['battery_pct']:.0f}",
                r["description"]] for r in rows])
        return 0
    rows = index.query_detections(label=args.label, category=args.category, color=args.color, make=args.make,
                                  model=args.model, zone=args.zone, action=args.action, entity_id=args.entity,
                                  start=args.start, end=args.end, limit=args.limit)
    table(f"Detections ({len(rows)})", ["time", "frame", "object", "action", "zone", "entity", "re-ID conf"],
          [[r["ts"][11:16], r["frame_id"], r["display_name"], r["action"], r["zone"], r["entity_id"],
            f"{r['track_confidence']:.2f}" if r["track_confidence"] is not None else ""] for r in rows])
    return 0


def cmd_alerts(args) -> int:
    index = open_index(args.db)
    rows = index.alerts(min_severity=args.min_severity, start=args.start, end=args.end, rule_id=args.rule)
    table(f"Alerts ({len(rows)})", ["time", "severity", "rule", "message", "evidence frames"],
          [[r["ts"][11:16], r["severity_name"], r["rule_id"], r["message"], r["evidence_frame_ids"]] for r in rows])
    if args.explain:
        for r in rows:
            out(f"{r['ts'][11:16]} {r['rule_id']}: {r['explanation']}", style="dim")
    return 0


def cmd_ask(args) -> int:
    analyst = make_analyst(args)
    print_answer(analyst.ask(" ".join(args.question)), analyst)
    return 0


def cmd_chat(args) -> int:
    analyst = make_analyst(args)
    out(f"Security analyst ({analyst.mode} mode). Ask about the footage; 'exit' to quit.", style="bold")
    while True:
        try:
            question = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"exit", "quit", "q"}:
            break
        if question:
            print_answer(analyst.ask(question), analyst)
    return 0


def cmd_summary(args) -> int:
    from .analysis import daily_briefing, recommend, summarize_video

    index = open_index(args.db)
    config = PropertyConfig.load(args.config)
    heading("One-sentence summary")
    out(summarize_video(index).one_sentence)
    llm = None
    if args.llm:
        from .gemini import GeminiClient

        llm = GeminiClient()
    heading(f"Daily security briefing ({'Gemini' if llm else 'offline template'})")
    out(daily_briefing(index, config, llm=llm))
    heading("Recommendations")
    for rec in recommend(index, config):
        out(f"[{rec.priority}] {rec.text}")
    return 0


def cmd_context(args) -> int:
    from .memory import estimate_tokens, render_from_index, render_naive

    index = open_index(args.db)
    config = PropertyConfig.load(args.config)
    text = render_from_index(index, config, args.budget)
    out(text)
    naive = render_naive(index, config)
    heading("Size")
    out(f"hierarchical context ~{estimate_tokens(text):,} tokens vs whole-video baseline ~{estimate_tokens(naive):,} tokens "
        f"(estimate: 4 characters per token)")
    return 0


def cmd_eval(args) -> int:
    from .evaluation.report import write_scenario_report
    from .evaluation.scenario_eval import evaluate_seeds

    config = PropertyConfig.load(args.config)
    seeds = list(range(1, args.seeds + 1))
    factory = None
    if args.analyzer == "gemini":
        factory = lambda: make_analyzer("gemini", config)  # noqa: E731
    result = evaluate_seeds(config, seeds, analyzer_factory=factory)
    path = write_scenario_report(result, RESULTS, name=f"eval_scenario_{args.analyzer}")
    heading(f"Scenario evaluation ({args.analyzer} analyzer, seeds {seeds[0]}-{seeds[-1]})")
    table("Mean over seeds (min-max)", ["metric", "mean", "min", "max"],
          [[k, v["mean"], v["min"], v["max"]] for k, v in result["summary"].items()])
    out(f"full results: {path}")
    return 0


def cmd_eval_retrieval(args) -> int:
    from .evaluation.retrieval_eval import run_retrieval_eval, write_retrieval_report

    config = PropertyConfig.load(args.config)
    embedders = {}
    if args.gemini:
        from .gemini import GeminiClient
        from .retrieval import GeminiEmbedder

        client = GeminiClient()
        embedders["gemini"] = lambda index: GeminiEmbedder(client, index)
    result = run_retrieval_eval(config, embedders)
    path = write_retrieval_report(result, RESULTS)
    table("Retrieval (k=10)", ["method", "hit@10", "MRR", "nDCG@10", "median ms"],
          [[name, r["overall"]["hit_at_k"], r["overall"]["mrr"], r["overall"]["ndcg"], r["median_latency_ms"]]
           for name, r in result["results"].items()])
    out(f"full results: {path}")
    return 0


def cmd_ablation(args) -> int:
    from .evaluation.ablation import run_noise_ablation, run_reid_ablation, write_ablation_report

    config = PropertyConfig.load(args.config)
    path = write_ablation_report(run_reid_ablation(config), run_noise_ablation(config), RESULTS)
    out(path.read_text(encoding="utf-8"))
    return 0


def cmd_eval_paraphrase(args) -> int:
    from .evaluation.paraphrase_eval import PARAPHRASES, make_paraphrases, run_paraphrase_eval, write_paraphrase_report

    config = PropertyConfig.load(args.config)
    if args.make:
        from .gemini import GeminiClient

        mapping = make_paraphrases(GeminiClient(), config)
        out(f"{len(mapping)} captions reworded -> {PARAPHRASES}")
    if not PARAPHRASES.exists():
        sys.exit(f"No reworded captions yet ({PARAPHRASES}); create them once with --make (needs GEMINI_API_KEY).")
    analyzers = {"rules": None}
    if args.gemini:
        analyzers["gemini"] = lambda: make_analyzer("gemini", config)
    path = write_paraphrase_report(run_paraphrase_eval(config, analyzers), RESULTS)
    out(path.read_text(encoding="utf-8"))
    return 0


def cmd_eval_qa(args) -> int:
    from .evaluation.qa_eval import HELDOUT, QUESTIONS, run_qa_eval, write_qa_report

    config = PropertyConfig.load(args.config)
    gemini = None
    if args.gemini:
        from .gemini import GeminiClient

        gemini = GeminiClient()
    questions = Path(args.questions) if args.questions else HELDOUT if args.heldout else QUESTIONS
    result = run_qa_eval(config, gemini=gemini, conditions=args.conditions, pause_s=args.pause, questions_path=questions)
    name = f"{questions.stem}_eval" if args.questions else "qa_heldout_eval" if args.heldout else "qa_context_eval"
    path = write_qa_report(result, RESULTS, name=name)
    table("Answers", ["condition", "accuracy", "abstention", "mean prompt tokens", "mean latency s", "made-up citations"],
          [[c, r["accuracy"], r["abstention_accuracy"], r["mean_prompt_tokens"], r["mean_latency_s"],
            r["answers_with_unsupported_citations"]] for c, r in result["results"].items()])
    out(f"full results: {path}")
    return 0


def cmd_vlm(args) -> int:
    from .evaluation.vlm_eval import run_vlm_check, write_vlm_report
    from .gemini import GeminiClient
    from .perception.gemini_analyzer import GeminiVisionAnalyzer

    config = PropertyConfig.load(args.config)
    client = GeminiClient()
    def show(r: dict) -> None:
        out(f"{r['file']} [{r['zone']} {r['time']}] {r['latency_s']} s", style="bold")
        out(f"  caption: {r['caption']}")
        out(f"  objects: {'; '.join(r['vlm_objects']) or '-'}")
        out(f"  alerts:  {', '.join(r['alerts']) or '-'} (expected {r['expected_alert'] or 'none'})")

    out(f"model: {client.model}")
    result = run_vlm_check(config, GeminiVisionAnalyzer(config, client), client=client, on_row=show)
    path = write_vlm_report(result, RESULTS)
    out(json.dumps(result["summary"], indent=1))
    out(f"full results: {path}")
    return 0


def cmd_benchmark(args) -> int:
    from .evaluation.benchmark import run_benchmark, write_benchmark_report

    config = PropertyConfig.load(args.config)
    heading(f"Scalability benchmark: {', '.join(f'{n:,}' for n in args.frames)} frames")
    result = run_benchmark(config, sizes=args.frames, repeats=args.repeats)
    path = write_benchmark_report(result, RESULTS)
    out(path.read_text(encoding="utf-8"))
    return 0


def cmd_demo(args) -> int:
    from .demo import Demo

    Demo(PropertyConfig.load(args.config), Path(args.db), pause=args.pause, fast=args.fast,
         use_llm=False if args.offline else None).run()
    return 0


def cmd_models(args) -> int:
    from .gemini import GeminiClient, record_model_choice

    client = GeminiClient(model="pending", embed_model="pending").client
    record = record_model_choice(client, RESULTS / "gemini_models.json")
    table("Gemini models available to this key", ["model", "actions", "input tokens"],
          [[m["name"], ", ".join(m["actions"]), m["input_token_limit"]] for m in record["available"]])
    out(f"chosen for generation: {record['generation_model']}; for embeddings: {record['embedding_model']}")
    return 0


# ----------------------------------------------------------------------------------------- helpers
def replay(records: list[dict], speed: float):
    """Yield records with real gaps between their timestamps, divided by `speed` (60 = a minute per second)."""
    import time
    from datetime import datetime

    previous = None
    for record in records:
        try:
            at = datetime.strptime(str(record.get("timestamp", ""))[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            at = None
        if previous and at and at > previous:
            time.sleep(min((at - previous).total_seconds() / speed, 2.0))  # never stall more than 2 s
        previous = at or previous
        yield record


def reset_db(db: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)


def open_index(db: str):
    from .indexing import FrameIndex

    if not Path(db).exists():
        sys.exit(f"No index at {db}. Run `python -m drone_security run` first.")
    return FrameIndex(db)


def make_analyzer(name: str, config: PropertyConfig):
    if name == "gemini":
        from .gemini import gemini_available
        from .perception.gemini_analyzer import GeminiFrameAnalyzer

        if not gemini_available():
            sys.exit("--analyzer gemini needs GEMINI_API_KEY (environment or .env file).")
        return GeminiFrameAnalyzer(config)
    from .perception import RuleBasedAnalyzer

    return RuleBasedAnalyzer()


def make_embedder(name: str, index):
    from .retrieval import TfidfEmbedder

    if name == "gemini":
        from .gemini import GeminiClient
        from .retrieval import GeminiEmbedder

        return GeminiEmbedder(GeminiClient(), index)
    return TfidfEmbedder()


def make_analyst(args):
    from .agent import SecurityAnalyst

    use_llm = False if args.offline else None
    analyst = SecurityAnalyst(open_index(args.db), PropertyConfig.load(args.config), use_llm=use_llm)
    if analyst.llm_error:
        out(analyst.llm_error, style="yellow")
    return analyst


def print_answer(answer, analyst) -> None:
    if answer.tool_calls:
        calls = ", ".join(f"{name}({', '.join(f'{k}={v!r}' for k, v in a.items())})" for name, a in answer.tool_calls)
        out(f"[{answer.mode}] tools: {calls}", style="dim")
    if answer.mode == "offline" and analyst.llm_error and analyst.llm_agent:
        out(f"(Gemini unavailable: {analyst.llm_error})", style="yellow")
    out(answer.text)
    grounding = getattr(analyst.llm_agent, "last_grounding", None) if answer.mode == "gemini" else None
    if grounding:
        out(f"citations: {grounding['cited']} unsupported: {grounding['unsupported']}", style="dim")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m drone_security", description="Drone Security Analyst Agent")
    parser.add_argument("--config", default=None, help="property config JSON (default: config/property.json)")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"frame index database (default: {DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="write the seeded scenario day")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--script", default=None, help="day script YAML (default: data/scenarios/day_script.yaml)")
    p.add_argument("--out", default=str(DEFAULT_DAY_DIR))
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("run", help="process a feed as a live stream")
    p.add_argument("--feed", default=str(DEFAULT_FEED), help=".jsonl records or the brief's .txt line format")
    p.add_argument("--analyzer", choices=["rules", "gemini"], default="rules")
    p.add_argument("--gemini-summaries", action="store_true", help="Gemini writes the hour and day summaries")
    p.add_argument("--speed", type=float, default=0, help="replay as a live feed: 60 = one footage minute per second")
    p.add_argument("--verbose", "-v", action="store_true", help="also print every frame with its telemetry")
    p.add_argument("--explain", action="store_true", help="print each alert's explanation and evidence")
    p.add_argument("--quiet", "-q", action="store_true", help="only print the final statistics")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("query", help="query the frame index")
    for flag in ("label", "category", "color", "make", "model", "zone", "action", "entity", "text"):
        p.add_argument(f"--{flag}")
    p.add_argument("--mode", choices=["keyword", "semantic", "hybrid"], default="hybrid", help="text search mode")
    p.add_argument("--embedder", choices=["tfidf", "gemini"], default="tfidf")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("alerts", help="list alerts")
    p.add_argument("--min-severity", choices=[s.name for s in Severity])
    p.add_argument("--rule")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--explain", action="store_true")
    p.set_defaults(func=cmd_alerts)

    for name, func in (("ask", cmd_ask), ("chat", cmd_chat)):
        p = sub.add_parser(name, help="ask the analyst agent" if name == "ask" else "interactive Q&A")
        if name == "ask":
            p.add_argument("question", nargs="+")
        p.add_argument("--offline", action="store_true", help="use the offline analyst even if a Gemini key is set")
        p.set_defaults(func=func)

    p = sub.add_parser("summary", help="video summary, daily briefing, recommendations")
    p.add_argument("--llm", action="store_true", help="have Gemini write the briefing from the same facts")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("context", help="print the agent's hierarchical working memory")
    p.add_argument("--budget", type=int, default=2500, help="token budget (approximate)")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("eval", help="score against ground truth over several seeds")
    p.add_argument("--seeds", type=int, default=10)
    p.add_argument("--analyzer", choices=["rules", "gemini"], default="rules")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("eval-retrieval", help="keyword vs semantic vs hybrid retrieval on labelled queries")
    p.add_argument("--gemini", action="store_true", help="also evaluate Gemini embeddings")
    p.set_defaults(func=cmd_eval_retrieval)

    p = sub.add_parser("ablation", help="re-ID and noise-control ablations")
    p.set_defaults(func=cmd_ablation)

    p = sub.add_parser("eval-paraphrase", help="held-out wording experiment")
    p.add_argument("--make", action="store_true", help="have Gemini reword the captions first (saved for offline re-runs)")
    p.add_argument("--gemini", action="store_true", help="also score the Gemini analyzer")
    p.set_defaults(func=cmd_eval_paraphrase)

    p = sub.add_parser("eval-qa", help="context-management experiment on a fixed question set")
    p.add_argument("--gemini", action="store_true", help="run the Gemini conditions (needs GEMINI_API_KEY)")
    p.add_argument("--heldout", action="store_true", help="use the held-out question set")
    p.add_argument("--questions", help="another question file, e.g. data/eval/qa_gemini_subset.yaml")
    p.add_argument("--conditions", nargs="+", choices=["offline", "naive", "hierarchical", "tools_only", "summaries"])
    p.add_argument("--pause", type=float, default=0.0, help="seconds between questions (free-tier rate limits)")
    p.set_defaults(func=cmd_eval_qa)

    p = sub.add_parser("vlm", help="Gemini VLM on the real images in data/images")
    p.set_defaults(func=cmd_vlm)

    p = sub.add_parser("benchmark", help="scalability benchmark")
    p.add_argument("--frames", type=int, nargs="+", default=[10_000, 50_000, 100_000, 200_000])
    p.add_argument("--repeats", type=int, default=30)
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("demo", help="scene-by-scene walkthrough of the system")
    p.add_argument("--pause", action="store_true", help="wait for Enter between scenes")
    p.add_argument("--fast", action="store_true", help="no streaming delay")
    p.add_argument("--offline", action="store_true", help="offline analyst even if a Gemini key is set")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("models", help="list Gemini models and record the choice")
    p.set_defaults(func=cmd_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    args = build_parser().parse_args(argv)
    return args.func(args)


__all__ = ["main", "build_parser"]
