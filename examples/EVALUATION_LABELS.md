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
  "acceptable_speed_max": 1.3,
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

For the natural pacing profile, judge speed ranges independently of class names:
`fast` now normally means 1.30x rather than the former 3.5x. Relabel subjective
speed acceptance for the new intended use before treating old speed bounds as
acceptance criteria. Classification F1 alone does not measure natural camera feel
or how well a future TTS narration fits.

Media and evaluation caches were deliberately left on the desktop during
migration. This repository does not include the local dataset; pass its real
paths or generate new analysis caches. The tuner refuses missing or corrupt
feature caches. See `AUDIT.md` for the current local regression snapshot and its
limitations.

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
