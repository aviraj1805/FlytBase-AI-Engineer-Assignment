# UI plan

Goal: a browser UI over the existing backend that shows every feature of the assignment, with no new core
logic. Start with `make ui` / `python make.py ui` / `python run_ui.py`, then open http://localhost:8000.

## Stack (and why)

* **API**: FastAPI in `src/drone_security/web/` (`api.py` routes, `state.py` shared state, `replay.py` live
  replay). It calls the existing modules directly.
* **Frontend**: plain HTML + ES modules + one CSS file in `src/drone_security/web/static/`. There is no build
  step and no npm install, so it works from a fresh clone and fully offline. Chart.js 4.4.1 is vendored
  (MIT licence included).
* **Live stream**: Server-Sent Events. Each replay connection runs its own `SecurityPipeline` on the
  scenario feed, so alerts are produced live by the rule engine, not read from a list.

## Backend functions the UI calls

| Screen | Backend |
|---|---|
| Mission Control (replay) | `ingest.read_records`, `ingest.FeedSanitizer`, `pipeline.SecurityPipeline.process` (per frame, live), `config.PropertyConfig` (zones, lat/lon) |
| Live Alerts | alerts from the live replay (`FrameResult.alerts`); full day from `FrameIndex.alerts` |
| Event Log | `FrameIndex.events` joined with `detections` (confidence, re-ID confidence) |
| Frame detail | `FrameIndex.query_frames(frame_ids=…)`, `query_detections`, `alerts`, `events` |
| Frame Index Explorer | `FrameIndex.query_detections`, `query_frames`, `retrieval.HybridRetriever.search`; the SQL shown is captured with `sqlite3.set_trace_callback` while the query runs |
| Agent Chat | `agent.SecurityAnalyst.ask` (Gemini agent when a key is set, else offline), `agent.check_citations` |
| Entity Tracker | `FrameIndex.entities`, `query_detections(entity_id=…)`, `alerts(entity_id=…)`, tracker weights from `tracking.py` |
| Summaries | `analysis.summarize_video`, `daily_briefing`, `recommend`, `FrameIndex.summaries(level)` |
| Context view | `memory.render_from_index`, `render_naive`, `estimate_tokens`; saved `docs/results/qa_*_eval.json` |
| QA / Tests | `pytest --junitxml` in a subprocess, parsed per test file |
| Evaluation | saved `docs/results/*.json`; `evaluation.benchmark.run_benchmark` with a small size |
| Architecture | `docs/diagrams/*.svg` |

## Data shapes (all JSON)

* Frame: `{frame_id, ts, description, zone, lat, lon, altitude_m, battery_pct, wind_mps, gps_hdop, mode, distance_from_home_m}`
* Detection: `{category, label, display_name, color, make, model, action, zone, attributes[], confidence, entity_id, track_confidence}`
* Alert: `{ts, frame_id, rule_id, severity_name, message, explanation, evidence_frame_ids[], zone, entity_id, recommended_action, related_rules[]}`
* SSE replay events: `snapshot` (on connect: position, alerts so far), `frame` (frame, detections, logs, alerts), `end`.

## Gaps found and how they are handled

* Event logs store no confidence: the API joins the detection row of the same frame and entity.
* Entities store no "why matched" text: the API returns the identity traits (signature) with the tracker's
  weights, the mean re-ID confidence and the per-sighting confidences.
* Captions give a zone, not exact object positions: the map draws objects at their detected zone and says so.
* The agent computes the whole answer before returning it: the UI streams the computed answer in chunks
  over SSE, with the tool calls sent first. This is display streaming, not token streaming from the model.
* Alert acknowledgement is not part of the backend: it is kept in the browser session only.
