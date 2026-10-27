# Context-management experiment

12 fixed questions (data/eval/qa_heldout.yaml) about the generated day (646 frames, seed 7). Model: `none (offline only)`. Raw data: `qa_heldout_eval.json`.

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
| offline | 0.583 | 0.545 | 1.0 | 0.5 | 0 | 0.0 | 0 |

## Per question

| id | question | offline |
|---|---|---|
| H01 | At what time did the blue Ford F150 first arrive? | ok |
| H02 | What was the person loitering at the main gate around midnight wearing? | ok |
| H03 | What did the masked person do at the warehouse? | ok |
| H04 | When did the drone dock to recharge after the night incident? | wrong |
| H05 | Was any vehicle parked at the warehouse in the late morning? | wrong |
| H06 | Until when did the black bag stay at the main gate? | ok |
| H07 | Did the gardener trigger any alerts? | wrong |
| H08 | What was the drone's altitude during the backyard sprinkler inspection? | ok |
| H09 | Which zones are on the property? | wrong |
| H10 | What was the name of the courier? | ok |
| H11 | How many critical alerts were raised today? | ok |
| H12 | What happened in the hour after the main gate was left open? | wrong |
