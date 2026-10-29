# Feature specification: Drone Security Analyst Agent

## Problem

A docked drone patrols one property every day and records hours of footage plus telemetry.
Nobody watches it. Incidents are discovered late or not at all, and when an owner asks
"did that blue truck come back?" the answer is hidden in thousands of frames.

## Value

The agent turns a day of drone footage into **a handful of explained alerts, a searchable
record and an analyst that answers questions with evidence**:

* **For the property owner / guard:** real-time alerts for what matters (intrusion,
  loitering, after-hours visitors, unattended items, open gates), each with a plain-English
  reason and the frames that prove it, instead of a raw video stream.
* **For the investigator:** "show all truck events", "what happened between 23:00 and
  23:30", "did the same F150 come back?" answered in milliseconds from a frame index, with
  identities tracked across the day.
* **For the drone operator (FlytBase's customer):** flight-safety alerts on the same
  channel (battery, geofence, GPS drift, altitude, wind), so security and drone health are
  monitored together.

Measured on the scripted day (docs/results): 20 of 20 incidents alerted with no false
alerts on the development captions; 19 alerts instead of 67 raw rule firings; prompt
context of ~2.4k tokens for 200,000 frames instead of 6.9M.

## Key requirements

### R1. Real-time, explainable alerts (must)
* Rules are declared in `config/rules.yaml` (no code change to add or tune one).
* Each alert carries severity, rule id, timestamp, **evidence frame ids** and an
  **explanation** generated from the exact conditions that fired.
* Noise control: cooldown per rule and entity/zone, correlation of overlapping rules for one
  entity, escalation passes the cooldown.
* Alerts never depend on the network: parsing, tracking and rules run offline.

**Acceptance:** every scripted incident in `day_script.yaml` is alerted inside its window;
benign traps (uniformed guard at night, whitelisted car, short visitor, workers in hours)
raise nothing. Verified by `tests/test_scenario.py` and `python -m drone_security eval`.

### R2. Frame-by-frame index queryable by time, object, zone, entity and text (must)
* SQLite with tables for frames, detections, entities, events, alerts, summaries; B-tree
  indexes on (label, ts), (zone, ts), (entity, ts); FTS5 full text over captions plus
  analyzer tags; optional caption vectors for semantic search.
* Robust to messy feeds: duplicates, out-of-order and late frames, missing telemetry,
  malformed records.

**Acceptance:** "show all truck events" returns every truck detection with its entity; query
latency stays in milliseconds at 200k frames (docs/results/benchmark.md).

### R3. Grounded analyst with whole-video context (must)
* Tools: `query_frames`, `entity_history`, `list_alerts`, `summarize_window`, `drone_status`.
* Answers cite frames as `[fN]`; citations are checked against what the tools returned; when
  the evidence is not there the answer is "Not enough evidence".
* Hierarchical memory (10-minute -> hour -> day summaries) keeps the prompt small however
  long the footage is; follow-up questions keep conversation history.
* Bonus: one-sentence video summary and a daily security briefing.

**Acceptance:** fixed question set in `data/eval/qa_questions.yaml` (plus a held-out set)
scored automatically; unanswerable questions must abstain.

## Out of scope (prototype)

Licence-plate and face recognition, multi-drone coordination, live video decoding, notification
delivery (SMS/push), multi-user access to the web UI (it is a single-operator console). The design leaves room for each (see REPORT, "With more time").
