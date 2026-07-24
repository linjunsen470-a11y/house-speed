"""Load and validate config.yaml with defaults."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

# Built-in defaults (used when a key is missing from YAML)
DEFAULTS: dict[str, Any] = {
    "io": {
        "output_suffix": "_edited",
        "work_dir": "frames",
        "save_motion_csv": True,
        "save_segments_json": True,
        "save_filter_script": True,
    },
    "analysis": {
        "resize_width": 135,
        "resize_height": 240,
        "canny_low": 50,
        "canny_high": 150,
        "smooth_window": 7,
    },
    "classify": {
        "wall_edge_max": 0.035,
        "wall_std_max": 45.0,
        "very_fast_motion": 14.0,
        "transitional_motion": 10.5,
        "transitional_edge_max": 0.055,
    },
    "speeds": {
        "room": 1.0,
        "move": 2.2,
        "fast": 3.5,
    },
    "segments": {
        "min_duration": 0.40,
    },
    "pacing": {
        "enabled": True,
        "room_hold_ramp": [
            {"after": 0.0, "speed": 1.0},
            {"after": 4.0, "speed": 1.5},
            {"after": 7.0, "speed": 2.0},
            {"after": 10.0, "speed": 2.8},
        ],
    },
    "overrides": [],
    "encode": {
        "video_codec": "libx264",
        "preset": "medium",
        "crf": 20,
        "pixel_format": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "128k",
        "movflags": "+faststart",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load YAML config and merge onto defaults."""
    cfg = deepcopy(DEFAULTS)
    if path is None:
        return cfg
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {p}")
    cfg = _deep_merge(cfg, data)
    _validate(cfg)
    return cfg


def _validate(cfg: dict[str, Any]) -> None:
    speeds = cfg["speeds"]
    for kind in ("room", "move", "fast"):
        if kind not in speeds:
            raise ValueError(f"speeds.{kind} is required")
        if float(speeds[kind]) <= 0:
            raise ValueError(f"speeds.{kind} must be > 0")

    sw = int(cfg["analysis"]["smooth_window"])
    if sw < 1:
        raise ValueError("analysis.smooth_window must be >= 1")
    cfg["analysis"]["smooth_window"] = sw if sw % 2 == 1 else sw + 1

    md = float(cfg["segments"]["min_duration"])
    if md < 0:
        raise ValueError("segments.min_duration must be >= 0")

    pacing = cfg.get("pacing") or {}
    ramp = list(pacing.get("room_hold_ramp") or [])
    for i, step in enumerate(ramp):
        if "after" not in step or "speed" not in step:
            raise ValueError(
                f"pacing.room_hold_ramp[{i}] needs after and speed"
            )
        if float(step["speed"]) <= 0:
            raise ValueError(f"pacing.room_hold_ramp[{i}].speed must be > 0")
        if float(step["after"]) < 0:
            raise ValueError(f"pacing.room_hold_ramp[{i}].after must be >= 0")
    # sort ascending by after for stable lookup
    if ramp:
        cfg.setdefault("pacing", {})["room_hold_ramp"] = sorted(
            ramp, key=lambda s: float(s["after"])
        )

    for i, ov in enumerate(cfg.get("overrides") or []):
        if "start" not in ov or "end" not in ov or "kind" not in ov:
            raise ValueError(
                f"overrides[{i}] needs start, end, kind (room|move|fast)"
            )
        if ov["kind"] not in speeds:
            raise ValueError(
                f"overrides[{i}].kind must be one of {list(speeds)}"
            )
        if float(ov["end"]) <= float(ov["start"]):
            raise ValueError(f"overrides[{i}]: end must be > start")
