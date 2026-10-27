# Architecture

## 1. System overview

```mermaid
flowchart TB
    subgraph Sources
        SIM["Simulator<br/>scenario JSON / TXT<br/>+ telemetry synthesizer"]
        CAM["Drone camera<br/>video file / image folder"]
    end
    subgraph Perception
        VLM["VLM captioner<br/>Claude vision | BLIP (local)"]
        AN["Frame analyzer<br/>RuleBasedAnalyzer (default)<br/>ClaudeFrameAnalyzer (structured outputs, cache, fallback)"]
    end
    subgraph Understanding
        TR["Entity tracker<br/>appearance signature → identity<br/>visits (enter/exit aware), dwell time"]
        LOG["Event logger<br/>logs changes, not frames"]
        RE["Rule engine<br/>12 rules → cooldown → correlation"]
    end
    subgraph Memory
        IDX[("Frame index — SQLite<br/>frames · detections · entities<br/>events · alerts · summaries<br/>+ FTS5 (BM25)")]
        CTX["Context manager<br/>hourly digests · budgeted working memory"]
    end
    subgraph Interaction
        AG["SecurityAnalyst<br/>LangChain create_agent + Claude<br/>↘ fallback: OfflineAnalyst"]
        CLI["CLI: run · query · alerts · ask · chat · summary · video · benchmark"]
    end

    SIM -->|caption + telemetry| AN
    CAM --> VLM -->|caption + telemetry| AN
    AN -->|detections| TR
    TR --> LOG & RE
    LOG --> IDX
    RE -->|alerts| IDX
    RE -->|real-time| CLI
    TR --> CTX
    CTX -->|hourly digests| IDX
    CTX -->|system prompt| AG
    IDX -->|9 retrieval tools| AG
    AG <--> CLI
```

Each frame is processed exactly once, in order, by a single `SecurityPipeline.process(frame)` call. There is no shared mutable state outside the pipeline object, so one pipeline per drone scales horizontally.

## 2. Per-frame sequence

```mermaid
sequenceDiagram
    autonumber
    participant D as Drone feed
    participant P as Pipeline
    participant A as Analyzer
    participant T as Tracker
    participant R as Rule engine
    participant I as Frame index
    participant C as Context
    D->>P: Frame(caption, telemetry)
    P->>A: analyze(frame)
    A-->>P: [Detection(vehicle, truck, blue, Ford, F150, parked, garage)]
    P->>T: observe(detection, t)
    T-->>P: TrackUpdate(entity V-002, new_visit=true, dwell=0s)
    P->>P: log "Blue Ford F150 spotted at garage, 12:00."
    P->>R: evaluate(frame, observations)
    R->>R: 12 rules → drop alerts in cooldown → correlate per entity
    R-->>P: [LOW "Blue Ford F150 entered twice today (09:14, 12:00)"]
    P->>I: add(frame, detections, logs, alerts) + upsert entities
    P->>C: observe(result) (closes hourly chunk → digest)
```

## 3. Components and the reasoning behind them

### 3.1 Ingestion: one representation for real and simulated footage

All footage becomes a `Frame(caption, telemetry)`. The simulator produces captions directly; real footage is sampled (one frame every *N* seconds, since consecutive frames at 30 fps are near-duplicates) and captioned by a VLM. Everything downstream is identical, so the VLM is a swappable component and the deterministic pipeline can be tested without images.

The **telemetry synthesizer** places the drone at each zone's GPS coordinates with jitter, derives heading and speed from consecutive fixes, models battery drain per sortie, and maps GPS back to zones with a haversine lookup. Any field can be overridden per frame to script safety events (battery 14 %, geofence breach, 8 m altitude, 12.4 m/s wind).

### 3.2 Perception

| Analyzer | When | Properties |
|---|---|---|
| `RuleBasedAnalyzer` | default; fallback for the LLM | Mention-based parser: finds entity mentions (vehicle models/types, people, animals, objects, open access points), reads modifiers before each (colour, make, count) and the action/zone after it, up to the next mention. Handles negation across lists ("no vehicles or people"), carried items ("person carrying a box" → attribute, not a second object), model spelling variants (F150 / F-150 / F 150). ~0.1 ms per caption. |
| `ClaudeFrameAnalyzer` | `--analyzer claude` | Claude structured outputs into a Pydantic schema (guaranteed-valid JSON). LRU cache keyed on caption + zone (patrol footage repeats heavily). Per-frame fallback to the rule parser on any error. |
| `ClaudeVisionCaptioner` / `BlipCaptioner` | `video` command | Security-focused captions: people (clothing, carried items, action), vehicles (colour, make/model, entering/leaving/parked), animals, open gates/doors. BLIP runs locally on the dock GPU; Claude gives richer detail. |

