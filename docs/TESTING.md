# Testing and validation

```bash
python -m pytest                 # 87 tests in ~2 s; no network, no API key
python -m pytest -k rules -v     # one area, verbose
python -m drone_security benchmark --frames 100000
```

84 tests need only the standard library (+ Pydantic for the LLM-component tests). The 3 LangChain wiring tests run when `requirements.txt` is installed and are skipped otherwise.

## Strategy

| Layer | What is verified | How |
|---|---|---|
| Unit | Caption parsing, re-identification, visit/dwell logic, each rule, index queries | Hand-written frames with exact expected outputs |
| Scenario (end-to-end) | The scripted 24-hour demo day reproduces the brief's sample outputs and a fixed alert profile | `demo_run` fixture runs the full pipeline once per module |
| Agent | Question routing, time windows, follow-ups, LLM-failure fallback, LangChain tool wiring | Offline analyst on the demo index; scripted fake chat model for LangChain |
| LLM components | Schema normalisation, caching, error fallback | Stub client (no network) |
| Scalability | Throughput and query latency under synthetic load; alert noise stays bounded | 5,000-frame test in CI; `benchmark` command for 100k+ |

LLM components are tested with stubs, so the suite is deterministic and free to run. Live behaviour is checked manually with `run --analyzer claude`, `ask` and `chat` when an API key is available.

## Test inventory

| File | Tests | Highlights |
|---|---|---|
| `test_perception.py` | 17 | "Blue Ford F150 parked at garage" → vehicle/truck/blue/Ford/F150/garage; F-150 spelling variants; multi-object captions; negations ("no vehicles or people"); carried items attach to the carrier; open gate/door; counts; a clock time "23:10" is not a count; colour does not bleed between objects |
| `test_tracking.py` | 8 | Same truck = one entity; exit → return = 2 visits; sparse sightings without an exit = 1 visit; dwell accumulates only while continuously observed; different clothing = different people |
| `test_rules.py` | 18 | **Alert at midnight, not at noon** for the same person at the gate; "Person loitering at main gate, 00:01."; dwell-based loitering after 6 min; climbing = CRITICAL; restricted zone after hours; **"Blue Ford F150 entered twice today (09:14, 12:00)"**; whitelisted Camry silent at night; gate open after hours only; battery low → critical escalation; geofence / altitude / wind; cooldown then re-alert after the window; correlation folds overlapping rules; every alert has a recommended action |
| `test_index.py` | 11 | Every frame indexed with telemetry; **"show all truck events"** = 7 rows; time-range, zone and case-insensitive make/model queries; full-text "truck" finds an F150 caption via enrichment tags; AND→OR fallback; severity/time alert filters; nearest telemetry; entity visits; persistence to disk; re-indexing replaces rows |
| `test_pipeline.py` | 8 | Brief's sample outputs on the demo day; alert profile `{CRITICAL 4, HIGH 4, MEDIUM 7, LOW 2}` with suppressions > 0; known vehicle logged with owner but never alerted; parked truck logged once per visit; quiet frames produce no detections; the brief's text format (`Time: 00:01, Location: Gate, Frame 1: ...`) works end to end |
| `test_agent.py` | 11 | "What objects were in the video?"; "Show all truck events"; visit count + **pronoun follow-up** ("When did it leave?"); night filter; time-window alerts; "What happened at 02:15?" → timeline; drone battery at 03:34; P1 < P2 < P3 ordering; one-sentence summary; free-text fallback; **LLM failure degrades to the offline answer** |
| `test_context.py` | 5 | Hourly digests persisted; budget respected with priority sections kept; larger budget → more history; context rebuilt from the database matches the live one; a failing LLM summarizer falls back to the template |
| `test_llm_components.py` | 4 | Structured output normalised (Truck/Blue/ford/F-150 → truck/blue/Ford/F150); cache hit on repeated captions; invalid zone → telemetry zone; API error → rule parser |
| `test_langchain_agent.py` | 3 | All 9 tools exposed with descriptions; tool executed and result fed back; history kept across turns |
| `test_scalability.py` | 2 | 5,000 frames > 300 frames/s with every query < 250 ms (loose CI bounds); cooldown absorbs > 50 % of repeat alerts under random load |

