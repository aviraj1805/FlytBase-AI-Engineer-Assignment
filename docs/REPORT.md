# Drone Security Analyst Agent: Report

*Source for the PDF report. Repository: github.com/aviraj1805/FlytBase-AI-Engineer-Assignment (private).*

## 1. Problem and approach

A docked drone patrols one property every day. The footage is only valuable if someone watches it, and nobody watches a full day of patrol video. The goal was a prototype agent that (1) processes telemetry and video frames, (2) identifies and logs objects and events with context, (3) raises real-time alerts from predefined rules, (4) indexes every frame for search, and (5) acts as an analyst that summarises, recommends and answers follow-up questions.

The approach in one sentence: **turn every frame into language early, keep the alerting core deterministic, and use the LLM where language understanding pays off.**

```
frame (caption + telemetry) → analyzer → entity tracker → event logs
                                                        → rule engine → real-time alerts
                              everything → SQLite/FTS5 frame index ← retrieval tools ← analyst agent (Claude)
                              tracker → context manager (hourly digests, budgeted working memory) → agent prompt
```

### Why "language-first"

The brief asks to simulate frames as text ("Frame 1: Blue truck at gate"). Rather than treat that as a shortcut, I made the caption the contract between perception and everything else. A real camera frame is captioned by a VLM; a simulated frame arrives already captioned; from there the path is identical. The VLM becomes swappable (Claude vision for detail, BLIP for local/offline), the analytics can be tested without images, and the index stores something a person and an LLM can both read.

### Why a deterministic core

Real-time alerting on a dock's edge computer cannot depend on network round-trips. Parsing, tracking, rules and indexing are deterministic Python (standard library only) and process ~3,000–5,000 frames/s. Claude handles the parts where rules break down: free-form captions (`--analyzer claude`), image captioning, briefings and open-ended questions. Every LLM component has a fallback: the frame analyzer falls back to the parser per frame, the agent falls back to an offline analyst per question, and LLM digest-writing falls back to a template. A network or API failure degrades answers but never stops alerts.

## 2. Assumptions

| Assumption | Why |
|---|---|
| **Simulated captions are the primary dataset.** A scripted 24-hour demo day (`demo_day.json`, 41 scripted + 26 patrol frames) plus a random generator for load tests | The brief asks for simulation; scripted events give exact expected outputs to test against; no public dataset combines drone telemetry and security events on one property |
| One property, one drone, local time, one footage day per run | Matches a docked-drone deployment; multi-day works (dates are stored) but the demo is one day |
| Telemetry is synthesized from zone positions (GPS jitter, heading/speed from fixes, battery drain per sortie, wind) | Realistic enough to drive geofence/battery/altitude/wind rules; any field can be overridden per frame |
| Captions name the location when it matters ("at main gate"); otherwise the drone's GPS zone is used | A camera often looks at something other than what is directly below; caption location wins |
| Identity = appearance attributes (colour, make/model, clothing) | No licence plates or faces in captions; good enough to demonstrate repeat-visit logic |
| Night 22:00–06:00, business hours 08:00–19:00, loiter 5 min, cooldown 15 min | Typical for a commercial yard; all configurable in `config/property.json` |
| Uniformed people and whitelisted vehicles are authorised | Stands in for badge/face/plate whitelisting |

## 3. Tool and configuration choices

| Choice | Alternatives considered | Reasoning |
|---|---|---|
| **Caption → structured detections**, rule parser by default | Run an object detector (YOLO) directly | The brief's input is text; a lexicon parser is instant, offline and testable. With real video, YOLO boxes could feed the same `Detection` type |
| **Claude (claude-opus-5-5) with structured outputs** for frame analysis | Prompt for JSON and parse it | `client.beta.messages.parse` with a Pydantic schema guarantees valid output; effort `low` for per-frame work; LRU cache because patrol captions repeat |
| **Claude vision vs BLIP** for captioning | CLIP | CLIP scores image-text similarity but does not *describe* a scene; a security log needs "masked person in black jacket climbing the fence", so a generative captioner is required. BLIP runs locally (dock GPU, no data leaves the site) but its captions are generic ("a man standing next to a fence"). Claude vision reads make/model, clothing and actions. Both are behind one `Captioner` interface. CLIP fits better in retrieval: image embeddings for "find frames that look like this", which is future work |
| **SQLite + FTS5** for the frame index | Elasticsearch, a vector DB (Chroma), Postgres | Embedded, zero-ops, ships with Python, runs on the dock. Structured `(column, ts)` indexes give sub-millisecond object/time queries; FTS5/BM25 over captions *plus analyzer tags* gives search ("truck" finds "Blue Ford F150"). Captions are short and vocabulary-constrained, so BM25 + synonyms covers the query space without embeddings. The schema maps 1:1 to Postgres + pgvector |
| **Entity tracker with behaviour-aware visits** | Count detections per label | "Entered twice today" is about identities and visits, not detections. Visits close on an observed exit; dwell time only accumulates while continuously observed |
| **Rule engine with cooldown + correlation** | Fire every rule every frame | Measured: raw rule firings on the demo day are 31, managed alerts 17 (one per incident step, each with a recommended action); on 20k random frames 12,530 raw vs 2,346 managed (5.3x less noise) |
| **LangChain `create_agent` + `ChatAnthropic`** | Hand-written tool loop; LangGraph custom graph | The brief asks for LangChain; `create_agent` gives a tested tool-calling loop. Tools are plain methods shared with the offline analyst, so the agent layer stays thin and swappable |
| **Two-tier memory** (budgeted working memory + retrieval tools) | Put all frames in the prompt; pure RAG | Prompt size stays bounded however long the footage is; the model sees the day's shape (alerts, entities, hourly digests) and fetches specifics on demand |
| **Server-side refusal fallbacks** (`fallbacks="default"`) | None | Security footage mentions break-ins and tools; if a safety classifier declines, the API retries on Anthropic's recommended fallback model instead of failing |