### 3.3 Tracking: identity over boxes

```mermaid
stateDiagram-v2
    [*] --> OpenVisit: first sighting
    OpenVisit --> OpenVisit: seen again within 6 h - same visit
    OpenVisit --> ClosedVisit: seen leaving
    ClosedVisit --> OpenVisit: seen again after 15 min or seen entering - NEW visit
    OpenVisit --> NewVisit: seen entering after a 15 min gap (exit missed)
    OpenVisit --> NewVisit: not seen for 6 h
    NewVisit --> OpenVisit
```

- **Signature**: vehicles by type/colour/make/model; people by clothing colour + clothing items (+ masked); anonymous people by zone, so two undescribed figures at opposite ends of the property don't merge.
- **Visits follow behaviour, not just time.** The first version split visits on a 15-minute gap; with 30-minute patrol passes, a truck that was parked the whole time was counted as entering again when it was next seen *leaving*. Visits now close on an observed exit and reopen on the next sighting or an observed entry.
- **Dwell time** accumulates only while the entity is observed continuously (consecutive sightings within 15 min, same zone). It drives the loitering rule independently of caption wording.

### 3.4 Rules: actionable, not noisy

| Rule | Severity | Fires when |
|---|---|---|
| R-SUSPICIOUS | CRITICAL / HIGH | climbing, tampering, peering; masked or carrying break-in tools |
| R-LOITER | HIGH night / MEDIUM day | caption describes loitering |
| R-DWELL | HIGH night / MEDIUM day | same person in one zone ≥ 5 min (perimeter only during business hours) |
| R-RESTRICTED | HIGH | person in a restricted zone outside business hours |
| R-NIGHT-PERSON | HIGH perimeter / MEDIUM elsewhere | person on site 22:00–06:00 (uniformed staff exempt) |
| R-VEHICLE-NIGHT | MEDIUM | non-whitelisted vehicle arrives at night |
| R-REPEAT-VEHICLE | LOW | non-whitelisted vehicle's *n*-th visit today (n ≥ 2) |
| R-ACCESS-OPEN | MEDIUM | gate/door open outside business hours |
| R-BATTERY | MEDIUM ≤ 25 % / HIGH ≤ 15 % | airborne |
| R-GEOFENCE | HIGH | drone farther than the geofence radius from the dock |
| R-ALTITUDE | MEDIUM | patrol altitude below minimum |
| R-WIND | MEDIUM | wind ≥ safe limit while airborne |

```mermaid
flowchart LR
    A[All rules fire<br/>on the frame] --> B{Same dedupe key fired<br/>within cooldown at<br/>same or higher severity?}
    B -- yes --> S[Suppress<br/>count it]
    B -- no --> C[Record as fired]
    C --> D{Several alerts for<br/>the same entity?}
    D -- yes --> E[Keep most severe<br/>others → related_rules]
    D -- no --> F[Raise]
    E --> F
```

Cooldown keys are rule-specific: per entity for most rules, per entity *and action* for R-SUSPICIOUS (climbing → tampering is an escalation worth a new alert), per visit count for R-REPEAT-VEHICLE, per level for R-BATTERY (low → critical always fires). Thresholds, zones, hours and the whitelist live in `config/property.json`.

### 3.5 Frame index: SQLite + FTS5

```mermaid
erDiagram
    FRAMES ||--o{ DETECTIONS : contains
    FRAMES ||--o{ EVENTS : produces
    FRAMES ||--o{ ALERTS : triggers
    ENTITIES ||--o{ DETECTIONS : "identified as"
    FRAMES ||--|| FRAMES_FTS : "indexed in"
    FRAMES {
        int frame_id PK
        text ts "indexed"
        text description
        text zone "indexed (zone, ts)"
        real lat_lon_altitude
        real battery_wind_speed
        text mode
    }
    DETECTIONS {
        int id PK
        int frame_id FK
        text ts "denormalised"
        text category "indexed (category, ts)"
        text label "indexed (label, ts)"
        text color_make_model
        text action
        text zone "indexed (zone, ts)"
        text entity_id "indexed (entity_id, ts)"
    }
    ENTITIES {
        text entity_id PK
        text signature
        text display_name
        int visit_count
        json visits
    }
    ALERTS {
        int alert_id PK
        text ts "indexed"
        int severity "indexed (severity, ts)"
        text rule_id
        text message
        text recommended_action
    }
    EVENTS {
        int id PK
        text ts "indexed"
        text message
    }
    SUMMARIES {
        int id PK
        text level "chunk | day"
        text period_start
        text text
    }
    FRAMES_FTS {
        text description
        text tags "enrichment"
    }
```

