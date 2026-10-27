# Audit of the first version (commit `273dceb`)

Audited on Wed 28 Oct 2026, 00:40 IST. Deadline: Thu 29 Oct 2026, 13:15 IST.
Feature freeze: Wed 28 Oct 2026, 19:15 IST. After that, only testing, documentation and the demo script.

## 1. What exists

| Area | What is there | State |
|---|---|---|
| Simulation | `simulator.py`: hand-written JSON scenario (`demo_day.json`, 41 scripted frames + 26 patrol frames), telemetry synthesizer (GPS jitter, battery drain, wind), random load generator | Works. **No ground truth**: expected outcomes live only inside test assertions |
| Perception | Lexicon/regex caption parser (`rule_analyzer.py`), Claude structured-output analyzer, Claude/BLIP captioners | Parser works well on the demo captions. The LLM paths were **never run against a real API** and no real images exist in the repo |
| Tracking | Exact-match appearance signature -> entity, visit segmentation, dwell time | Works for exact repeats. **No confidence score**. Any attribute variation creates a new entity (the demo day reports 10 "people" for 6 real ones) |
| Rules | 12 Python rule classes, cooldown + per-frame correlation | Works. **Not declarative** (thresholds in JSON, logic in code). Alerts have no evidence frame ids and no explanation |
| Index | SQLite: frames, detections, entities, events, alerts, summaries; FTS5 with enrichment tags; (column, ts) indexes | Solid. Missing: alert evidence, re-ID confidence, semantic retrieval |
| Agent | LangChain `create_agent` + Claude, 9 tools; deterministic offline analyst with intent routing and follow-ups | Offline analyst works. **The LLM agent crashes on its first tool call**: LangGraph runs tools in a worker thread and the SQLite connection is single-thread (`2 failed` in pytest). Answers do not cite frame ids and there is no "not enough evidence" behaviour |
| Context | Hourly digests + a character-budgeted working-memory block | Works, but it is one level (hour), not hierarchical, and **was never measured** against a naive baseline |
| Benchmark | `benchmark` command, 20k random frames | Runs, but results are printed only. Numbers in the README cannot be traced to a saved run. No 50k/100k, no plots |
| Tests | 87 pytest tests (85 pass, 2 fail) | Good unit coverage. Missing: ground-truth scenario scoring and edge cases (out-of-order/duplicate frames, missing telemetry, malformed input, midnight rollover, empty day) |
| Docs | README, FEATURE_SPEC, ARCHITECTURE (Mermaid source), TESTING, REPORT (markdown), DEMO_SCRIPT | Well written. Missing: exported diagrams, PDF report, AUDIT, SUBMISSION_CHECKLIST, Makefile. LLM is Claude, but this upgrade targets Gemini |
| Repo layout | package at repo root (`drone_security/`), no `src/`, no `pyproject.toml`, no task runner | Needs `src/` layout and `setup/run/test/demo/benchmark` targets |

## 2. Gaps against the assignment

| Requirement | Gap |
|---|---|
| 1. Process telemetry + frames | Fine for clean input. Breaks or misbehaves on missing telemetry (every stage dereferences `frame.telemetry`), duplicates (re-processed, double-counted), out-of-order frames (negative gaps in the tracker) |
| 2. Identify and log objects/events | Logs are good. Identity is brittle (exact signatures), no confidence |
| 3. Real-time alerts from predefined rules | Rules are code, not declarative. No evidence frame ids, no explanation. No unattended-package or GPS-drift rule |
| 4. Frame index, queryable by time/object | Yes. Free-text search is keyword-only |
| 5. Deliverables | No PDF report, no exported diagrams, no Makefile, no measured results in `docs/results/`, no checklist |
| Bonus: summary + Q&A | One-sentence summary and offline Q&A exist. No daily briefing. LLM Q&A is broken (thread bug) |

## 3. Gaps against the scoring criteria

| Criterion (weight) | Main gap |
|---|---|
| Correctness and model performance (25%) | **Nothing is measured against ground truth.** No precision/recall for detections, alerts or re-ID. The VLM path has never processed an image |
| Reasoning and scalability (25%) | Context management is one level and unmeasured. The scale test stops at 20k frames and is not saved |
| Experimentation and innovation (30%) | No experiments: no backend comparison, no retrieval evaluation, no ablation, no error analysis |
| Documentation and code quality (20%) | Docs cite numbers that have no raw output behind them. LLM agent is broken. No `src/` layout |

## 4. Plan

Tiers run strictly in order. The repo stays runnable and all tests pass at every commit.

### P0: every requirement solid and provably correct
1. Fix the SQLite cross-thread bug (LLM agent). Move to `src/` layout with `pyproject.toml`; add `make.py` (Windows has no `make`) and a `Makefile` that delegates to it: `setup`, `run`, `test`, `demo`, `benchmark`.
2. **Seeded scenario generator** for one full day with scripted events (same blue Ford F150 entering twice, midnight loitering at the main gate, unknown person in the restricted warehouse, unattended package, gate left open, after-hours vehicle, low battery, geofence breach, GPS drift) plus benign distractors and caption paraphrases. Frames and **ground truth are written to separate files**.
3. **Robust ingestion**: optional telemetry, duplicate and out-of-order handling (reorder buffer), malformed-input quarantine, midnight rollover.
4. **Declarative YAML rules** (`config/rules.yaml`): loitering (dwell), after-hours person/vehicle, repeat vehicle entry, restricted zone, unattended package, gate open, drone health (battery, geofence, GPS drift, altitude, wind). Every alert carries severity, rule id, timestamp, evidence frame ids and a plain-English explanation; cooldown and de-duplication stay.
5. **Gemini backends** behind the existing interfaces: model chosen by listing available models; LLM frame analyzer (structured output, batched, cached) and VLM analyzer run on a small set of real images. Offline parser stays the default.
6. **Evaluation harness**: alerts vs ground truth (P/R/F1) and detection accuracy. pytest gets scenario tests that score against ground truth and the edge cases above.

### P1: what makes it stand out (measured, not claimed)
7. **Re-identification with confidence**: attribute-similarity matching instead of exact signatures; pairwise precision/recall against ground truth with error analysis.
8. **Hybrid retrieval**: FTS5/BM25 + semantic vectors (Gemini embeddings, offline TF-IDF fallback) fused with reciprocal-rank fusion; evaluated on a labelled query set (Recall@k, MRR).
9. **Grounded agent**: LangChain agent on Gemini with `query_frames`, `entity_history`, `list_alerts`, `summarize_window`, `drone_status`; cites frame ids; says "not enough evidence" instead of guessing. Citation check against retrieved ids.
10. **Hierarchical context**: frame -> minute -> hour -> day summaries with a rolling window, measured against a "whole video in the prompt" baseline (tokens and answer accuracy on a fixed question set).
11. Bonus: one-sentence summary, daily security briefing, follow-up Q&A.

### P2: evidence of scale and rigour
12. Scalability benchmark at 10k, 50k and 100k+ frames (ingest throughput, query latency, DB size, context size) with tables and plots in `docs/results/`.
13. Evaluation report (P/R/F1, false-positive and false-negative analysis) and a small ablation (with/without hierarchical summaries, with/without semantic retrieval).

### After the freeze
README, REPORT (markdown + PDF), diagrams (Mermaid + SVG/PNG), FEATURE_SPEC, TESTING, DEMO_SCRIPT, SUBMISSION_CHECKLIST, clean-clone verification.
