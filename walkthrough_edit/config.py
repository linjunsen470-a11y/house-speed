"""Load and validate config.yaml with defaults."""
from __future__ import annotations

from copy import deepcopy
import math
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
    "cache": {
        "enabled": True,
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
        "scenic_edge_min": 0.16,
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
        "scenic_skip_hold_ramp": True,
        "scenic_skip_edge_min": 0.18,
        "room_hold_ramp": [
            {"after": 0.0, "speed": 1.0},
            {"after": 4.0, "speed": 1.5},
            {"after": 7.0, "speed": 2.0},
            {"after": 10.0, "speed": 2.8},
        ],
    },
    "overrides": [],
    "encode": {
        "match_source": True,
        "bitrate_scale": 1.0,
        "video_codec": "auto",
        "preset": "medium",
        "crf": 28,
        "pixel_format": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "64k",
        "movflags": "+faststart",
    },
    "review": {
        "width": 540,
        "fps": 12,
        "video_bitrate": "900k",
        "preset": "ultrafast",
        # Optional absolute path to a .ttf/.ttc for drawtext (auto-detect if empty)
        "fontfile": "",
    },
    # Stage-B packaging: short-video 花字 + 私信贴纸 + BGM only
    "pack": {
        "enabled": False,
        "assets_dir": "assets",
        "style": "douyin_fire",
        "fontfile": "",
        "text": {
            "lines": [],
            "line1": "",
            "line2": "",
            "line3": "",
            "line4": "",
        },
        "layout": {
            "y_rel": 0.30,
            "max_width_rel": 0.90,
            # optional: font_size_rel, line_gap_rel (see validate)
        },
        "sticker": {
            "enabled": True,
            "style": "dm_emoji_bubble",
            "start": 5.0,
            "width_rel": 0.30,
            "x": 0.16,
            "y": 0.90,
            "file": "",
        },
        "audio": {
            "bgm": "carefree",
            "volume": 0.80,
            "fade_in": 0.5,
            "fade_out": 0.8,
        },
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
        _validate(cfg)
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


def merge_config_file(cfg: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """Deep-merge one YAML layer onto an already loaded configuration."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {p}")
    merged = _deep_merge(cfg, data)
    _validate(merged)
    return merged


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and out < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return out


def _validate(cfg: dict[str, Any]) -> None:
    analysis = cfg["analysis"]
    for key in ("resize_width", "resize_height"):
        analysis[key] = int(_number(analysis[key], f"analysis.{key}", minimum=1))
    low = int(_number(analysis["canny_low"], "analysis.canny_low", minimum=0))
    high = int(_number(analysis["canny_high"], "analysis.canny_high", minimum=0))
    if low > 255 or high > 255 or low >= high:
        raise ValueError("analysis Canny thresholds must satisfy 0 <= low < high <= 255")
    analysis["canny_low"], analysis["canny_high"] = low, high

    speeds = cfg["speeds"]
    for kind in ("room", "move", "fast"):
        if kind not in speeds:
            raise ValueError(f"speeds.{kind} is required")
        value = _number(speeds[kind], f"speeds.{kind}", minimum=0)
        if value <= 0:
            raise ValueError(f"speeds.{kind} must be > 0")
        speeds[kind] = value

    sw = int(_number(analysis["smooth_window"], "analysis.smooth_window", minimum=1))
    analysis["smooth_window"] = sw if sw % 2 else sw + 1

    classify = cfg["classify"]
    for key in ("wall_edge_max", "transitional_edge_max", "scenic_edge_min"):
        value = _number(classify[key], f"classify.{key}", minimum=0)
        if value > 1:
            raise ValueError(f"classify.{key} must be <= 1")
        classify[key] = value
    wall_std = _number(classify["wall_std_max"], "classify.wall_std_max", minimum=0)
    if wall_std > 255:
        raise ValueError("classify.wall_std_max must be <= 255")
    classify["wall_std_max"] = wall_std
    for key in ("very_fast_motion", "transitional_motion"):
        classify[key] = _number(classify[key], f"classify.{key}", minimum=0)

    cfg["segments"]["min_duration"] = _number(
        cfg["segments"]["min_duration"], "segments.min_duration", minimum=0
    )

    pacing = cfg.get("pacing") or {}
    ramp = list(pacing.get("room_hold_ramp") or [])
    for i, step in enumerate(ramp):
        if "after" not in step or "speed" not in step:
            raise ValueError(
                f"pacing.room_hold_ramp[{i}] needs after and speed"
            )
        if _number(step["speed"], f"pacing.room_hold_ramp[{i}].speed") <= 0:
            raise ValueError(f"pacing.room_hold_ramp[{i}].speed must be > 0")
        _number(step["after"], f"pacing.room_hold_ramp[{i}].after", minimum=0)
    if ramp:
        cfg.setdefault("pacing", {})["room_hold_ramp"] = sorted(
            ramp, key=lambda s: float(s["after"])
        )
    scenic = _number(
        pacing.get("scenic_skip_edge_min", 0.18),
        "pacing.scenic_skip_edge_min",
        minimum=0,
    )
    if scenic > 1:
        raise ValueError("pacing.scenic_skip_edge_min must be <= 1")
    cfg.setdefault("pacing", {})["scenic_skip_edge_min"] = scenic

    for i, ov in enumerate(cfg.get("overrides") or []):
        if "start" not in ov or "end" not in ov or "kind" not in ov:
            raise ValueError(
                f"overrides[{i}] needs start, end, kind (room|move|fast)"
            )
        if ov["kind"] not in speeds:
            raise ValueError(
                f"overrides[{i}].kind must be one of {list(speeds)}"
            )
        start = _number(ov["start"], f"overrides[{i}].start", minimum=0)
        end = _number(ov["end"], f"overrides[{i}].end", minimum=0)
        if end <= start:
            raise ValueError(f"overrides[{i}]: end must be > start")
        ov["start"], ov["end"] = start, end

    enc = cfg["encode"]
    scale = _number(enc.get("bitrate_scale", 1.0), "encode.bitrate_scale")
    if scale <= 0:
        raise ValueError("encode.bitrate_scale must be > 0")
    enc["bitrate_scale"] = scale
    codec = str(enc.get("video_codec", "auto")).lower()
    allowed = {"auto", "libx264", "h264", "libx265", "hevc", "h265"}
    if codec not in allowed:
        raise ValueError(f"encode.video_codec must be one of {sorted(allowed)}")
    enc["video_codec"] = codec
    if not str(enc.get("preset", "")).strip():
        raise ValueError("encode.preset must not be empty")
    _number(enc.get("crf", 28), "encode.crf", minimum=0)

    review = cfg.get("review") or {}
    review["width"] = int(
        _number(review.get("width", 540), "review.width", minimum=2)
    )
    if review["width"] % 2:
        review["width"] += 1
    review["fps"] = int(_number(review.get("fps", 12), "review.fps", minimum=1))
    review["fps"] = min(review["fps"], 30)
    fontfile = str(review.get("fontfile") or "").strip()
    if fontfile:
        font_path = Path(fontfile)
        if not font_path.is_file():
            raise ValueError(f"review.fontfile not found: {font_path}")
        review["fontfile"] = str(font_path)
    else:
        review["fontfile"] = ""
    cfg["review"] = review

    pack = cfg.get("pack") or {}
    pack["enabled"] = bool(pack.get("enabled", False))
    pack["assets_dir"] = str(pack.get("assets_dir") or "assets")
    pack["style"] = str(pack.get("style") or "douyin_fire")
    pack["fontfile"] = str(pack.get("fontfile") or "").strip()
    text = pack.get("text") or {}
    if not isinstance(text, dict):
        raise ValueError("pack.text must be a mapping")
    lines_val = text.get("lines")
    if lines_val is None:
        lines_list: list[str] = []
    elif isinstance(lines_val, list):
        lines_list = [str(x) for x in lines_val]
    else:
        raise ValueError("pack.text.lines must be a list of strings")
    pack["text"] = {
        "lines": lines_list,
        "line1": str(text.get("line1") or text.get("title") or ""),
        "line2": str(text.get("line2") or text.get("subtitle") or ""),
        "line3": str(text.get("line3") or text.get("price") or ""),
        "line4": str(text.get("line4") or ""),
        "line5": str(text.get("line5") or ""),
    }
    layout = pack.get("layout") or {}
    if not isinstance(layout, dict):
        raise ValueError("pack.layout must be a mapping")
    layout_out: dict[str, Any] = {
        "y_rel": _number(layout.get("y_rel", 0.30), "pack.layout.y_rel", minimum=0),
        "max_width_rel": _number(
            layout.get("max_width_rel", 0.90), "pack.layout.max_width_rel", minimum=0.4
        ),
    }
    if layout.get("font_size_rel") is not None and str(layout.get("font_size_rel")).strip() != "":
        layout_out["font_size_rel"] = _number(
            layout.get("font_size_rel"), "pack.layout.font_size_rel", minimum=0.03
        )
        if layout_out["font_size_rel"] > 0.2:
            raise ValueError("pack.layout.font_size_rel must be <= 0.2")
    if layout.get("line_gap_rel") is not None and str(layout.get("line_gap_rel")).strip() != "":
        # allow negative to tighten (pull glyph pads together)
        layout_out["line_gap_rel"] = _number(
            layout.get("line_gap_rel"), "pack.layout.line_gap_rel", minimum=-0.05
        )
        if layout_out["line_gap_rel"] > 0.08:
            raise ValueError("pack.layout.line_gap_rel must be <= 0.08")
    pack["layout"] = layout_out
    if pack["layout"]["y_rel"] > 1:
        raise ValueError("pack.layout.y_rel must be <= 1")
    if pack["layout"]["max_width_rel"] > 1:
        raise ValueError("pack.layout.max_width_rel must be <= 1")
    st = pack.get("sticker") or {}
    if not isinstance(st, dict):
        raise ValueError("pack.sticker must be a mapping")
    pack["sticker"] = {
        "enabled": bool(st.get("enabled", True)),
        "style": str(st.get("style") or "dm_emoji_bubble"),
        "start": _number(st.get("start", 5.0), "pack.sticker.start", minimum=0),
        "width_rel": _number(st.get("width_rel", 0.30), "pack.sticker.width_rel", minimum=0.05),
        "x": _number(st.get("x", 0.16), "pack.sticker.x", minimum=0),
        "y": _number(st.get("y", 0.90), "pack.sticker.y", minimum=0),
        "file": str(st.get("file") or "").strip(),
    }
    if pack["sticker"]["width_rel"] > 0.6:
        raise ValueError("pack.sticker.width_rel must be <= 0.6")
    if pack["sticker"]["x"] > 1 or pack["sticker"]["y"] > 1:
        raise ValueError("pack.sticker.x/y must be in 0..1")
    audio = pack.get("audio") or {}
    if not isinstance(audio, dict):
        raise ValueError("pack.audio must be a mapping")
    pack["audio"] = {
        "bgm": str(audio.get("bgm") or "").strip(),
        "volume": _number(audio.get("volume", 0.85), "pack.audio.volume", minimum=0),
        "fade_in": _number(audio.get("fade_in", 0.5), "pack.audio.fade_in", minimum=0),
        "fade_out": _number(audio.get("fade_out", 0.8), "pack.audio.fade_out", minimum=0),
    }
    if pack["audio"]["volume"] > 2:
        raise ValueError("pack.audio.volume must be <= 2")
    cfg["pack"] = pack
