"""Short-video 花字 style presets (multi-layer stroke + glow, not plain UI bars)."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# Visual model (Jianying / Douyin 花字):
#   outer stroke → inner stroke → fill (+ optional glow)
# Layout is center-upper on the frame; no plain CSV-looking captions.


def _style(
    *,
    label: str,
    fill: tuple[int, int, int, int],
    stroke_outer: tuple[int, int, int, int],
    stroke_inner: tuple[int, int, int, int],
    glow: tuple[int, int, int, int] | None,
    # FreeType stroke_width is visual half-width — keep moderate for clean rings
    stroke_outer_rel: float = 0.14,
    stroke_inner_rel: float = 0.07,
    glow_rel: float = 0.05,
    font_size_rel: float = 0.082,  # slightly smaller main 花字
    y_center_rel: float = 0.30,
    # Compact vertical rhythm (0 / negative pulls padded glyphs closer)
    line_gap_rel: float = -0.006,
    letter_spacing_rel: float = 0.0,
    fill_bottom: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "kind": "douyin",
        "y_center_rel": y_center_rel,
        "max_width_rel": 0.92,
        "line_gap_rel": line_gap_rel,
        "letter_spacing_rel": letter_spacing_rel,
        "font_size_rel": font_size_rel,
        "uniform_lines": True,
        "fill": fill,
        "fill_bottom": fill_bottom,
        "stroke_outer": stroke_outer,
        "stroke_inner": stroke_inner,
        "stroke_outer_rel": stroke_outer_rel,
        "stroke_inner_rel": stroke_inner_rel,
        "glow": glow,
        "glow_rel": glow_rel,
        "sparkle": True,
    }


STYLES: dict[str, dict[str, Any]] = {
    "douyin_pink": _style(
        label="粉字白描边发光（参考甜宠盘）",
        fill=(255, 90, 170, 255),
        fill_bottom=(255, 165, 210, 255),
        stroke_outer=(255, 255, 255, 255),
        stroke_inner=(255, 210, 230, 255),
        glow=(255, 100, 170, 150),
        stroke_outer_rel=0.15,
        stroke_inner_rel=0.07,
    ),
    "douyin_fire": _style(
        label="红字白边蓝外描（参考爆款盘）",
        fill=(255, 40, 40, 255),
        fill_bottom=(255, 120, 30, 255),
        stroke_outer=(25, 105, 255, 255),
        stroke_inner=(255, 255, 255, 255),
        glow=(25, 90, 255, 130),
        stroke_outer_rel=0.16,
        stroke_inner_rel=0.08,
    ),
    "douyin_lemon": _style(
        label="黄字红描边（醒目降价）",
        fill=(255, 235, 45, 255),
        fill_bottom=(255, 200, 25, 255),
        stroke_outer=(230, 15, 50, 255),
        stroke_inner=(255, 255, 255, 255),
        glow=(255, 50, 70, 140),
        stroke_outer_rel=0.15,
        stroke_inner_rel=0.07,
    ),
    "douyin_mint": _style(
        label="薄荷绿白描边（清新盘）",
        fill=(40, 225, 175, 255),
        fill_bottom=(160, 255, 225, 255),
        stroke_outer=(255, 255, 255, 255),
        stroke_inner=(20, 160, 130, 255),
        glow=(30, 210, 170, 130),
    ),
    "douyin_gold": _style(
        label="金粉双描边（品质改善）",
        fill=(255, 210, 90, 255),
        fill_bottom=(255, 240, 170, 255),
        stroke_outer=(100, 50, 8, 255),
        stroke_inner=(255, 255, 255, 255),
        glow=(255, 170, 50, 130),
        stroke_outer_rel=0.14,
        stroke_inner_rel=0.07,
    ),
    "douyin_violet": _style(
        label="紫粉霓虹（年轻盘）",
        fill=(190, 100, 255, 255),
        fill_bottom=(255, 150, 230, 255),
        stroke_outer=(255, 255, 255, 255),
        stroke_inner=(110, 50, 190, 255),
        glow=(170, 70, 255, 140),
    ),
    "douyin_sky": _style(
        label="天蓝白描边（江景/采光）",
        fill=(50, 165, 255, 255),
        fill_bottom=(170, 225, 255, 255),
        stroke_outer=(255, 255, 255, 255),
        stroke_inner=(20, 90, 190, 255),
        glow=(50, 150, 255, 130),
    ),
    "douyin_candy": _style(
        label="糖果橙粉（日租/商铺也可）",
        fill=(255, 95, 145, 255),
        fill_bottom=(255, 170, 90, 255),
        stroke_outer=(255, 255, 255, 255),
        stroke_inner=(255, 70, 110, 255),
        glow=(255, 90, 130, 130),
        stroke_outer_rel=0.15,
    ),
}

# Friendly aliases
STYLES["pink"] = STYLES["douyin_pink"]
STYLES["fire"] = STYLES["douyin_fire"]
STYLES["lemon"] = STYLES["douyin_lemon"]
STYLES["mint"] = STYLES["douyin_mint"]
STYLES["gold"] = STYLES["douyin_gold"]

DEFAULT_STYLE = "douyin_fire"


def list_styles() -> list[str]:
    # Hide aliases from the public list
    return [k for k in STYLES if k.startswith("douyin_")]


def get_style(name: str) -> dict[str, Any]:
    key = (name or DEFAULT_STYLE).strip()
    # Map retired plain styles to a punchy default so old configs still look good
    legacy = {
        "bar_dark": "douyin_fire",
        "clean_white": "douyin_sky",
        "gold_estate": "douyin_gold",
        "pop_stroke": "douyin_pink",
        "minimal_top": "douyin_mint",
        "bottom_card": "douyin_gold",
        "cyan_tech": "douyin_sky",
        "soft_pink": "douyin_pink",
    }
    key = legacy.get(key, key)
    if key not in STYLES:
        known = ", ".join(list_styles())
        raise ValueError(f"Unknown text style {name!r}. Choose one of: {known}")
    return deepcopy(STYLES[key])