## 4. Results

All numbers below come from `python -m drone_security run` on the demo day and the `benchmark` command.

### 4.1 Frame descriptions → detections

| Caption | Parsed detection(s) |
|---|---|
| Blue Ford F150 pickup truck entering through main gate | vehicle · truck · blue · Ford · F150 · entering · main_gate |
| Masked man in black jacket climbing the back fence carrying bolt cutters | person · black · [jacket, bolt cutters, masked] · climbing · back_fence |
| Courier in red jacket drops off a package at front door; white delivery van idling in driveway | person (delivering, front_door) + package (front_door) + white van (parked, driveway) |
| Empty driveway, no vehicles or people visible | (nothing) |
| Warehouse side door ajar, no people visible | infrastructure · door · open · warehouse |

### 4.2 Logs and alerts (excerpt of the live run)

```
LOG    Person in dark hoodie loitering at main gate, 00:01.
ALERT  HIGH     Person in dark hoodie loitering at main gate, 00:01.
ALERT  HIGH     Person in dark hoodie loitering at main gate for 6 min (since 00:01), 00:07.
ALERT  CRITICAL Masked person in black jacket climbing with bolt cutters at back fence, 02:14.
ALERT  CRITICAL Masked person in black jacket tampering with access point at warehouse, 02:16.
ALERT  HIGH     Drone battery critical (14%) over front door, 03:34.
LOG    White Toyota Camry entered at driveway, 04:50 (known: Property manager).
LOG    Blue Ford F150 spotted at garage, 12:00.
ALERT  LOW      Blue Ford F150 entered twice today (09:14, 12:00), 12:00.
ALERT  MEDIUM   Gate left open at main gate, 22:45.
ALERT  CRITICAL 2 people tampering with access point at garage, 23:20.
```

Demo day totals: 67 frames, 41 detections, 36 log entries, **17 alerts** (4 CRITICAL, 4 HIGH, 7 MEDIUM, 2 LOW), **10 duplicates suppressed**; 13 ms total.

### 4.3 Indexed frames: queryable by time or object

```
$ python -m drone_security query --label truck          → 7 detections, 09:14 … 17:00, all entity V-002
$ python -m drone_security query --text "masked fence"  → 02:14 back_fence "Masked man in black jacket climbing…"
$ python -m drone_security query --category person --zone main_gate --from 00:00 --to 06:00 → 00:01 … 00:09
```

### 4.4 Video summary (bonus)

> Across 67 frames from 00:01 to 23:50, the drone tracked 10 people, 6 vehicles (the Blue Ford F150 visited 3 times) and 3 animals, raising 17 alerts - most serious: masked person in black jacket climbing with bolt cutters at back fence, 02:14.

### 4.5 Agent recommendations

```
[P1] Review footage at the back fence at 02:14 (1 serious alert). Possible intrusion in progress: notify on-call guard and police…
[P1] Review footage at the main gate between 00:01 and 00:07 (2 serious alerts). Hover and keep the person in frame…
[P2] Confirm whether the Blue Ford F150 is expected: it entered 3 times (09:14, 12:00, 16:45). Add it to the whitelist if…
[P2] Identify the vehicle from this alert and pull its plate from the zoom pass: Unrecognised Black SUV at driveway after hours, 23:10.
[P3] Add an extra night patrol waypoint and IR floodlight at the main gate (3 night alerts).
[P3] Shorten the night patrol loop or add a mid-patrol recharge; drone battery critical (14%) over front door, 03:34.
```

### 4.6 Follow-up questions (bonus)

