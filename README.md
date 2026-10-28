# Drone Security Analyst Agent

A security analyst for a **docked drone that patrols one property every day**. It reads the drone's
telemetry and video frames (simulated as text descriptions, or real images through a VLM), turns each frame
into structured objects, tracks who and what comes and goes, raises **explained real-time alerts**, indexes
every frame for search, and answers questions through an **agent that cites the frames it relies on**.

```
[02:14] ALERT  CRITICAL Masked person in black jacket climbing at back fence, 02:14.
        why: Break-in behaviour: Masked person in black jacket detected at the back fence (frame 67);
             the caption describes climbing.   evidence frames: [67]
[16:45] ALERT  LOW      Blue Ford F150 entered twice today (09:14, 16:45), 16:45.

you> How many times did the blue Ford F150 enter today?
Blue Ford F150 (V-002, re-ID confidence 1.00) made 2 visits with 10 sightings:
09:14-09:42 (main gate, garage) [f256]; 16:45-17:10 (main gate, garage) [f454].
you> When did it leave?
Blue Ford F150 was seen leaving at 09:42 [f270], 17:10 [f466].
you> What was the licence plate of the black SUV?
Not enough evidence in the footage to answer that. The frame descriptions never record licence plates.
```

| Document | |
|---|---|
| [docs/REPORT.md](docs/REPORT.md) / [REPORT.pdf](docs/REPORT.pdf) | Approach, assumptions, design choices, all results, error analysis, how AI tools helped |
| [docs/FEATURE_SPEC.md](docs/FEATURE_SPEC.md) | Value and the three key requirements with acceptance criteria |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data model, diagrams ([docs/diagrams](docs/diagrams)) |
| [docs/TESTING.md](docs/TESTING.md) | Test strategy, cases, scenarios, how to reproduce, latest results |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Scene-by-scene script for the demo video |
| [docs/results/](docs/results) | Raw outputs of every experiment (JSON + Markdown + plots) |
| [docs/AUDIT.md](docs/AUDIT.md) | Audit of the first version and the upgrade plan |

## Quick start

Python 3.10+. Works offline; no API key needed.

```bash
git clone <repo-url> && cd <repo>
python make.py setup        # creates .venv and installs the package + test deps
python make.py test         # 128 tests
python make.py run          # process the scripted day: live logs, alerts, summary
python make.py demo         # the 8-scene walkthrough used for the demo video
python make.py eval         # offline experiments -> docs/results
python make.py benchmark    # 10k / 50k / 100k / 200k frames -> docs/results
```

`make <target>` does the same on Linux/macOS (the Makefile calls `make.py`). After `setup`, activate the
environment (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere) and use the CLI,
`python -m drone_security <command>`:

```bash
python -m drone_security query --label truck                  # "show all truck events"
python -m drone_security query --text "person climbing the fence" --from 00:00 --to 06:00
python -m drone_security alerts --min-severity HIGH --explain
python -m drone_security ask "What happened between 23:00 and 23:30?"
python -m drone_security chat                                 # follow-up questions keep context
python -m drone_security summary                              # one sentence + daily briefing
python -m drone_security run --feed data/scenarios/sample_frames.txt   # the brief's text format
python -m drone_security run --speed 60 --explain             # replay as a live feed, 1 footage minute per second
```

### Gemini (optional)

Put the key in an environment variable or a gitignored `.env` file in the repo root:

```
GEMINI_API_KEY=your-key
```

With a key, the same commands use Gemini where it helps: `ask`/`chat` use the LangChain + Gemini agent,
`run --analyzer gemini` uses the LLM perception backend, `summary --llm` writes the briefing with Gemini, and
`python make.py eval-gemini` runs the Gemini experiments (model listing, VLM on real images, embeddings,
held-out wording, context experiment). The model is chosen by listing what the key can access; set
`GEMINI_MODEL` to override. Without a key, or if a call fails, everything falls back to the offline path.

## What it does (mapped to the assignment)

| Requirement | Implementation |
|---|---|
| Process telemetry + frames | Seeded scenario generator ([day_script.yaml](data/scenarios/day_script.yaml)): 646 frames with GPS, altitude, battery, wind, HDOP; robust ingest (validation, de-duplication, reorder buffer, missing telemetry); the brief's text format; real images via the Gemini VLM |
| Identify and log objects/events | Pluggable perception: lexicon parser (offline), Gemini LLM, Gemini VLM, all returning the same structured detections (category, label, colour, make, model, clothing, action, zone, confidence); re-identification with confidence; event log ("Blue Ford F150 spotted at garage, 12:00.") |
| Real-time alerts from predefined rules | 14 declarative rules in [config/rules.yaml](config/rules.yaml): intrusion, loitering (caption or measured dwell), restricted zone, after-hours person/vehicle, repeat vehicle, unattended object, gate/door open, battery, geofence, GPS drift, altitude, wind. Each alert: severity, rule id, time, evidence frame ids, explanation, recommended action; cooldown + correlation |
| Frame-by-frame index | SQLite: frames, detections, entities, events, alerts, summaries; indexes on time, object, zone, entity; FTS5 + caption vectors with hybrid (BM25 + vector) search |
| Bonus: summary + Q&A | One-sentence summary, daily briefing, agent with five tools (`query_frames`, `entity_history`, `list_alerts`, `summarize_window`, `drone_status`), frame citations checked, "not enough evidence" instead of guessing |

