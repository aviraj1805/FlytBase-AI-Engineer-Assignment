# Testing

Two layers: **automated tests** (`python make.py test`, 128 tests, about 1 minute, no network) check that
each component does what it claims, and **experiments** (`python make.py eval`, `benchmark`, `eval-gemini`)
measure how well the whole system works against ground truth. Experiments write their raw output to
`docs/results/`, so every number in the README and report can be traced to a file and a command.

## Strategy

| Level | What it proves | Where |
|---|---|---|
| Unit | Parser, rules, tracker, index, retrieval, memory, tools behave as specified, including failure paths | `tests/test_perception.py`, `test_rules.py`, `test_tracking.py`, `test_index.py`, `test_retrieval_memory.py`, `test_agent.py` |
| Edge cases | Messy feeds never crash the pipeline or produce wrong alerts | `tests/test_ingest_edge_cases.py` |
| Scenario (ground truth) | The scripted day is alerted correctly; benign traps stay silent; the F150 is one entity with two visits and the Silverado is separate | `tests/test_scenario.py` |
| LLM integration (no network) | Gemini model selection, key handling, retries, output normalisation, batching, caching, fallbacks, the LangChain tool loop and citation checks, using fake clients | `tests/test_gemini.py`, `tests/test_agent.py` |
| Measurement code | Grading, ranking metrics, VLM scoring, ablations, paraphrase experiment, benchmark | `tests/test_evaluation.py` |
| Experiments | Precision/recall/F1, retrieval quality, Q&A accuracy and context size, robustness, scale | `src/drone_security/evaluation/`, results in `docs/results/` |

Principles: the pipeline under test never reads the ground truth; tests build frames through the same ingest
path as real feeds (`make_frame` in `tests/conftest.py`); LLM code is tested with fakes so the suite is
deterministic and free, and the live Gemini behaviour is measured separately by `make eval-gemini`.

## Test cases (by file)

| File | Tests | Highlights |
|---|---|---|
| test_perception.py | 17 | colour/make/model variants ("F-150", "F 150"), clothing, masked intruders with tools, multiple objects per caption, negation before and after the noun, carried items attach to the person, open doors/gates, counts, clock times not read as counts |
| test_rules.py | 27 | the rules file loads; invalid rules rejected with a clear message; every rule fires with the expected explanation and evidence frames; day vs night severity; whitelists (uniform, known vehicle); unattended object needs time and nobody nearby; GPS drift by HDOP and by impossible jump; geofence threshold read from config; cooldown, escalation, correlation; no telemetry -> no drone alerts but security rules still run |
| test_tracking.py | 13 | same vehicle = one entity; vague sighting soon after joins; vague sighting after the vehicle left does not; full description after a gap = return visit; conflicting make keeps two blue pickups apart; ambiguous match lowers confidence; clothing colour separates people; groups vs individuals; static objects; dwell resets; evidence frames; re-ID horizon |
| test_ingest_edge_cases.py | 10 | malformed records quarantined with reasons; broken telemetry dropped, frame kept; missing telemetry; duplicates (same id or same content); out-of-order frames reordered; frames later than the buffer indexed but not tracked; **midnight rollover** (dwell continues across midnight, daily visit counts reset); **empty day**; the brief's text format |
| test_scenario.py | 9 | determinism per seed; frames carry no labels and truth is written separately; scripted events present; simulated battery crosses thresholds inside the truth windows; multi-day stream; **alerts match ground truth (P = R = 1.0, seed 7)**; F150 one entity with two visits, no merges; benign situations raise nothing; scoring catches a wrong zone |
| test_index.py | 9 | every frame indexed with telemetry; "show all truck events"; time range, zone, make/model, entity queries; full text via enrichment tags; alerts store explanation and evidence; entity visits; telemetry near a time and extremes; persistence; re-indexing replaces detections |
| test_retrieval_memory.py | 8 | TF-IDF matches spelling variants; RRF; hybrid vs keyword; filters; summaries at every level with citations; hour summary cites its alerts; context within budget and far smaller than the whole video; live and rebuilt memory agree |
| test_agent.py | 14 | inventory, truck events, visit count + pronoun follow-up, Silverado vs F150, alert questions, time windows, drone status, briefing and summary, **abstention** on unanswerable questions, every offline citation points at a retrieved frame, exactly five tools, LangChain loop with a scripted fake model, made-up citations flagged, LLM failure degrades to the offline answer |
| test_gemini.py | 11 | newest stable Flash model chosen from a listed set; override; key from `.env`; offline flag wins; 429 retried; output normalised to the parser vocabulary; captions batched and cached; API failure falls back to the parser; VLM returns caption + objects; usage accounting; real client needs a key |
| test_evaluation.py | 10 | regex grading, ranking metrics, ground-truth relevance, offline Q&A never cites unseen frames, VLM check scoring with a fake VLM, Gemini summarizer wiring, attribute dropout, v1 tracker baseline, paraphrase experiment, benchmark smoke |