- **Two ways in.** Structured queries are `(column, ts)` B-tree range scans ("all truck detections 09:00–13:00"). Full-text search is FTS5 with BM25 over the caption **plus enrichment tags** written by the analyzer (`vehicle truck pickup blue ford f150 parked garage`), so "truck" finds a frame captioned "Blue Ford F150 parked at garage".
- `ts` is denormalised onto detections so time-filtered object queries skip the join; values are stored lower-case so equality uses the index.
- WAL mode + batched commits; re-indexing a frame (e.g. re-captioned by a better VLM) replaces its derived rows.
- Why not a vector DB: captions are short and vocabulary-constrained, so BM25 plus synonym tags covers the query space at zero extra infrastructure. Image-embedding search (CLIP) is the natural next step for real footage (see the report).

### 3.6 Context management and the agent

```mermaid
flowchart LR
    subgraph WMEM["Working memory (system prompt, character budget)"]
        P[Property profile] --> AL[High-severity alerts] --> KE[Key entities] --> DG[Hourly digests<br/>newest first] --> LF[Latest frames]
    end
    subgraph LTM["Long-term memory (frame index tools)"]
        T1[search_frames]
        T2[find_detections]
        T3[get_alerts]
        T4[entity_report]
        T5[object_inventory]
        T6[timeline]
        T7[drone_status]
        T8[video_summary]
        T9[recommendations]
    end
    Q[Operator question] --> AGENT[LangChain create_agent<br/>ChatAnthropic claude-opus-5-5]
    WMEM --> AGENT
    AGENT -->|tool calls| LTM
    AGENT --> ANS[Grounded answer]
```

- **Hierarchical summarisation**: the context manager closes a digest every hour of footage as frames stream in (template by default; any `Chunk -> str` summarizer, e.g. an LLM, can be plugged in, and a failing summarizer falls back to the template). Digests are also persisted to the index.
- **Budgeted rendering**: sections are added in priority order until the character budget is spent; truncated sections end with "… (n more, use the search tools)" so the model knows to retrieve. The same block can be rebuilt from the database (`render_from_index`) for questions asked after a run.
- **Agent**: LangChain `create_agent` with `ChatAnthropic(model="claude-opus-5-5", effort="medium")`, the 9 toolkit methods as `StructuredTool`s (docstrings are the tool descriptions), and the full message history kept between turns for follow-ups.
- **Offline analyst**: the same tools behind a deterministic intent router (summary, recommendations, drone status, inventory, entity history, alerts, detections, timeline, full-text fallback), with time-window parsing ("between 23:00 and 23:59", "at night", "at 02:15") and pronoun follow-ups. `SecurityAnalyst` uses Claude when a key is configured and falls back per question on any failure.

## 4. Real-time behaviour

| Stage | Cost per frame (laptop, deterministic path) |
|---|---|
| Rule-based analysis | ~0.1 ms |
| Tracking + rules | ~0.05 ms |
| Index write (batched) | ~0.15 ms |
| **Total** | **~0.3 ms → 3,000–5,000 frames/s** |

A drone sampling one frame per second produces 86,400 frames a day; the deterministic pipeline processes that in about 30 seconds. When the Claude analyzer is enabled, caption caching and per-frame fallback keep the alert path from blocking on the network.

## 5. Scaling beyond the prototype

```mermaid
flowchart LR
    subgraph EDGE["Dock edge computer (per site)"]
        CAM2[Camera + telemetry] --> VLM2[Local VLM<br/>BLIP / small VLM]
        VLM2 --> PIPE[Deterministic pipeline<br/>analyze · track · rules]
        PIPE --> LOCAL[(SQLite index<br/>rolling 30 days)]
        PIPE -->|alerts: immediate| PUSH[Push / SMS / VMS]
    end
    PIPE -->|frames + detections, batched| BUS[(Event stream<br/>Kafka / Kinesis)]
    BUS --> PG[(Postgres + pgvector<br/>multi-site index)]
    BUS --> OBJ[(Object storage<br/>keyframes)]
    PG --> AGT[Analyst agent service<br/>Claude]
    AGT --> DASH[Operator dashboard / chat]
```

- Alerting stays at the edge (no network dependency); the cloud adds cross-site search, long retention, re-captioning with stronger VLMs and the conversational agent.
- The SQLite schema maps directly to Postgres; `frames_fts` becomes `tsvector` + GIN, and CLIP image embeddings go into pgvector for visual search.
- Per-site pipelines are independent (one tracker + rule engine per drone), so throughput scales with sites.