## Results (all from runs in this repo; raw data in docs/results)

| What | Result | Source |
|---|---|---|
| Alerts vs ground truth (20 incidents, 10 seeds, development captions) | precision 1.00, recall 1.00 | [eval_scenario_rules.md](docs/results/eval_scenario_rules.md) |
| Detections / re-identification (same runs) | P 0.991 / R 1.00; re-ID F1 1.00 | same |
| Re-ID with 25% of identity attributes dropped | F1 0.917 (this version) vs 0.430 (v1 matcher) | [ablation.md](docs/results/ablation.md) |
| Noise control | 19 alerts/day instead of 67.4 raw, same recall | same |
| Retrieval, 22 labelled queries | nDCG@10: hybrid 0.846, keyword 0.822, TF-IDF 0.768 | [retrieval_eval.md](docs/results/retrieval_eval.md) |
| Offline Q&A, held-out questions | 7/12 correct, 0 made-up citations (20/20 on the tuned dev set) | [qa_heldout_eval.md](docs/results/qa_heldout_eval.md) |
| Agent context at 200,000 frames | 2,442 tokens (hierarchical) vs 6,862,855 (every frame) | [benchmark.md](docs/results/benchmark.md) |
| Ingest at 200,000 frames (310 days) | 4,976 frames/s; indexed queries < 1 ms; DB 96.5 MB | same |
| Gemini experiments (VLM on 7 real photos, embeddings, LLM perception, context experiment) | not run: no API key was available during development; `python make.py eval-gemini` produces them | [REPORT 5.6](docs/REPORT.md) |

The 1.00 scores are on captions the parser was developed against; the held-out and ablation numbers are
the ones that say how it generalises. See the report, sections 5-6.

## Architecture

![architecture](docs/diagrams/architecture.png)

```
src/drone_security/
  scenario.py        seeded day generator -> frames.jsonl + ground_truth.json
  ingest.py          validation, de-duplication, reorder buffer, text format
  perception/        rule parser + lexicon; Gemini LLM and VLM analyzers (same interface)
  tracking.py        re-identification with confidence, visits, dwell, evidence frames
  rules/engine.py    declarative rule engine (config/rules.yaml), cooldown, correlation
  indexing/          SQLite + FTS5 frame index
  retrieval.py       hybrid search: BM25 + TF-IDF / Gemini embeddings, reciprocal-rank fusion
  memory.py          hierarchical summaries (10-minute -> hour -> day), prompt rendering
  agent/             5 tools, offline router, LangChain + Gemini agent, citation check
  analysis.py        one-sentence summary, daily briefing, recommendations
  gemini.py          key handling, model selection, retries, token accounting
  evaluation/        ground-truth scoring, retrieval, Q&A/context, VLM, ablations, benchmark
  demo.py, cli.py    demo scenes and the command line
config/              property.json (zones, hours, known vehicles), rules.yaml
data/                scenario script + generated day, real images (+ licences), eval sets
tests/               128 tests
```

Key design decisions (details and alternatives in the report, section 4):

* **Deterministic core, LLM at the edges.** Alerts never wait on the network; Gemini handles unusual wording,
  images and open questions, each with an offline fallback.
* **Rules as data.** Conditions explain themselves, so an alert's explanation is exactly the checks that passed.
* **SQLite + FTS5.** Embedded, zero-ops, runs on the dock; sub-millisecond indexed queries at 200k frames.
* **Hierarchical memory + tools** instead of pasting the video into the prompt: bounded context, drill-down on demand.
* **Measure against ground truth** that the system never sees.

## How AI tools were used

This project was built with **Claude Code** (Anthropic) working from the assignment text: it audited the first
version, planned the upgrade, wrote the code, tests, experiments and documents, ran every command, and fixed
what the measurements exposed. **Gemini** is part of the product (perception, Q&A, embeddings). What I
reviewed and own, especially the ground-truth design, thresholds, and the honesty of the reported numbers, is
listed in the report, section 10.

## Limitations

* The main dataset is a scripted day; the perception numbers on it are optimistic (development set).
* Identity relies on appearance attributes from captions: no plates, no faces. A vague description of a
  returning vehicle is deliberately not matched (precision over recall), so some repeat visits are missed.
* The offline Q&A router is keyword-based and generalises poorly (7/12 held-out); open-ended questions need
  the Gemini agent.
* Q&A grading is regex-based on small question sets.
* No live video decoding, notification delivery or UI; the CLI is the interface.
