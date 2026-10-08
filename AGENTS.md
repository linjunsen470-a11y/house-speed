# AGENTS.md

## Project intent

This is a speed-only real-estate walkthrough video editor, primarily maintained
through IDE AI agents. Keep it easy to inspect, run, tune, and repair locally.
Prefer a small explicit change over a new framework or abstraction layer.

Core product constraints:

- Never discard source intervals. Editing is implemented with trim, speed
  changes, and concat; the complete source timeline must remain covered.
- Default pacing targets natural camera movement for later TTS voiceover:
  room 1.0x, move 1.18x, fast 1.30x, and modest hold boosts up to 1.25x.
  Keep short pauses; do not restore aggressive speed-up defaults.
  Final segment speeds must respect max_speed (default 1.30), even when
  older sidecars contain higher speed values. Preserve this guard when changing pacing.
- Protect informative rooms, gardens, and deliberate pans from excessive
  acceleration. A false `room -> fast` decision is more costly than a slightly
  longer output.
- Keep processing local with Python, OpenCV, NumPy, PyYAML, ffmpeg, and
  ffprobe. Do not add cloud services or model APIs without an explicit request.
- `algorithm.mode: legacy` is the production default. `candidate` is for
  offline A/B evaluation until independent videos pass the documented gates.
- This is not a general video platform. Avoid databases, job queues, plugin
  systems, and service layers unless a concrete requirement demands them.

## Repository map

- `edit_speed.py`: user-facing CLI.
- `walkthrough_edit/analyze.py`: per-frame CV features and cache row schema.
- `walkthrough_edit/classify.py`: frame classification, segment
  post-processing, overrides, and pacing.
- `walkthrough_edit/render.py`: ffprobe helpers and atomic ffmpeg
  rendering.
- `walkthrough_edit/pipeline.py`: configuration layers, analysis caches, reports, and
  end-to-end orchestration.
- `config.yaml`: checked-in defaults intended for normal personal use.
- `evaluate_segments.py`: single-plan and dataset evaluation.
- `scripts/tune_segments.py`: bounded grid search with leave-one-video-out
  validation.
- `tests/`: fast unit tests plus small ffmpeg integration tests.
- `examples/`: safe checked-in configuration and labeling examples.
- `AUDIT.md`: audit findings, validation evidence, and known limitations.
- `legacy/`: local folder for archiving completed project video and image files (untracked).

## Setup and routine commands

Use Python 3.10+ and ensure `ffmpeg` and `ffprobe` are on `PATH`.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
python edit_speed.py path/to/video.mp4 --dry-run
python edit_speed.py path/to/video.mp4 --dry-run --review
```

Evaluation and tuning:

```bash
python evaluate_segments.py --dataset eval/strict --work-root frames
python scripts/tune_segments.py
python scripts/tune_segments.py --mode candidate
```

Use a short existing source for an end-to-end smoke test. Do not encode every
local video unless the task specifically requires it.

## Agent workflow

1. Read `git status --short` before editing. Existing modifications belong to
   the user; preserve them and keep unrelated changes out of the patch.
2. Inspect the relevant call path and nearby tests before changing behavior.
3. Make the narrowest implementation that satisfies the request.
4. Add or update focused tests. Run `python -m pytest`.
5. For algorithm changes, also run the dataset evaluator and report per-video
   regressions, macro F1, move F1, and `room -> fast` rate.
6. Run `git diff --check` before handoff.
7. Do not commit, stage, push, delete local media, or erase evaluation outputs
   unless the user explicitly asks.

## Change rules

Configuration:

- Add new settings to `DEFAULTS`, validation, `config.yaml`, and tests together.
- Preserve deep-merge behavior for per-video `<stem>.edit.yaml` sidecars.
- Unknown YAML keys should remain visible as warnings; do not silently accept
  likely typos.
- Explicit CLI config paths must exist; do not silently fall back on a typo.
- pacing.enabled=false disables both room hold and static boosts. Hold ramp
  entries are relative to their first speed and scale with speeds.room.
- Use sidecar overrides for one video instead of hard-coding filenames or
  timestamps in core modules.

Analysis and caches:

- If an analysis row field is added or its meaning changes, update NPZ
  serialization/validation and bump `_ANALYSIS_CACHE_VERSION`.
- Cache writes and final media writes must remain temporary-file plus atomic
  replace operations.
- Treat corrupt or incomplete analysis caches as misses and reanalyze.
- Keep arrays compact (`float32` is preferred for cached features) unless
  precision evidence requires otherwise.

Timeline and rendering:

- Every returned segment list must start at `0`, end at probed media duration,
  contain no gaps/overlaps, and use finite positive speeds.
- Account for videos without audio, Windows file locking, Unicode paths, and
  filenames beginning with `-`.
- Never replace the original input path.
- A review path must differ from both input and final output paths.
- Validate an ffmpeg output before atomically replacing an existing result.
  Confirm its video stream, dimensions, audio presence, and expected duration.

Algorithm work:

- Do not tune on aggregate accuracy alone. The current labels are imbalanced
  and `move` is the weak class.
- Split evaluation by whole video, never by neighboring frames.
- Prefer held-out macro F1 and low `room -> fast` rate over a small in-sample
  gain.
- Existing contact-sheet labels are not an independent test set. Do not claim
  generalization until 3–5 blind-labeled new videos are available.
- Do not switch the production default to `candidate` unless independent
  results meet the acceptance criteria in `examples/EVALUATION_LABELS.md`.

## Generated and local-only data

The following are local/regenerable and must not be committed:

- source and rendered videos;
- `frames/`, `eval/`, and `legacy/`;
- ffmpeg logs, `.part` files, Python caches, and IDE state;
- `config.local.yaml`, `<stem>.edit.yaml`, private labels, and `scripts/_*.py`
  one-off scratch scripts.

Markdown documentation and files under `examples/` may be tracked.
Do not add titles, stickers, music, voiceover, subtitles, or packaging features.

## Definition of done

A change is ready when:

- behavior and failure modes are covered by focused tests;
- `python -m pytest` passes;
- `git diff --check` passes;
- legacy behavior is unchanged unless the request explicitly changes it;
- generated media/caches are not accidentally tracked;
- the handoff states what changed, what was verified, and any algorithm gate
  that remains unresolved.
