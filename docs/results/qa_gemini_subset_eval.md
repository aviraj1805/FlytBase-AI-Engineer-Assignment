# Context-management experiment

6 fixed questions (data/eval/qa_gemini_subset.yaml) about the generated day (646 frames, seed 7). Model: `gemini-3.5-flash-lite`. Raw data: `qa_gemini_subset_eval.json`.

## Context size

| context | characters | tokens (estimate, 4 chars/token) | tokens (Gemini count) |
|---|---|---|---|
| naive | 87,051 | 21,763 | 46082 |
| hierarchical | 6,488 | 1,622 | 2858 |
| tools_only | 307 | 77 | 96 |
| summaries | 6,488 | 1,622 | - |

## Answers

| condition | accuracy | answerable | abstention | whole-day questions | mean prompt tokens | mean latency s | answers with made-up citations | API errors (counted wrong) |
|---|---|---|---|---|---|---|---|---|
| offline | 0.5 | 0.4 | 1.0 | 1 | 0 | 0.0 | 0 | 0 |
| naive | 0.833 | 0.8 | 1.0 | 1 | 46,190 | 12.48 | 0 | 0 |
| hierarchical | 0.833 | 0.8 | 1.0 | 1 | 5,329 | 7.1 | 0 | 0 |

## Per question

| id | question | offline | naive | hierarchical |
|---|---|---|---|---|
| Q01 | How many times did the blue Ford F150 enter the property today? | ok | ok | ok |
| Q11 | What animals were seen today? | ok | ok | ok |
| H05 | Was any vehicle parked at the warehouse in the late morning? | wrong | ok | ok |
| H07 | Did the gardener trigger any alerts? | wrong | wrong | wrong |
| H12 | What happened in the hour after the main gate was left open? | wrong | ok | ok |
| Q18 | What was the licence plate of the black SUV? | ok | ok | ok |
