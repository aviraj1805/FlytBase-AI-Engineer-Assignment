# Ablations (offline)

Produced by `python -m drone_security ablation`; mean over seeds [1, 2, 3, 4, 5]. Raw data: `ablation.json`.

## Re-identification when perception loses detail

Each identity attribute (colour, make, model, clothing) of every detection is dropped with probability p.

| p | tracker | re-ID precision | re-ID recall | re-ID F1 | entities (true) | F150 repeat alert found | false repeat alerts | alert precision | alert recall |
|---|---|---|---|---|---|---|---|---|---|
| 0.0 | similarity (this version) | 1.0 | 1.0 | 1.0 | 18 (18) | 1 | 0 | 1.0 | 1.0 |
| 0.0 | exact signature (v1) | 1.0 | 0.997 | 0.999 | 18.4 (18) | 1 | 0 | 1.0 | 1.0 |
| 0.1 | similarity (this version) | 0.999 | 0.967 | 0.982 | 18.4 (18) | 1 | 0 | 0.98 | 1.0 |
| 0.1 | exact signature (v1) | 1.0 | 0.614 | 0.757 | 27.8 (18) | 0.6 | 0.8 | 0.953 | 0.98 |
| 0.25 | similarity (this version) | 0.996 | 0.857 | 0.917 | 21.8 (18) | 0.6 | 0.4 | 0.924 | 0.98 |
| 0.25 | exact signature (v1) | 1.0 | 0.276 | 0.43 | 35.4 (18) | 0.4 | 1.8 | 0.891 | 0.97 |
| 0.5 | similarity (this version) | 0.988 | 0.51 | 0.67 | 31.6 (18) | 0 | 1 | 0.806 | 0.95 |
| 0.5 | exact signature (v1) | 0.937 | 0.126 | 0.222 | 40 (18) | 0.4 | 1.6 | 0.842 | 0.97 |

## Noise control (cooldown and correlation)

| setting | alerts per day | alerts per incident | precision | recall | redundant alerts |
|---|---|---|---|---|---|
| cooldown + correlation | 19 | 0.95 | 1.0 | 1.0 | 1 |
| cooldown only | 21 | 1.05 | 1.0 | 1.0 | 1 |
| neither | 67.4 | 3.37 | 1.0 | 1.0 | 47.4 |