## Scenario coverage: the demo day

`data/scenarios/demo_day.json` is a scripted 24-hour patrol (41 scripted frames + 26 background patrol frames = 67) covering dynamic inputs and emergency responses:

| Time | Situation | Expected response | Result |
|---|---|---|---|
| 00:01–00:09 | Person in dark hoodie at main gate, 5 frames | HIGH loitering alert at 00:01; HIGH dwell alert at 00:07 ("for 6 min"); repeats suppressed | ✅ |
| 02:14–02:20 | Masked intruder climbs back fence, runs to warehouse, tampers with door, door left ajar | CRITICAL ×3 (climbing, running while masked at night, tampering), MEDIUM door open | ✅ |
| 03:10 | Drone drifts 379 m from dock | HIGH geofence | ✅ |
| 03:30 / 03:34 | Battery 22 % → 14 % | MEDIUM → HIGH "return to dock now" | ✅ |
| 04:50 | Property manager's white Camry at night | Logged "(known: Property manager)", no alert | ✅ |
| 07:55 | Uniformed guard at gate | Logged, no alert | ✅ |
| 09:14–17:00 | Blue F150: enters, parks, leaves; returns 12:00; returns 16:45 | 3 visits; LOW "entered twice today (09:14, 12:00)" then "3 times" | ✅ |
| 10:05 | Courier + package + idling van | 3 detections, logs, no alert (daytime) | ✅ |
| 13:00 / 15:10 | Drone at 8 m; wind 12.4 m/s | MEDIUM altitude; MEDIUM wind | ✅ |
| 22:45 | Main gate left open | MEDIUM access-open | ✅ |
| 23:10–23:25 | Black SUV, headlights off; 2 people try garage door handles | MEDIUM unknown vehicle; MEDIUM night presence; CRITICAL tampering | ✅ |
| Background | Quiet patrol frames every 30 min | No detections, no security alerts | ✅ |

## Benchmark results

Laptop (Windows 11, Python 3.10, single process), `python -m drone_security benchmark`:

| Frames | Pipeline throughput | DB size | Truck events | People at gate 00–06 | Full-text | HIGH+ alerts | Nearest telemetry | Inventory |
|---|---|---|---|---|---|---|---|---|
| 20,000 | 4,575 frames/s | 15.6 MB | 1.1 ms | 0.7 ms | 8.2 ms | 0.4 ms | 0.06 ms | 22 ms |
| 100,000 | 3,161 frames/s | 80.2 MB | 0.6 ms | 0.6 ms | 42 ms | 0.4 ms | 0.04 ms | 128 ms |

(median query latency; 100,000 frames ≈ 3.5 days of footage at one frame every 3 s)

Structured queries stay flat because they are index range scans. Full-text and the whole-footage inventory grow linearly; the fixes for production are time-partitioned FTS tables and incrementally maintained counts.

## Defects found by testing and smoke runs

| Defect | Found by | Fix |
|---|---|---|
| "Blue Ford F150 entered twice today" fired when the truck *left* at 09:40 (time-gap visit splitting under sparse patrol sampling) | Demo-day run | Visits follow observed enter/exit behaviour (`test_sparse_patrol_sightings_without_exit_stay_one_visit`) |
| "no vehicles or people" detected people | Parser smoke test | Negation carries across coordinated lists |
| A rule folded into a correlated alert re-fired on the next frame | Code review of the engine | Cooldown filter → record all fresh alerts → correlate |
| "Person in dark hoodie near a truck" gave the truck the colour "dark" | Parser smoke test | Colour read only from the two words before the noun |
| "At 23:10 a Camry" read 10 as a count | Parser smoke test | Counts ignore digits following ":" |
| "Drone docked and charging" detected a drone object | Demo-day run | Removed "drone" from the object lexicon |
| "Was the gate left open?" routed to entity history ("left") | Agent smoke test | `left` not followed by `open`; infrastructure subjects use detection search |
