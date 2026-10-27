# Demo video plan

Five short screen recordings with voiceover (the brief requires narration). Each one maps to an assessment goal. Commands assume the repository root and an activated virtualenv; set `ANTHROPIC_API_KEY` before videos 3 and 5 to show the Claude agent (everything also works offline).

Before recording: `python -m drone_security run --quiet` once so `data/runs/latest.db` exists, and widen the terminal to ~140 columns.

---

## Video 1: Video processing, logs and real-time alerts (≈4 min)

**Show**

```bash
python -m drone_security run --speed 120 --verbose
```

**Narrate**

1. "This is a docked drone patrolling a logistics yard for a full day. Each line is a frame: time, the zone the drone is over, altitude, battery, and the frame caption. In production the caption comes from a vision-language model; here it's simulated, and the same pipeline runs either way."
2. At 00:01: "A person in a dark hoodie at the main gate at midnight. The analyzer extracted person, clothing, loitering, main gate. The rule engine raised a HIGH loitering alert, and the night-perimeter rule was folded into it by correlation, so the operator gets one alert, not two."
3. At 00:07: "Same person, six minutes later. This alert didn't come from the caption. The tracker measured six minutes of dwell time at the gate. The frames in between were suppressed by the cooldown."
4. At 02:14–02:16: "Masked intruder: climbing the fence, then tampering with the warehouse door. Each escalation is a new CRITICAL alert, and each alert carries a recommended action."
5. At 03:34 / 03:10: "The agent also watches the drone itself: geofence breach, battery critical, return to dock."
6. At 04:50: "The property manager's Camry at night. It's whitelisted, so it's logged with its owner and doesn't alert."
7. At 12:00: "The expected sample output: 'Blue Ford F150 spotted at garage, 12:00', and the alert 'entered twice today (09:14, 12:00)'. The tracker counts visits by observed entries and exits, not by gaps between frames."
8. End: "67 frames, 17 alerts, 10 duplicates suppressed, in milliseconds. Then the video summary and prioritised recommendations."

## Video 2: Frame-by-frame index and context management (≈3 min)

**Show**

```bash
python -m drone_security query --label truck
python -m drone_security query --category person --zone main_gate --from 00:00 --to 06:00
python -m drone_security query --text "masked fence"
python -m drone_security query --text "truck garage"
python -m drone_security alerts --min-severity HIGH
python -m drone_security context --budget 2500
```

**Narrate**

- "Every frame is indexed in SQLite with its telemetry and detections. 'Show all truck events' is an indexed range scan. Note the caption says 'Ford F150', not 'truck': the analyzer tagged it, so full-text search finds it too."
- "Full-text search uses SQLite FTS5 with BM25 ranking."
- Context: "This is what the agent sees in its prompt: property profile, serious alerts, key entities, and hourly digests written while the footage streamed. It has a hard budget; when a section is cut, it tells the agent to use its tools. However long the footage gets, the prompt stays this size."

## Video 3: Agent Q&A, follow-ups, summary, recommendations (≈4 min)

**Show**

```bash
python -m drone_security summary
python -m drone_security chat
```

In `chat`, ask:

1. `What objects were in the video?`
2. `How many times did the blue Ford F150 enter today?`
3. `When did it leave?` *(follow-up: "it" resolves to the F150)*
4. `Were there any people at the main gate at night?`
5. `What happened at 02:15?`
6. `What was the drone battery at 03:34?`
7. `What should I do?`

**Narrate**

- "The agent is a LangChain tool-calling agent on Claude. The grey line shows which tools it called; every answer is grounded in the index."
- On follow-up: "It keeps the conversation history, so 'it' means the F150."
- "Without an API key the same questions are answered by a deterministic analyst using the same tools, so the system never goes dark." *(optionally show `ask --offline ...`)*
- Recommendations: "P1 is act now, P2 is verify today, P3 is harden the site and the patrol: floodlights where night alerts cluster, a mid-patrol recharge because the battery hit 14 %."

## Video 4: Scalability and tests (≈2 min)

**Show**

```bash
python -m drone_security benchmark --frames 100000
python -m pytest -v
```

**Narrate**

- "100,000 frames, about three and a half days of footage, at over 3,000 frames per second on a laptop. Structured queries stay under a millisecond because they're index range scans. Cooldown suppressed about 49,000 duplicate alerts."
- "87 tests: the brief's sample outputs, alert at midnight but not at noon, follow-up questions, LLM failure falling back to offline, persistence, scalability."

## Video 5 (optional): Real footage through a VLM (≈2 min)

**Show**

```bash
python -m drone_security video path/to/clip.mp4 --captioner claude --zone driveway --every 2 --max-frames 15 --save-frames data/frames
```

**Narrate**

- "Same pipeline, real pixels: one frame every two seconds is captioned by Claude vision, then parsed, tracked, alerted and indexed exactly like the simulation. BLIP is the local alternative for running on the dock without network."

---

## Recording checklist

- [ ] Voiceover on every video
- [ ] Terminal font large enough to read at 1080p
- [ ] Upload to Google Drive / YouTube (unlisted) with view access for the reviewers
- [ ] Paste links into `docs/REPORT.md` section 7 and the submission email
