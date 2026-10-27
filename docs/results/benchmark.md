# Scalability benchmark

Produced by `python -m drone_security benchmark` (single process, SQLite file on local disk). Raw data: `benchmark.json`; plots: `benchmark_*.png`. Frames are the scripted day repeated on consecutive dates with fresh paraphrases and jitter.

## Ingest and storage

| frames | days | ingest s | frames/s | detections | entities | raw rule firings | alerts | DB MB | unique captions |
|---|---|---|---|---|---|---|---|---|---|
| 10,000 | 16 | 1.58 | 6,310 | 1,695 | 148 | 1,061 | 296 | 4.9 | 129 |
| 50,000 | 78 | 9.26 | 5,400 | 8,431 | 676 | 5,256 | 1,474 | 24.1 | 129 |
| 100,000 | 155 | 19.22 | 5,202 | 16,885 | 1,334 | 10,457 | 2,941 | 48.2 | 129 |
| 200,000 | 310 | 40.2 | 4,976 | 33,755 | 2,646 | 20,915 | 5,883 | 96.5 | 129 |

## Query latency, median / p95 in ms (30 repeats)

| query | 10,000 | 50,000 | 100,000 | 200,000 |
|---|---|---|---|---|
| object: all truck detections | 0.58 / 0.625 | 0.571 / 0.746 | 0.57 / 0.748 | 0.579 / 0.634 |
| time range: one hour of frames | 0.038 / 0.06 | 0.035 / 0.045 | 0.036 / 0.039 | 0.037 / 0.045 |
| object + zone + time | 0.079 / 0.107 | 0.079 / 0.099 | 0.08 / 0.106 | 0.081 / 0.101 |
| entity history (F150) | 0.503 / 0.562 | 1.381 / 1.48 | 2.348 / 2.533 | 7.295 / 8.036 |
| alerts HIGH+ (first 100) | 0.516 / 0.561 | 0.514 / 0.582 | 0.515 / 0.544 | 0.515 / 0.607 |
| full text (BM25) | 0.192 / 0.221 | 0.573 / 0.664 | 3.46 / 3.844 | 6.799 / 7.135 |
| hybrid text (BM25 + TF-IDF) | 0.439 / 0.459 | 0.88 / 1.162 | 3.835 / 4.014 | 7.2 / 7.677 |
| summaries for one day | 0.086 / 0.116 | 0.078 / 0.092 | 0.105 / 0.118 | 0.094 / 0.119 |

## Agent context size (tokens, estimated at 4 characters per token)

| frames | hierarchical working memory | every frame in the prompt (naive) | ratio |
|---|---|---|---|
| 10,000 | 2,376 | 339,316 | 143x |
| 50,000 | 2,226 | 1,707,106 | 767x |
| 100,000 | 2,678 | 3,416,763 | 1,276x |
| 200,000 | 2,442 | 6,862,855 | 2,810x |
