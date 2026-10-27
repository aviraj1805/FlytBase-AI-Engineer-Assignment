"""Command-line interface: python -m drone_security <command> ...

  run        process a scenario as a live feed (logs + alerts), build the frame index
  query      structured / full-text queries over the index ("show all truck events")
  alerts     list alerts, optionally by severity and time
  ask        one question to the analyst agent
  chat       interactive Q&A with follow-ups
  summary    one-sentence video summary, highlights and recommendations
  context    print the agent's working-memory block
  video      caption a real video / image folder with a VLM, then run the pipeline
  benchmark  scalability test: ingest N synthetic frames, measure throughput and query latency
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT, PropertyConfig
from .models import FrameResult, Severity

DEFAULT_DB = PROJECT_ROOT / "data" / "runs" / "latest.db"
DEFAULT_SCENARIO = PROJECT_ROOT / "data" / "scenarios" / "demo_day.json"

try:
    from rich.console import Console
    from rich.table import Table

    _console = Console(highlight=False)
except ImportError:  # rich is optional; plain output works everywhere
    _console = None
    Table = None

_SEVERITY_STYLE = {"CRITICAL": "bold white on red", "HIGH": "bold red", "MEDIUM": "yellow", "LOW": "cyan", "INFO": "dim"}


def out(text: str = "", style: str | None = None) -> None:
    if _console:
        _console.print(text, style=style, markup=False)
    else:
        print(text)


def heading(text: str) -> None:
    out(f"\n== {text} ==", style="bold")


def table(title: str, columns: list[str], rows: list[list]) -> None:
    if _console and Table:
        t = Table(title=title, show_lines=False)
        for c in columns:
            t.add_column(c)
        for r in rows:
            t.add_row(*[str(v) if v is not None else "" for v in r])
        _console.print(t)
        return
    heading(title)
    print(" | ".join(columns))
    for r in rows:
        print(" | ".join(str(v) if v is not None else "" for v in r))


# ----------------------------------------------------------------------------------------- commands
def cmd_run(args) -> int:
    from .analysis import recommend, summarize_video
    from .indexing import FrameIndex
    from .pipeline import SecurityPipeline
    from .simulator import load_scenario, stream

    config = PropertyConfig.load(args.config)
    db = Path(args.db)
    _reset_db(db)
    index = FrameIndex(db)
    analyzer = _analyzer(args.analyzer, config)
    pipeline = SecurityPipeline(config, analyzer=analyzer, index=index)
    frames = load_scenario(args.scenario, config, background=not args.no_patrol)

    heading(f"Processing {len(frames)} frames from {Path(args.scenario).name} (analyzer: {analyzer.name})")

    def show(result: FrameResult) -> None:
        f, t = result.frame, result.frame.telemetry
        if args.verbose:
            out(f"[{f.timestamp:%H:%M}] #{f.frame_id:<4} {t.zone:<11} alt {t.altitude_m:>4.0f}m  bat {t.battery_pct:>3.0f}%  "
                f"\"{f.description}\"", style="dim")
        for log in result.logs:
            out(f"  LOG    {log.message}")
        for alert in result.alerts:
            out(f"  ALERT  {alert.severity.name:<8} {alert.message}", style=_SEVERITY_STYLE[alert.severity.name])

    stats = pipeline.run(stream(frames, args.speed), on_result=None if args.quiet else show)
    index.flush()

    heading("Run statistics")
    out(f"{stats.frames} frames, {stats.detections} detections, {stats.logs} log entries, {stats.alerts} alerts "
        f"({', '.join(f'{k} {v}' for k, v in sorted(stats.alerts_by_severity.items(), key=lambda kv: -Severity[kv[0]]))}); "
        f"{stats.suppressed_alerts} duplicate alerts suppressed")
    out(f"{stats.elapsed_s * 1000:.0f} ms total, {stats.fps:,.0f} frames/s; index: {db}")
    if hasattr(analyzer, "stats"):
        out(f"LLM analyzer: {analyzer.stats}")

    summary = summarize_video(index)
    heading("Video summary")
    out(summary.one_sentence)
    heading("Recommendations")
    for rec in recommend(index, config):
        out(f"[{rec.priority}] {rec.text}")
    index.close()
    return 0


def cmd_query(args) -> int:
    index = _open_index(args.db)
    if args.text:
        rows = index.search(args.text, start=args.start, end=args.end, limit=args.limit)
        table(f"Full-text search: '{args.text}' ({len(rows)} frames)", ["time", "frame", "zone", "caption"],
              [[r["ts"][11:16], r["frame_id"], r["zone"], r["description"]] for r in rows])
        return 0
    if not any([args.label, args.category, args.color, args.make, args.model, args.zone, args.action, args.entity]):
        rows = index.query_frames(start=args.start, end=args.end, limit=args.limit)
        table(f"Frames ({len(rows)})", ["time", "frame", "zone", "alt m", "battery %", "caption"],
              [[r["ts"][11:16], r["frame_id"], r["zone"], f"{r['altitude_m']:.0f}", f"{r['battery_pct']:.0f}", r["description"]]
               for r in rows])
        return 0
    rows = index.query_detections(label=args.label, category=args.category, color=args.color, make=args.make,
                                  model=args.model, zone=args.zone, action=args.action, entity_id=args.entity,
                                  start=args.start, end=args.end, limit=args.limit)
    table(f"Detections ({len(rows)})", ["time", "frame", "object", "action", "zone", "entity", "caption"],
          [[r["ts"][11:16], r["frame_id"], r["display_name"], r["action"], r["zone"], r["entity_id"], r["description"]]
           for r in rows])
    return 0


def cmd_alerts(args) -> int:
    index = _open_index(args.db)
    rows = index.alerts(min_severity=args.min_severity, start=args.start, end=args.end)
    table(f"Alerts ({len(rows)})", ["time", "severity", "rule", "message", "recommended action"],
          [[r["ts"][11:16], r["severity_name"], r["rule_id"], r["message"], r["recommended_action"]] for r in rows])
    return 0


def cmd_ask(args) -> int:
    analyst = _analyst(args)
    answer = analyst.ask(" ".join(args.question))
    _print_answer(answer, analyst)
    return 0


def cmd_chat(args) -> int:
    analyst = _analyst(args)
    out(f"Security analyst ({analyst.mode} mode). Ask about the footage; 'exit' to quit.", style="bold")
    while True:
        try:
            question = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"exit", "quit", "q"}:
            break
        if question:
            _print_answer(analyst.ask(question), analyst)
    return 0


def cmd_summary(args) -> int:
    from .analysis import recommend, summarize_video

    index = _open_index(args.db)
    config = PropertyConfig.load(args.config)
    s = summarize_video(index)
    heading("One-sentence summary")
    out(s.one_sentence)
    if args.llm:
        from .llm import ClaudeClient
        from .context import render_from_index

        text = ClaudeClient(effort="medium").text(
            system="You summarise a security drone's day for the property owner. Use only the facts given.",
            content=f"Write a 3-4 sentence briefing for the property owner.\n\n{render_from_index(index, config)}",
        )
        heading("Claude briefing")
        out(text)
    heading("Highlights")
    for h in s.highlights:
        out(f"- {h}")
    heading("Recommendations")
    for rec in recommend(index, config):
        out(f"[{rec.priority}] {rec.text}")
    return 0


def cmd_context(args) -> int:
    from .context import render_from_index

    out(render_from_index(_open_index(args.db), PropertyConfig.load(args.config), args.budget))
    return 0


def cmd_video(args) -> int:
    from .analysis import summarize_video
    from .indexing import FrameIndex
    from .perception.vlm import get_captioner
    from .pipeline import SecurityPipeline
    from .video import caption_video

    config = PropertyConfig.load(args.config)
    _reset_db(Path(args.db))
    index = FrameIndex(args.db)
    pipeline = SecurityPipeline(config, analyzer=_analyzer(args.analyzer, config), index=index)
    captioner = get_captioner(args.captioner)
    frames = caption_video(args.source, captioner, config, start=datetime.fromisoformat(args.start), zone=args.zone,
                           every_s=args.every, max_frames=args.max_frames, save_dir=args.save_frames)

    def show(result: FrameResult) -> None:
        out(f"[{result.frame.timestamp:%H:%M:%S}] {captioner.name}: {result.frame.description}", style="dim")
        for log in result.logs:
            out(f"  LOG    {log.message}")
        for alert in result.alerts:
            out(f"  ALERT  {alert.severity.name:<8} {alert.message}", style=_SEVERITY_STYLE[alert.severity.name])

    stats = pipeline.run(frames, on_result=show)
    heading("Summary")
    out(f"{stats.frames} frames captioned and indexed in {stats.elapsed_s:.1f}s")
    out(summarize_video(index).one_sentence)
    index.close()
    return 0


def cmd_benchmark(args) -> int:
    from .indexing import FrameIndex
    from .pipeline import SecurityPipeline
    from .simulator import random_frames

    config = PropertyConfig.load(args.config)
    tmp = Path(tempfile.mkdtemp()) / "bench.db"
    index = FrameIndex(tmp, batch_size=500)
    pipeline = SecurityPipeline(config, index=index)
    started = time.perf_counter()
    frames = random_frames(args.frames, config, seed=args.seed)
    gen_s = time.perf_counter() - started
    stats = pipeline.run(frames)

    queries = {
        "show all truck events": lambda: index.query_detections(label="truck", limit=100),
        "people at main gate 00:00-06:00": lambda: index.query_detections(label="person", zone="main_gate",
                                                                           start="00:00", end="06:00", limit=100),
        "full-text 'masked fence'": lambda: index.search("masked fence", limit=20),
        "HIGH+ alerts": lambda: index.alerts(min_severity="HIGH", limit=100),
        "telemetry near 12:00": lambda: index.telemetry_near("12:00"),
        "object inventory": lambda: index.object_counts(),
    }
    rows = []
    for name, fn in queries.items():
        times = []
        for _ in range(args.repeats):
            t0 = time.perf_counter()
            fn()
            times.append((time.perf_counter() - t0) * 1000)
        rows.append([name, f"{statistics.median(times):.2f}", f"{max(times):.2f}"])

    heading(f"Scalability benchmark: {args.frames:,} frames")
    out(f"frame generation: {gen_s:.2f}s")
    out(f"pipeline (analyze + track + rules + index): {stats.elapsed_s:.2f}s -> {stats.fps:,.0f} frames/s "
        f"({stats.detections:,} detections, {stats.alerts:,} alerts, {stats.suppressed_alerts:,} suppressed)")
    out(f"database size: {tmp.stat().st_size / 1e6:.1f} MB")
    table("Query latency (ms)", ["query", "median", "max"], rows)
    index.close()
    return 0


# ----------------------------------------------------------------------------------------- helpers
def _reset_db(db: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)


def _open_index(db: str):
    from .indexing import FrameIndex

    if not Path(db).exists():
        sys.exit(f"No index at {db}. Run `python -m drone_security run` first.")
    return FrameIndex(db)


def _analyzer(name: str, config: PropertyConfig):
    if name == "claude":
        from .llm import llm_available
        from .perception.llm_analyzer import ClaudeFrameAnalyzer

        if not llm_available():
            sys.exit("--analyzer claude needs ANTHROPIC_API_KEY (or DSA_LLM=1 with an `ant auth login` profile).")
        return ClaudeFrameAnalyzer(config)
    from .perception import RuleBasedAnalyzer

    return RuleBasedAnalyzer()


def _analyst(args):
    from .agent import SecurityAnalyst

    use_llm = False if args.offline else None
    analyst = SecurityAnalyst(_open_index(args.db), PropertyConfig.load(args.config), use_llm=use_llm)
    if analyst.llm_error:
        out(analyst.llm_error, style="yellow")
    return analyst


def _print_answer(answer, analyst) -> None:
    if answer.tool_calls:
        calls = ", ".join(f"{name}({', '.join(f'{k}={v!r}' for k, v in a.items())})" for name, a in answer.tool_calls)
        out(f"[{answer.mode}] tools: {calls}", style="dim")
    if answer.mode == "offline" and analyst.llm_error and analyst.llm_agent:
        out(f"(LLM unavailable: {analyst.llm_error})", style="yellow")
    out(answer.text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m drone_security", description="Drone Security Analyst Agent")
    parser.add_argument("--config", default=None, help="property config JSON (default: config/property.json)")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"frame index database (default: {DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="process a scenario as a live feed")
    p.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    p.add_argument("--analyzer", choices=["rules", "claude"], default="rules")
    p.add_argument("--speed", type=float, default=0, help="replay speed: 60 = one footage minute per second (0 = max)")
    p.add_argument("--verbose", "-v", action="store_true", help="also print every frame with its telemetry")
    p.add_argument("--quiet", "-q", action="store_true", help="only print the final statistics")
    p.add_argument("--no-patrol", action="store_true", help="do not add background patrol frames")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("query", help="query the frame index")
    for flag in ("label", "category", "color", "make", "model", "zone", "action", "entity", "text"):
        p.add_argument(f"--{flag}")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("alerts", help="list alerts")
    p.add_argument("--min-severity", choices=[s.name for s in Severity])
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.set_defaults(func=cmd_alerts)

    for name, func in (("ask", cmd_ask), ("chat", cmd_chat)):
        p = sub.add_parser(name, help="ask the analyst agent" if name == "ask" else "interactive Q&A")
        if name == "ask":
            p.add_argument("question", nargs="+")
        p.add_argument("--offline", action="store_true", help="force the offline analyst even if an API key is set")
        p.set_defaults(func=func)

    p = sub.add_parser("summary", help="video summary + recommendations")
    p.add_argument("--llm", action="store_true", help="also write a Claude briefing for the owner")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("context", help="print the agent working memory")
    p.add_argument("--budget", type=int, default=6000, help="character budget")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("video", help="caption real footage with a VLM and run the pipeline")
    p.add_argument("source", help="video file or folder of images")
    p.add_argument("--captioner", choices=["claude", "blip"], default="claude")
    p.add_argument("--analyzer", choices=["rules", "claude"], default="rules")
    p.add_argument("--zone", default="main_gate", help="zone the drone is watching")
    p.add_argument("--start", default=datetime.now().replace(microsecond=0).isoformat(sep=" "), help="footage start time")
    p.add_argument("--every", type=float, default=2.0, help="sample one frame every N seconds")
    p.add_argument("--max-frames", type=int, default=30)
    p.add_argument("--save-frames", default=None, help="folder to save sampled JPEGs")
    p.set_defaults(func=cmd_video)

    p = sub.add_parser("benchmark", help="scalability test")
    p.add_argument("--frames", type=int, default=20000)
    p.add_argument("--repeats", type=int, default=20)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(func=cmd_benchmark)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    args = build_parser().parse_args(argv)
    return args.func(args)
