# Context-management experiment

20 fixed questions (data/eval/qa_questions.yaml) about the generated day (646 frames, seed 7). Model: `none (offline only)`. Raw data: `qa_context_eval.json`.

## Context size

| context | characters | tokens (estimate, 4 chars/token) | tokens (Gemini count) |
|---|---|---|---|
| naive | 87,051 | 21,763 | - |
| hierarchical | 6,488 | 1,622 | - |
| tools_only | 307 | 77 | - |
| summaries | 6,488 | 1,622 | - |

## Answers

| condition | accuracy | answerable | abstention | whole-day questions | mean prompt tokens | mean latency s | answers with made-up citations |
|---|---|---|---|---|---|---|---|
| offline | 1.0 | 1.0 | 1.0 | 1 | 0 | 0.0 | 0 |

## Per question

| id | question | offline |
|---|---|---|
| Q01 | How many times did the blue Ford F150 enter the property today? | ok |
| Q02 | When was someone loitering at the main gate? | ok |
| Q03 | Was anyone in the warehouse after hours, and when? | ok |
| Q04 | When was the main gate left open? | ok |
| Q05 | Which vehicle arrived after hours, and at what time? | ok |
| Q06 | Was a bag or package left unattended? Where? | ok |
| Q07 | What was the drone's lowest battery level while flying? | ok |
| Q08 | Did the drone leave the geofence? When? | ok |
| Q09 | When did the drone's GPS become unreliable? | ok |
| Q10 | Is the blue Chevrolet Silverado the same vehicle as the blue Ford F150? | ok |
| Q11 | What animals were seen today? | ok |
| Q12 | What happened between 23:00 and 23:30? | ok |
| Q13 | Who picked up the package delivered at 10:05? | ok |
| Q14 | Did a white Toyota Camry visit, and is it a known vehicle? | ok |
| Q15 | What happened at the back fence at 02:14? | ok |
| Q16 | How strong was the wind at 15:10? | ok |
| Q17 | How many people in total were seen climbing the back fence during the night? | ok |
| Q18 | What was the licence plate of the black SUV? | ok |
| Q19 | Did anyone enter the main office building? | ok |
| Q20 | What colour was the courier's van? | ok |
