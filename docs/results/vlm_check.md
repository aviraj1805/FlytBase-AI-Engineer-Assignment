# VLM check on real images

Produced by `python -m drone_security vlm`. Model: `gemini-3.5-flash`. Raw data: `vlm_check.json`.
Images and licences: data/images/ATTRIBUTION.md; hand labels: data/images/manifest.json.

| | object recall | attribute accuracy | images with false alarms |
|---|---|---|---|
| Gemini VLM (structured output) | 1.0 | 1.0 | 0 / 7 |
| Rule parser on the VLM caption | 0.857 | 1.0 | 0 / 7 |

Expected alert behaviour matched on 100% of images. Mean VLM latency 14.64 s per image.

| image | zone, time | VLM caption | VLM objects | alerts (expected) |
|---|---|---|---|---|
| 05_fox_lawn.jpg | backyard 01:30 | A brown fox is standing on the grass in the backyard. | Brown fox standing | - (none) |
| 03_fence_climbers.jpg | back_fence 02:14 | Two men in black jackets are climbing over the back fence, one of them carrying a briefcase. | Person in black jacket climbing; Person in black jacket climbing | INTRUSION (INTRUSION) |
| 01_blue_f150_traffic.jpg | main_gate 09:14 | A blue Ford F150 truck, a navy Hyundai Sonata car, a red Dodge Ram truck, a blue garbage truck, a black SUV, and a grey car move along the roadway near the main gate. | Blue Ford F150; Blue Hyundai Sonata; Red Dodge Ram; Blue truck; Black SUV; Gray car | - (none) |
| 02_parcel_doorstep.jpg | front_door 10:07 | A damaged brown package sits on the step at the front door. | Brown package | - (none) |
| 06_forklift_loading_dock.jpg | warehouse 12:30 | A blue Komatsu forklift and a yellow Panus container truck are positioned at the warehouse as two workers wearing green tops load packages into the open container. | Person in green shirt loading; Person in green shirt loading; Blue Komatsu loading; Yellow Panus loading; Door | - (none) |
| 07_empty_lane.jpg | driveway 13:00 | The driveway is empty with no people, vehicles, animals, or open gates present. |  | - (none) |
| 04_black_suv_driveway.jpg | driveway 23:10 | A black Cadillac Escalade is parked in the driveway. | Black Cadillac Escalade parked | AFTER_HOURS_VEHICLE (AFTER_HOURS_VEHICLE) |
