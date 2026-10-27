"""Retrieval experiment: keyword vs semantic vs hybrid on a labelled query set.

Metrics per query (k = 10), averaged over queries and per query style:

* hit@k-norm: relevant frames in the top k / min(k, number of relevant frames)
  (1.0 = the top k is as full of relevant frames as it can be);
* MRR: 1 / rank of the first relevant frame (how soon the operator sees a right answer);
* nDCG@k: rank-weighted gain, standard for ranked search.
"""
from __future__ import annotations

import math
import statistics
import time
from pathlib import Path
from typing import Optional

import yaml

from ..config import PROJECT_ROOT, PropertyConfig
from ..retrieval import HybridRetriever, TfidfEmbedder
from .scenario_eval import run_scenario

QUERIES = PROJECT_ROOT / "data" / "eval" / "retrieval_queries.yaml"
K = 10


def relevant_frames(query: dict, truth: dict) -> set[int]:
    wanted = set(query["entities"])
    actions = set(query.get("actions", []))
    out = set()
    for frame_id, objects in truth["frame_labels"].items():
        for o in objects:
            if o["entity"] in wanted and (not actions or o["action"] in actions):
                out.add(int(frame_id))
    return out


def score_ranking(ranked: list[int], relevant: set[int], k: int = K) -> dict:
    top = ranked[:k]
    hits = [fid in relevant for fid in top]
    first = next((i for i, hit in enumerate(hits, start=1) if hit), None)
    dcg = sum(1 / math.log2(i + 1) for i, hit in enumerate(hits, start=1) if hit)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return {
        "hit_at_k": sum(hits) / min(k, len(relevant)) if relevant else 0.0,
        "mrr": 1 / first if first else 0.0,
        "ndcg": dcg / ideal if ideal else 0.0,
    }


def run_retrieval_eval(config: PropertyConfig, embedders: Optional[dict] = None, seed: int = 7) -> dict:
    """embedders: name -> factory(index) for the semantic side; TF-IDF is always included."""
    run = run_scenario(config, seed=seed)
    index, truth = run.pipeline.index, run.truth
    queries = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))["queries"]
    factories = {"tfidf": lambda _index: TfidfEmbedder(), **(embedders or {})}

    retrievers = {"keyword (FTS5/BM25)": HybridRetriever(index, mode="keyword")}
    for name, factory in factories.items():
        retrievers[f"semantic ({name})"] = HybridRetriever(index, embedder=factory(index), mode="semantic")
        retrievers[f"hybrid (BM25 + {name})"] = HybridRetriever(index, embedder=factory(index), mode="hybrid")

    results = {}
    for name, retriever in retrievers.items():
        retriever.search("warm up", limit=1)  # fit vectors once so latency below is per query
        per_query, latencies = [], []
        for q in queries:
            relevant = relevant_frames(q, truth)
            started = time.perf_counter()
            rows = retriever.search(q["query"], limit=K)
            latencies.append((time.perf_counter() - started) * 1000)
            per_query.append({"query": q["query"], "style": q["style"], "relevant": len(relevant),
                              **score_ranking([r["frame_id"] for r in rows], relevant)})
        results[name] = {
            "overall": _mean(per_query),
            "by_style": {style: _mean([p for p in per_query if p["style"] == style])
                         for style in sorted({p["style"] for p in per_query})},
            "median_latency_ms": round(statistics.median(latencies), 2),
            "per_query": per_query,
        }
    return {"queries": len(queries), "k": K, "seed": seed, "results": results}


def _mean(rows: list[dict]) -> dict:
    return {m: round(statistics.mean(r[m] for r in rows), 3) for m in ("hit_at_k", "mrr", "ndcg")} | {"n": len(rows)}


def write_retrieval_report(result: dict, out_dir: Path) -> Path:
    from .report import write_json

    write_json(result, out_dir, "retrieval_eval", "python -m drone_security eval-retrieval")
    styles = sorted(next(iter(result["results"].values()))["by_style"])
    lines = [
        "# Retrieval evaluation",
        "",
        f"{result['queries']} labelled queries over the generated day (seed {result['seed']}); relevance from ground-truth "
        f"entities (data/eval/retrieval_queries.yaml). k = {result['k']}. Raw data: `retrieval_eval.json`.",
        "",
        "| method | hit@10 (norm.) | MRR | nDCG@10 | " + " | ".join(f"nDCG {s} (n={result['results'][next(iter(result['results']))]['by_style'][s]['n']})" for s in styles)
        + " | median latency ms |",
        "|---|---|---|---|" + "---|" * len(styles) + "---|",
    ]
    for name, r in result["results"].items():
        o = r["overall"]
        lines.append(f"| {name} | {o['hit_at_k']} | {o['mrr']} | {o['ndcg']} | "
                     + " | ".join(str(r["by_style"][s]["ndcg"]) for s in styles) + f" | {r['median_latency_ms']} |")
    lines += ["", "## Per query (nDCG@10)", "", "| query | style | " + " | ".join(result["results"]) + " |",
              "|---|---|" + "---|" * len(result["results"])]
    names = list(result["results"])
    for i, q in enumerate(result["results"][names[0]]["per_query"]):
        lines.append(f"| {q['query']} | {q['style']} | " + " | ".join(f"{result['results'][n]['per_query'][i]['ndcg']:.2f}"
                                                                      for n in names) + " |")
    path = out_dir / "retrieval_eval.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = ["run_retrieval_eval", "write_retrieval_report", "relevant_frames", "score_ranking"]
