# Held-out wording: every caption reworded by Gemini

646 of 646 frames reworded by `gemini-3.5-flash` (data/eval/paraphrases.json); ground truth unchanged. Raw data: `paraphrase_eval.json`.

| analyzer, captions | alert P | alert R | alert F1 | detection P | detection R | colour acc. | re-ID F1 | F150 visits |
|---|---|---|---|---|---|---|---|---|
| rules | original captions | 1.0 | 1.0 | 1.0 | 0.991 | 1.0 | 1.0 | 1.0 | [2] |
| rules | reworded captions | 0.789 | 0.75 | 0.769 | 0.837 | 0.759 | 0.986 | 0.999 | [2] |
| gemini | original captions | 1.0 | 1.0 | 1.0 | 1.0 | 0.981 | 0.935 | 1.0 | [2] |
| gemini | reworded captions | 1.0 | 1.0 | 1.0 | 0.938 | 0.981 | 0.935 | 0.983 | [2] |

## Missed incidents on reworded captions

- rules | reworded captions: GT-03 02:14 INTRUSION: Masked person climbing the back fence; GT-06 02:16 INTRUSION: Forcing the warehouse side door; GT-07 02:36 INTRUSION: Intruder climbing back out over the fence; GT-08 02:18 ACCESS_OPEN: Warehouse side door left open after the break-in; GT-20 23:16 INTRUSION: Trying the garage door handles
- gemini | reworded captions: none

## Example rewrites

- "Person in a dark hoodie loitering at the main gate" -> "Individual wearing a dark hooded sweatshirt lingering near the main gate."
- "Person in dark hoodie still standing at the main gate" -> "Subject clad in a dark hoodie continues standing at the main gate location."
- "Person in dark hoodie walking back and forth at the main gate" -> "Individual in a dark hooded sweater pacing back and forth beside the main gate."
- "Person in dark hoodie leaving the main gate area" -> "Person dressed in a dark hoodie departing the vicinity of the main gate."
- "Main gate shut, no one around" -> "Main gate remains closed, with no personnel present nearby."
- "Driveway clear" -> "Driveway sector is completely clear."
- "Garage area quiet" -> "No activity observed in the garage zone."
- "Loading bay empty" -> "The loading bay area is currently unoccupied."
- "Back fence intact, no one nearby" -> "Rear perimeter fence is undamaged, no individuals in the area."
- "Backyard quiet" -> "Backyard area shows zero activity."
- "Nobody at the front door" -> "No individuals detected at the front door."
- "Empty driveway, no vehicles" -> "Vacant driveway space, zero vehicles present."
