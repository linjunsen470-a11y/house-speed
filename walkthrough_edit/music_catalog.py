"""
Built-in short-video BGM presets (soft pure instrumentals).

Sources: Kevin MacLeod (incompetech.com) — Creative Commons BY 4.0.
These are free alternatives in the same *mood* as popular Douyin lifestyle/
real-estate BGMs. They are **not** the copyrighted chart hits themselves.
Attribution is required when you publish (see assets/music/README.md).
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

# id → file under assets/music/ + human label + vibe for picking
BGM_PRESETS: dict[str, dict[str, Any]] = {
    "carefree": {
        "file": "bgm_carefree.mp3",
        "label": "轻松明亮 · Carefree",
        "vibe": "轻快不吵，看房/日常 vlog 默认推荐",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
    },
    "easy_lemon": {
        "file": "bgm_easy_lemon.mp3",
        "label": "柔和俏皮 · Easy Lemon",
        "vibe": "经典轻松纯音乐，短视频常用气质",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
    },
    "life_of_riley": {
        "file": "bgm_life_of_riley.mp3",
        "label": "温暖愉快 · Life of Riley",
        "vibe": "温馨有亲和力，改善盘/家庭向",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
    },
    "summer_day": {
        "file": "bgm_summer_day.mp3",
        "label": "夏日通透 · Summer Day",
        "vibe": "明亮开阔，采光/江景/新盘",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
    },
    "dreamlike": {
        "file": "bgm_dreamlike.mp3",
        "label": "柔和梦幻 · Dreamlike",
        "vibe": "不突兀、有氛围，易听完",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
    },
    "bittersweet": {
        "file": "bgm_bittersweet.mp3",
        "label": "轻情绪 · Bittersweet",
        "vibe": "略带情绪，适合「忍痛挂牌」叙事",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
    },
}

DEFAULT_BGM = "carefree"


def list_bgm_ids() -> list[str]:
    return list(BGM_PRESETS.keys())


def resolve_bgm_path(
    bgm: str,
    assets: Path,
    *,
    rng: random.Random | None = None,
) -> Path:
    """
    Resolve pack.audio.bgm to a local file.

    Accepts:
      - preset id: carefree / easy_lemon / ...
      - random / random_soft: pick a built-in preset
      - filename under assets/music/
      - absolute or relative path
    """
    raw = str(bgm or "").strip()
    if not raw:
        raise ValueError("pack.audio.bgm is empty")

    key = raw.lower().replace("\\", "/").split("/")[-1]
    key_stem = Path(key).stem
    # strip optional bgm_ prefix / .mp3
    for prefix in ("bgm_",):
        if key_stem.startswith(prefix):
            key_stem = key_stem[len(prefix) :]

    music_dir = Path(assets) / "music"

    if key in ("random", "random_soft", "auto"):
        r = rng or random.Random()
        pick = r.choice(list(BGM_PRESETS.keys()))
        path = music_dir / BGM_PRESETS[pick]["file"]
        if not path.is_file():
            raise FileNotFoundError(
                f"BGM preset {pick!r} file missing: {path}. "
                "Run: python scripts/bootstrap_assets.py"
            )
        return path.resolve()

    if key_stem in BGM_PRESETS:
        path = music_dir / BGM_PRESETS[key_stem]["file"]
        if path.is_file():
            return path.resolve()
        raise FileNotFoundError(
            f"BGM preset {key_stem!r} file missing: {path}. "
            "Run: python scripts/bootstrap_assets.py"
        )

    # Direct path / filename
    candidates = [
        Path(raw),
        music_dir / raw,
        music_dir / Path(raw).name,
        music_dir / f"bgm_{key_stem}.mp3",
        music_dir / f"{key_stem}.mp3",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()

    known = ", ".join(list_bgm_ids())
    raise FileNotFoundError(
        f"BGM not found: {bgm!r}. "
        f"Use a path, or preset id: {known}, or 'random'"
    )
