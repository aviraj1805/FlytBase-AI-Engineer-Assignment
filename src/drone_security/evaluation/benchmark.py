"""Scalability benchmark: the full pipeline at 10k / 50k / 100k / 200k frames.

The frames are the scripted day repeated on consecutive dates (fresh paraphrases, jitter and
wind each day, the same regular visitors), i.e. what the dock's index looks like after weeks
or months of daily patrols. For each size we measure:

* ingest: frames per second through parse -> track -> rules -> index -> memory;
* query latency (median and p95 over repeats) for the operator's typical lookups;
* database size on disk;
* prompt context: the hierarchical working memory vs pasting every frame (naive).
"""
from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path

from ..agent import AnalystToolkit
from ..config import PropertyConfig
from ..indexing import FrameIndex
from ..memory import estimate_tokens, render_from_index, render_naive
from ..pipeline import SecurityPipeline
from ..retrieval import HybridRetriever
from ..scenario import generate_frames

DEFAULT_SIZES = [10_000, 50_000, 100_000, 200_000]


def run_benchmark(config: PropertyConfig, sizes: list[int] = DEFAULT_SIZES, repeats: int = 30) -> dict:
    rows = []
    for n in sizes:
        rows.append(_one_size(config, n, repeats))
        print(f"  {n:>7,} frames: {rows[-1]['ingest_fps']:,.0f} frames/s, db {rows[-1]['db_mb']} MB", flush=True)
    return {"sizes": sizes, "repeats": repeats, "rows": rows}


def _one_size(config: PropertyConfig, n: int, repeats: int) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="dsa_bench_"))
    db = workdir / "bench.db"
    started = time.perf_counter()
    records = list(generate_frames(n, config))
    generate_s = time.perf_counter() - started

    index = FrameIndex(db, batch_size=2000)
    pipeline = SecurityPipeline(config, index=index)
    stats = pipeline.run(records)
    with index.lock:
        index.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db_mb = db.stat().st_size / 1e6

    last_day = index.stats()["last_ts"][:10]
    toolkit = AnalystToolkit(index, config)
    f150 = next(e for e in index.entities(category="vehicle") if "F150" in e["display_name"])
    retriever = HybridRetriever(index)
    fit_started = time.perf_counter()
    retriever.search("warm up", limit=1)  # fits the TF-IDF vectors over the unique captions once
    vector_fit_s = time.perf_counter() - fit_started

    queries = {
        "object: all truck detections": lambda: index.query_detections(label="truck", limit=100),
        "time range: one hour of frames": lambda: index.query_frames(start=f"{last_day} 23:00", end=f"{last_day} 23:59"),
        "object + zone + time": lambda: index.query_detections(label="person", zone="main_gate",
                                                                start=f"{last_day} 00:00", end=f"{last_day} 06:00"),
        "entity history (F150)": lambda: toolkit.entity_history("blue Ford F150"),
        "alerts HIGH+ (first 100)": lambda: index.alerts(min_severity="HIGH", limit=100),
        "full text (BM25)": lambda: index.search("masked fence", limit=20),
        "hybrid text (BM25 + TF-IDF)": lambda: retriever.search("someone climbing the fence", limit=10),
        "summaries for one day": lambda: index.summaries("hour", start=f"{last_day} 00:00", end=f"{last_day} 23:59"),
    }
    latencies = {}
    for name, fn in queries.items():
        times = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            fn()
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        latencies[name] = {"p50_ms": round(statistics.median(times), 3), "p95_ms": round(times[int(0.95 * (len(times) - 1))], 3)}

    context = render_from_index(index, config)
    naive_chars = len(render_naive(index, config))
    s = index.stats()
    row = {
        "frames": n,
        "days": len({r["timestamp"][:10] for r in records}),
        "generate_s": round(generate_s, 2),
        "ingest_s": round(stats.elapsed_s, 2),
        "ingest_fps": round(stats.fps),
        "detections": s["detections"],
        "entities": s["entities"],
        "alerts": stats.alerts,
        "raw_rule_firings": stats.raw_rule_firings,
        "db_mb": round(db_mb, 1),
        "unique_captions": len(retriever._captions),
        "vector_fit_s": round(vector_fit_s, 2),
        "latency": latencies,
        "context_tokens_hierarchical": estimate_tokens(context),
        "context_tokens_naive": (naive_chars + 3) // 4,
    }
    index.close()
    return row


