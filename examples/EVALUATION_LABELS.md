# Blind evaluation labels

New evaluation videos should not show the current algorithm label or speed in
their contact sheets. Keep at least 3–5 whole videos outside parameter tuning.
Split by video, never by adjacent frames.

Each `eval/strict/<video-stem>/labels.jsonl` line may use the existing fields:

```json
{"video":"sample.mp4","t":1.2,"vis_kind":"room","vis_confidence":0.9}
```

New labels may additionally describe the editing intent directly:

```json
{
  "video": "sample.mp4",
  "t": 1.2,
  "vis_kind": "room",
  "confidence": 0.9,
  "acceptable_speed_min": 1.0,
  "acceptable_speed_max": 1.7,
  "content_value": "showcase",
  "camera_motion": "pan"
}
```

Allowed values:

- `vis_kind`: `room`, `move`, `fast`
- `content_value`: `showcase`, `transition`, `low_info`
- `camera_motion`: `static`, `pan`, `walk`, `whip`

`content_value` and `camera_motion` are descriptive metadata. The evaluator
uses `vis_kind`, confidence, and the optional acceptable-speed range. Rounded
timestamps a few milliseconds beyond the media tail are clamped automatically.

Run the full current dataset:

```bash
python evaluate_segments.py --dataset eval/strict --work-root frames
```

Run the bounded legacy grid and leave-one-video-out validation:

```bash
python scripts/tune_segments.py
```

After reanalysis has produced cache version 3 features, evaluate the opt-in
score/hysteresis candidate:

```bash
python scripts/tune_segments.py --mode candidate \
  --param algorithm.candidate.low_information_max=.22,.28,.34 \
  --param algorithm.candidate.move_motion_min=.28,.34,.40
```
