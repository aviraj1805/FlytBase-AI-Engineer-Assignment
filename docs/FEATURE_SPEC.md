# Feature Spec: Drone Security Analyst Agent

## Summary

A security analyst that runs alongside a docked patrol drone. It watches every frame the drone captures, knows the property (zones, schedules, known vehicles), remembers what it has seen across the day, alerts the moment something needs a human, and answers the owner's questions in plain language.

**Value to property owners: it turns hours of patrol footage into a handful of decisions.** Today a docked drone produces footage that nobody watches until after an incident. The agent watches all of it, raises only what matters (with what to do about it), and makes the rest searchable: "did that truck come back?", "what happened at the gate last night?".

## Users and jobs

| User | Job to be done |
|---|---|
| Property owner / manager | Know immediately when something needs action; get a short daily briefing; check on a specific vehicle or visitor |
| Security operator (remote, often covering many sites) | Triage alerts quickly with context and a recommended action; search footage without scrubbing video |
| Drone operations | Know when the drone itself is unsafe (battery, geofence, wind, altitude) |

## Key requirements

### R1: Real-time, actionable alerts

The agent must evaluate every frame against predefined security and flight-safety rules and raise an alert within the same processing step.

- Security: suspicious behaviour (climbing, tampering, masked, break-in tools), loitering (described or measured by dwell time), people at night (higher severity at the perimeter), people in restricted zones after hours, unrecognised vehicles at night, the same vehicle entering repeatedly, gates/doors left open after hours.
- Flight safety: low/critical battery, geofence breach, below minimum patrol altitude, wind above the safe limit.
- Every alert carries severity, time, zone, the entity involved and a **recommended action**.
- Noise control: repeats suppressed by cooldown, overlapping rules merged into one alert, whitelisted vehicles and uniformed staff never alert.

Acceptance: "Person loitering at main gate, 00:01." raises a HIGH alert; the same person at 12:00 raises nothing; the owner's own car at 04:50 raises nothing.

### R2: Memory of who and what, queryable frame by frame

Every frame must be logged with context and indexed so it can be found later by time, object, zone, identity or free text.

- Detections are structured (category, label, colour, make/model, clothing, carried items, action, zone).
- Entities are re-identified across frames ("Blue Ford F150" is one identity with three visits, not seven unrelated detections).
- Logs record changes ("Blue Ford F150 spotted at garage, 12:00."), not every frame.
- Index queries: "show all truck events", "people at the main gate between 00:00 and 06:00", full-text "masked fence".

Acceptance: "show all truck events" returns the 7 F150 detections; "how many times did the blue F150 enter?" returns 3 visits with times.

### R3: An analyst you can talk to

The agent must answer follow-up questions grounded in the indexed footage, summarise the day, and recommend actions.

- One-sentence video summary plus highlights.
- Multi-turn Q&A with follow-ups ("when did *it* leave?").
- Prioritised recommendations: P1 act now, P2 verify today, P3 harden the site or the patrol.
- Works with no network (deterministic analyst), and with Claude when available.

Acceptance: "What objects were in the video?" lists vehicles, people, animals, objects and open access points with counts.

## Non-functional requirements

| | Target | Prototype |
|---|---|---|
| Latency per frame (deterministic path) | < 50 ms | ~0.3 ms (3,000+ frames/s) |
| Index query latency | < 100 ms | < 1 ms structured, ~40 ms full-text at 100k frames |
| Runs offline on dock hardware | required for alerting | core is standard-library Python |
| Degrades gracefully | LLM failure must not stop alerts | every LLM component has a deterministic fallback |

## Out of scope for the prototype

Licence-plate recognition, face recognition / identity whitelisting, multi-drone coordination, push notifications (SMS/app), and a web dashboard. The architecture leaves room for each (see [ARCHITECTURE.md](ARCHITECTURE.md#scaling-beyond-the-prototype)).