## Scenarios

The scripted day (`data/scenarios/day_script.yaml`, seed 7 for the demo) contains 18 episodes:

| Time | Episode | Expected alerts |
|---|---|---|
| 00:01-00:09 | Person in dark hoodie loitering at the main gate | LOITERING, AFTER_HOURS_PERSON |
| 01:30, 05:40, 19:30 | Fox, deer, dog | none |
| 02:14-03:02 | Masked intruder climbs the back fence, forces the warehouse door, climbs back out; drone follows, GPS drifts, leaves the geofence | INTRUSION x3, RESTRICTED_ZONE, AFTER_HOURS_PERSON, ACCESS_OPEN, GPS_DRIFT, GEOFENCE |
| 03:07-03:40 | Extended sortie drains the battery | BATTERY_LOW, BATTERY_CRITICAL |
| 05:50 | Uniformed guard closes the door | none (staff) |
| 07:50 | Property manager's white Toyota Camry | none (known vehicle) |
| 09:14-09:42 | Blue Ford F150, first visit | none |
| 10:05-10:17 | Courier delivers a package, collected after 12 minutes | none (below 15 min) |
| 11:00-11:25 | Blue Chevrolet Silverado (decoy for re-identification) | none |
| 12:30 | Workers and forklift in the restricted warehouse | none (business hours) |
| 13:00 | Inspection pass at 8 m | LOW_ALTITUDE |
| 14:30-15:20 | Bag left at the main gate | UNATTENDED_OBJECT |
| 15:10 | Wind gusts | HIGH_WIND |
| 15:40 | Visitor waits 2 minutes at the gate | none (below 5 min) |
| 16:45-17:10 | Same blue Ford F150 returns | REPEAT_VEHICLE |
| 22:45 | Main gate left open | ACCESS_OPEN |
| 23:10-23:25 | Black SUV, two people try the garage door handles | AFTER_HOURS_VEHICLE, AFTER_HOURS_PERSON, INTRUSION |

## How to reproduce

```bash
python make.py setup
python make.py test                 # unit + scenario + edge-case tests
python make.py eval                 # writes docs/results/eval_scenario_rules.*, retrieval_eval.*, ablation.*,
                                    # qa_context_eval.*, qa_heldout_eval.* (and paraphrase_eval.* if the
                                    # reworded captions exist)
python make.py benchmark            # docs/results/benchmark.* and benchmark_*.png (about 2 minutes)
python make.py eval-gemini          # with GEMINI_API_KEY: gemini_models.json, vlm_check.*, Gemini rows in
                                    # retrieval_eval, eval_scenario_gemini.*, paraphrase_eval.*, qa_*_eval.*
```

Single experiments: `python -m drone_security eval --seeds 10`, `eval-retrieval [--gemini]`, `ablation`,
`eval-qa [--gemini] [--heldout]`, `vlm`, `eval-paraphrase [--make] [--gemini]`, `benchmark --frames 10000`.

## Latest results

Generated on 2026-10-28 with the commands above (raw files in `docs/results/`, each with a provenance header).

| Experiment | Result |
|---|---|
| Test suite | 128 passed |
| Scenario, rule parser, 10 seeds | alert P 1.00 / R 1.00 / F1 1.00; detection P 0.991 / R 1.00; re-ID F1 1.00; 19 alerts for 20 incidents per seed |
| Re-ID ablation (25% attributes dropped) | F1 0.917 vs 0.430 for the v1 exact-signature matcher |
| Noise control | 19 alerts/day with cooldown + correlation, 21 with cooldown only, 67.4 with neither; recall 1.00 in all three |
| Retrieval (22 queries, nDCG@10) | hybrid 0.846, keyword 0.822, TF-IDF 0.768 |
| Offline Q&A | dev set 20/20 (tuned on it); held-out 7/12; 0 answers with made-up citations |
| Context size, one day | 1,622 tokens hierarchical vs 21,763 naive |
| Benchmark, 200k frames | 4,976 frames/s ingest, 96.5 MB, indexed queries < 1 ms, hybrid text 7.2 ms |
| Gemini experiments | GEMINI_TESTING_ROW |

## Known gaps

* Q&A grading uses regular expressions on small question sets; it can miss a correct answer phrased
  unexpectedly (the per-question answers are stored in the JSON files for manual review).
* The scripted day reuses one paraphrase pool across seeds, so seed-to-seed variance is small.
* The VLM check uses seven images; it shows the path works on real photos, not a statistically strong
  accuracy estimate.
