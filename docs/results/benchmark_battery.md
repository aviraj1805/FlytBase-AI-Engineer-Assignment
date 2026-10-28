# Scalability benchmark: re-run from a fresh clone, laptop on battery

Produced by `python -m drone_security benchmark` (single process, SQLite file on local disk). Raw data: `benchmark_battery.json`. Same code as `benchmark.md` plus per-frame latency; run from a fresh clone of commit 9aadf0d with the laptop on battery (CPU held at 2.1 GHz), so throughput is about 2.3x lower than the mains-power run. Frames are the scripted day repeated on consecutive dates with fresh paraphrases and jitter.

## Ingest and storage

| frames | days | ingest s | frames/s | per-frame p50 / p99 ms | detections | entities | raw rule firings | alerts | DB MB | unique captions |
|---|---|---|---|---|---|---|---|---|---|---|
| 10,000 | 16 | 3.88 | 2,579 | 0.185 / 1.459 | 1,695 | 148 | 1,061 | 296 | 4.9 | 129 |
| 50,000 | 78 | 22.15 | 2,257 | 0.189 / 1.848 | 8,431 | 676 | 5,256 | 1,474 | 24.1 | 129 |
| 100,000 | 155 | 47.15 | 2,121 | 0.194 / 1.969 | 16,885 | 1,334 | 10,457 | 2,941 | 48.2 | 129 |
| 200,000 | 310 | 93.48 | 2,139 | 0.189 / 1.986 | 33,755 | 2,646 | 20,915 | 5,883 | 96.5 | 129 |

## Query latency, median / p95 in ms (30 repeats)

| query | 10,000 | 50,000 | 100,000 | 200,000 |
|---|---|---|---|---|
| object: all truck detections | 1.297 / 1.639 | 1.407 / 1.784 | 1.338 / 1.711 | 1.289 / 1.453 |
| time range: one hour of frames | 0.083 / 0.211 | 0.081 / 0.107 | 0.085 / 0.138 | 0.084 / 0.104 |
| object + zone + time | 0.183 / 0.247 | 0.179 / 0.259 | 0.188 / 0.283 | 0.184 / 0.212 |
| entity history (F150) | 1.329 / 1.664 | 3.191 / 3.584 | 5.269 / 6.027 | 15.665 / 16.57 |
| alerts HIGH+ (first 100) | 1.379 / 1.631 | 1.184 / 1.89 | 1.169 / 1.579 | 1.168 / 1.419 |
| full text (BM25) | 0.473 / 0.661 | 1.35 / 1.656 | 8.022 / 9.908 | 15.52 / 16.425 |
| hybrid text (BM25 + TF-IDF) | 1.141 / 1.397 | 2.044 / 2.418 | 8.68 / 9.389 | 15.877 / 17.117 |
| summaries for one day | 0.186 / 0.279 | 0.179 / 0.227 | 0.239 / 0.263 | 0.207 / 0.229 |

## Agent context size (tokens, estimated at 4 characters per token)

| frames | hierarchical working memory | every frame in the prompt (naive) | ratio |
|---|---|---|---|
| 10,000 | 2,376 | 339,316 | 143x |
| 50,000 | 2,226 | 1,707,106 | 767x |
| 100,000 | 2,678 | 3,416,763 | 1,276x |
| 200,000 | 2,442 | 6,862,855 | 2,810x |
