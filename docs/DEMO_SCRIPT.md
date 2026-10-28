# Demo script

Two recordings: **Video 1** (about 7 minutes) walks through the system with `python make.py demo`; **Video 2**
(about 3 minutes) shows the Gemini paths (agent, VLM on real photos, results). Each scene below matches a
`SCENE n` banner in the console. Timings are targets; the talking points are written to be read aloud.

## Before recording

```bash
python make.py setup            # once
python make.py test             # show "128 passed" if you want it on screen
```

* Terminal at least 110 columns wide, font size 14+, dark background.
* Run `python -m drone_security demo --pause --offline` for Video 1. `--pause` waits for Enter between
  scenes so you control the pace. (Without `--offline` the Q&A scene uses Gemini when a key is set.)
* Have `docs/diagrams/architecture.png` and `docs/results/benchmark_context.png` open in an image viewer.

---

## Video 1: the system (offline)

### Intro (0:00-0:30), show architecture.png

> "This is a security analyst for a docked drone that patrols one property every day. It reads telemetry
> and video frames, turns each frame into objects, tracks who comes and goes, raises explained alerts in real
> time, indexes everything for search, and answers questions with evidence. Everything you will see runs
> offline on a laptop. Gemini plugs in behind the same interfaces, and I will show that in the second video."

### Scene 1: the feed (0:30-1:10)

> "The input is one simulated day at a logistics yard: 646 frames, each a caption plus telemetry: GPS,
> altitude, battery. It is generated from a script and a seed, so it is reproducible, and the script also
> writes the ground truth: 20 incidents a correct system must alert on, and 7 traps that must stay silent.
> The ground truth goes to a separate file the system never reads. That is what lets me measure, not claim."

### Scene 2: real-time processing, night shift (1:10-2:30)

> "Now the frames stream through the pipeline. At one minute past midnight: a person in a dark hoodie
> loitering at the main gate, a HIGH alert because it is night. Every alert explains itself: these are the
> exact rule conditions that passed, and these are the evidence frames."
>
> "At 02:14 the serious one: a masked person climbing the back fence, CRITICAL. One minute later the same
> person is in the restricted warehouse, then forcing the side door, a second intrusion because it is a new
> access point. The drone follows, its GPS drifts, it leaves the geofence, and the battery runs low. Those
> are drone-health alerts on the same channel."

### Scene 3: the rest of the day (2:30-3:10)

> "For the rest of the day I only show alerts. A bag left at the gate for 19 minutes. The same blue Ford F150
> entering for the second time today. The tracker re-identified it, and the explanation says with what
> confidence. The gate left open at night, an unknown SUV, two people trying the garage door handles."
>
> "Notice the count at the bottom: 67 raw rule firings became 19 alerts. Cooldown and correlation turn a
> stream of repeats into one alert per incident step."

### Scene 4: the frame index (3:10-4:00)

> "Every frame is in SQLite. 'Show all truck events' is an indexed lookup: here is the F150 with its entity
> id, and the blue Silverado is a different entity, even though both are blue pickups. Queries by zone and
> time work the same way. Free text uses hybrid search: keyword matching plus vectors, merged by rank, so
> 'person climbing the fence' finds the right frames even when the wording differs."

### Scene 5: the analyst agent (4:00-5:20)

> "Now I ask questions. 'What objects were in the video?' The agent picks a tool and answers with frame
> citations in square brackets. 'How many times did the blue Ford F150 enter?' Twice, with the frames.
> Follow-up: 'When did it leave?' It remembers what 'it' is. 'Was the Silverado the same vehicle?' No, and it
> shows both identities."
>
> "And the important one: 'What was the licence plate?' The footage never records plates, so it says
> 'not enough evidence' instead of guessing. Every citation is checked against what the tools actually
> returned."

### Scene 6: summaries (5:20-6:00)

> "The one-sentence summary of the whole day, then a daily briefing for the owner: overnight, daytime,
> drone health, actions. And this is how the agent sees a long video: a hierarchical memory of ten-minute,
> hourly and daily summaries. For this day it is 1,600 tokens instead of 22,000 for pasting every frame."

### Scene 7: measured against ground truth (6:00-6:40)

> "Five different seeds of the same day: all 20 incidents alerted, no false alerts, re-identification
> correct. I want to be honest about this number: the parser was built against these captions, so this is
> a development score. The report has the tests that are harder: held-out questions the system was never
> tuned on, and re-identification when perception loses detail."

### Scene 8: scale (6:40-7:20), then show benchmark_context.png

> "Last, ten thousand frames, sixteen simulated days, ingested live in under two seconds. Lookups stay
> under a millisecond. The full benchmark goes to two hundred thousand frames, about ten months of patrols:
> still about five thousand frames a second, and the agent's context stays around 2,400 tokens while
> pasting every frame would need almost seven million. That is the point of the hierarchical memory."

---

## Video 2: Gemini (needs `GEMINI_API_KEY`)

### Scene A: model choice (0:00-0:30)

`python -m drone_security models`

> "The code does not hard-code a model. It lists what this key can use and picks the newest stable Flash
> model, fast and able to read images. The choice is saved with the list it came from."

### Scene B: real images (0:30-1:30)

`python -m drone_security vlm`

> "Seven real photos from Wikimedia Commons: a blue F-150 in traffic, a parcel on a doorstep, two men
> climbing a fence, a black SUV in a driveway, a fox, a forklift, an empty lane. Gemini writes a caption and
> structured objects for each, and they go through exactly the same rules. The fence climbers at 02:14
> raise an intrusion alert; the SUV at 23:10 an after-hours vehicle alert; the empty lane raises nothing."

### Scene C: the Gemini agent (1:30-2:30)

`python -m drone_security chat`, then ask:
"What happened in the hour after the main gate was left open?", "Did the gardener trigger any alerts?",
"When did it leave?" (after asking about the F150), "What was the licence plate of the black SUV?"

> "These are questions the offline router gets wrong or cannot route. The Gemini agent decides which tool to
> call, drills down when it needs detail, and still cites frames; the citation check runs after every answer."

### Scene D: results (2:30-3:00), show docs/results/qa_gemini_subset_eval.md and paraphrase_eval.md

> "Two measured results. Context: the same questions answered with the whole video pasted into the prompt, and
> with the hierarchical memory plus tools. Both get five of six right, but the hierarchy uses about five
> thousand tokens per question instead of forty-six thousand, and neither invents a citation. Perception:
> when Gemini rewords every caption, the rule parser misses a quarter of the incidents, while the Gemini
> analyzer still catches all twenty. That is why the parser is the fast fallback and the LLM is the
> robust path. All numbers come from runs saved in the repo."

---

## Recording checklist

- [ ] Video 1 recorded and uploaded (unlisted link)
- [ ] Video 2 recorded and uploaded, or skipped if no key (say so in the submission)
- [ ] Links added to `SUBMISSION_CHECKLIST.md`