```
you> What objects were in the video?
  vehicle: truck (7 sightings, 1 distinct; e.g. Blue Ford F150); car (4, 2 distinct; Red Honda Civic, White Toyota Camry); suv …
  person: person (17 sightings, 10 distinct …); animal: fox, deer, dog; object: package; infrastructure: gate, door
you> How many times did the blue Ford F150 enter today?
  Blue Ford F150 (V-002) made 3 visits with 7 sightings: 09:14-09:40, 12:00-12:20, 16:45-17:00 …
you> When did it leave?
  Blue Ford F150 was seen leaving at 09:40, 12:20. After its 16:45 arrival it was last seen at 17:00 at the garage (no exit observed).
you> What happened at 02:15?
  Events: climbing at back fence 02:14 · running at warehouse 02:15 · tampering at warehouse 02:16 · door open 02:20 (+ 4 alerts)
```

### 4.7 Scalability

| Frames | Throughput | DB | Object query | Full-text | Alerts | Telemetry lookup |
|---|---|---|---|---|---|---|
| 20,000 | 4,575 frames/s | 15.6 MB | 1.1 ms | 8 ms | 0.4 ms | 0.06 ms |
| 100,000 | 3,161 frames/s | 80.2 MB | 0.6 ms | 42 ms | 0.4 ms | 0.04 ms |

A day at one frame per second (86,400 frames) processes in about 30 s on the deterministic path. Context for the agent stays at the configured budget (6,000 characters by default) regardless of footage length.

### 4.8 Quality

87 automated tests (`python -m pytest`, ~2 s, no network). See [TESTING.md](TESTING.md) for the inventory, the scenario table and the defects that testing caught.

## 5. What I would do with more time

1. **Real footage end to end.** Run the `video` path on public aerial/security datasets (VisDrone, UCF-Crime, Okutama-Action) and measure caption → detection accuracy against labels. Compare Claude vision, BLIP-2 and a small local VLM (e.g. Florence-2 / Qwen-VL) on cost, latency and attribute recall.
2. **Detector + VLM hybrid.** A fast detector (YOLO) on every frame for boxes and tracks; the VLM only on frames where something changes. That cuts VLM calls by orders of magnitude and gives pixel-level tracks for re-identification.
3. **Stronger identity.** Licence-plate OCR, visual re-ID embeddings for people and vehicles, and a CLIP embedding per frame in pgvector for "find frames that look like this".
4. **Video summarisation with clips.** Per-incident keyframe strips and short clips attached to alerts; hourly digests written by the LLM (the hook exists: `ContextManager(summarizer=...)`).
5. **Learning the property.** Baselines per zone and hour (how many vehicles normally enter at 09:00?) so anomalies are statistical, not only rule-based; operator feedback on alerts (true/false positive) to tune thresholds.
6. **Evaluation harness for the agent.** A labelled question set over the demo day, scored automatically, to compare prompts, effort levels and models before changing them.
7. **Deployment.** Edge pipeline + cloud agent as in the [scaling diagram](ARCHITECTURE.md#5-scaling-beyond-the-prototype); push notifications for HIGH/CRITICAL; a web dashboard with the alert feed, timeline and chat.

## 6. How AI tools were used

The prototype was built with **Claude Code (Claude Opus 5.5)** as the main development tool, from the assignment brief to the pushed repository.

| What | AI contribution | My role / verification |
|---|---|---|
| Architecture | Proposed the language-first pipeline, deterministic core with LLM fallbacks, two-tier agent memory, SQLite/FTS5 index; drafted the Mermaid diagrams | Checked each requirement of the brief maps to a component; kept edge/offline operation as a constraint |
| Alert rules (AI-generated component) | Generated the 12 rules with severities, message templates and recommended actions, plus cooldown and correlation | Reviewed severities and thresholds; moved all thresholds into the property config |
| Object/caption logic (AI-generated component) | Generated the lexicon (makes/models, clothing, actions, zones) and the mention-based parser | Iterated on real outputs: negation, carried items, colour bleed, time-as-count |
| Indexing (new domain) | Explained FTS5 (BM25, external tags, tokenizers) and index design for time-range queries; wrote the schema | Benchmarked to 100k frames to confirm the design holds |
| LangChain agent | Wrote the tool layer, `create_agent` integration and the offline router | Verified the current LangChain and Anthropic SDK signatures against their GitHub source (PyPI was too slow to install during the session), which caught two stale patterns |
| QA | Generated the 87-test suite from the brief's examples and edge cases | Every bug found in smoke runs got a regression test |
| Docs | Drafted README, spec, architecture, testing notes, this report and the demo script | Edited for accuracy against actual run output |

Lessons: the most useful loop was *generate → run the demo day → read the output → fix*. The two most significant design changes (behaviour-aware visit counting, and filtering by cooldown before correlating) came from reading pipeline output, not from the first generated design. AI-generated code needed the most scrutiny in two places: behaviour that only shows up over a sequence of frames (tracking), and fast-moving third-party APIs (SDK parameters).

## 7. Demo videos

See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the recording plan. Links:

- Video 1: overview and live processing (logs, alerts, telemetry): *link*
- Video 2: frame index queries and context management: *link*
- Video 3: agent Q&A, follow-ups, summary and recommendations: *link*
- Video 4: scalability benchmark and tests: *link*
- Video 5 (optional): real video with VLM captions: *link*
