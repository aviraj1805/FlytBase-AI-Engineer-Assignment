# Scenario evaluation (rules analyzer)

Produced by `python -m drone_security eval --seeds 10 --analyzer rules` on 2026-10-28 01:47. Raw data: `eval_scenario_rules.json`.

Ground truth: 20 scripted alert incidents and per-frame object labels (data/scenarios/day_script.yaml). Seeds change caption paraphrases, GPS jitter and wind.

## Mean over seeds

| metric | mean | min | max |
|---|---|---|---|
| alert_precision | 1.0 | 1.0 | 1.0 |
| alert_recall | 1.0 | 1.0 | 1.0 |
| alert_f1 | 1.0 | 1.0 | 1.0 |
| detection_precision | 0.991 | 0.991 | 0.991 |
| detection_recall | 1.0 | 1.0 | 1.0 |
| reid_precision | 1.0 | 1.0 | 1.0 |
| reid_recall | 1.0 | 1.0 | 1.0 |
| reid_f1 | 1.0 | 1.0 | 1.0 |

## Per-rule results (seed 10)

| rule | expected incidents | detected | alerts raised | false positives |
|---|---|---|---|---|
| ACCESS_OPEN | 2 | 2 | 2 | 0 |
| AFTER_HOURS_PERSON | 3 | 3 | 2 | 0 |
| AFTER_HOURS_VEHICLE | 1 | 1 | 1 | 0 |
| BATTERY_CRITICAL | 1 | 1 | 1 | 0 |
| BATTERY_LOW | 1 | 1 | 1 | 0 |
| GEOFENCE | 1 | 1 | 1 | 0 |
| GPS_DRIFT | 1 | 1 | 1 | 0 |
| HIGH_WIND | 1 | 1 | 1 | 0 |
| INTRUSION | 4 | 4 | 4 | 0 |
| LOITERING | 1 | 1 | 1 | 0 |
| LOW_ALTITUDE | 1 | 1 | 1 | 0 |
| REPEAT_VEHICLE | 1 | 1 | 1 | 0 |
| RESTRICTED_ZONE | 1 | 1 | 1 | 0 |
| UNATTENDED_OBJECT | 1 | 1 | 1 | 0 |

## Errors across all seeds

- seed 1: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 2: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 3: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 4: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 5: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 6: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 7: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 8: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 9: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -
- seed 10: alert FP 0, missed 0 | detection FP 1, FN 0 | re-ID splits -, merges -

## Detection error examples (seed 10)

- FP f340 'Two workers loading boxes onto a yellow forklift at the warehouse': object/package