def write_benchmark_report(result: dict, out_dir: Path) -> Path:
    from .report import write_json

    write_json(result, out_dir, "benchmark", "python -m drone_security benchmark")
    _plots(result, out_dir)
    rows = result["rows"]
    queries = list(rows[0]["latency"])
    lines = [
        "# Scalability benchmark",
        "",
        "Produced by `python -m drone_security benchmark` (single process, SQLite file on local disk). "
        "Raw data: `benchmark.json`; plots: `benchmark_*.png`. Frames are the scripted day repeated on "
        "consecutive dates with fresh paraphrases and jitter.",
        "",
        "## Ingest and storage",
        "",
        "| frames | days | ingest s | frames/s | detections | entities | raw rule firings | alerts | DB MB | unique captions |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *[f"| {r['frames']:,} | {r['days']} | {r['ingest_s']} | {r['ingest_fps']:,} | {r['detections']:,} | {r['entities']:,} | "
          f"{r['raw_rule_firings']:,} | {r['alerts']:,} | {r['db_mb']} | {r['unique_captions']:,} |" for r in rows],
        "",
        f"## Query latency, median / p95 in ms ({result['repeats']} repeats)",
        "",
        "| query | " + " | ".join(f"{r['frames']:,}" for r in rows) + " |",
        "|---|" + "---|" * len(rows),
        *[f"| {q} | " + " | ".join(f"{r['latency'][q]['p50_ms']} / {r['latency'][q]['p95_ms']}" for r in rows) + " |"
          for q in queries],
        "",
        "## Agent context size (tokens, estimated at 4 characters per token)",
        "",
        "| frames | hierarchical working memory | every frame in the prompt (naive) | ratio |",
        "|---|---|---|---|",
        *[f"| {r['frames']:,} | {r['context_tokens_hierarchical']:,} | {r['context_tokens_naive']:,} | "
          f"{r['context_tokens_naive'] / r['context_tokens_hierarchical']:,.0f}x |" for r in rows],
    ]
    path = out_dir / "benchmark.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _plots(result: dict, out_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")  # no display needed
    import matplotlib.pyplot as plt

    rows = result["rows"]
    frames = [r["frames"] for r in rows]

    fig, ax = plt.subplots(figsize=(6, 3.6))
    for q in rows[0]["latency"]:
        ax.plot(frames, [r["latency"][q]["p50_ms"] for r in rows], marker="o", label=q)
    ax.set(xlabel="frames in index", ylabel="median latency (ms)", title="Query latency vs index size", yscale="log")
    ax.legend(fontsize=6, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "benchmark_latency.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.plot(frames, [r["context_tokens_naive"] for r in rows], marker="o", label="every frame in the prompt")
    ax.plot(frames, [r["context_tokens_hierarchical"] for r in rows], marker="o", label="hierarchical working memory")
    ax.axhline(1_000_000, color="grey", linestyle="--", linewidth=1, label="1M-token context window")
    ax.set(xlabel="frames", ylabel="prompt tokens (estimate)", title="Agent context size", yscale="log")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "benchmark_context.png", dpi=150)
    plt.close(fig)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8, 3.2))
    a1.bar([f"{f // 1000}k" for f in frames], [r["ingest_fps"] for r in rows], color="#4C78A8")
    a1.set(title="Ingest throughput", ylabel="frames / s")
    a2.bar([f"{f // 1000}k" for f in frames], [r["db_mb"] for r in rows], color="#F58518")
    a2.set(title="Database size", ylabel="MB")
    fig.tight_layout()
    fig.savefig(out_dir / "benchmark_ingest.png", dpi=150)
    plt.close(fig)


__all__ = ["run_benchmark", "write_benchmark_report", "DEFAULT_SIZES"]
