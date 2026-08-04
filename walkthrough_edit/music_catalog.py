"""
BGM presets for pack stage.

Mapped to files that currently exist under ``assets/music/``:

1) **curated/** — short-video completion picks (``pop_*``)
2) **shortlist/** — rhythmic shortlist ``01.mp3`` … ``12.mp3``
3) **a*.m4a** — local library picks ``a1`` … ``a18`` (missing numbers skipped)

Legacy CC BY Kevin MacLeod ids (``carefree`` etc.) still resolve if you re-run
``scripts/fetch_bgm.py``; otherwise they alias to ``pop_hook`` when the mp3 is gone.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

# Prefer curated pop-style picks for 完播; ids go in pack.audio.bgm
BGM_PRESETS: dict[str, dict[str, Any]] = {
    # --- curated/ ---
    "pop_hook": {
        "file": "curated/bgm_pop_hook.mp3",
        "label": "流行钩子短循环（完播首选）",
        "vibe": "约27s 连续律动、前奏不空，适合20s成片循环",
        "group": "curated",
    },
    "pop_spark": {
        "file": "curated/bgm_pop_spark.mp3",
        "label": "明亮轻快开场",
        "vibe": "约20s 抓耳，适合前3秒留人",
        "group": "curated",
    },
    "pop_vibe": {
        "file": "curated/bgm_pop_vibe.mp3",
        "label": "中长流行律动",
        "vibe": "约69s 能量偏高、空间感好",
        "group": "curated",
    },
    "pop_soft": {
        "file": "curated/bgm_pop_soft.mp3",
        "label": "柔和流行",
        "vibe": "约27s 不抢画面，耐听",
        "group": "curated",
    },
    "pop_drive": {
        "file": "curated/bgm_pop_drive.mp3",
        "label": "推进感",
        "vibe": "约22s 适合快节奏带看",
        "group": "curated",
    },
    "pop_clean": {
        "file": "curated/bgm_pop_clean.mp3",
        "label": "干净耐听",
        "vibe": "约26s 循环友好",
        "group": "curated",
    },
}

# shortlist/01.mp3 … 12.mp3 → ids sl01 … sl12 (also accept "01", "shortlist/01")
for _i in range(1, 13):
    _n = f"{_i:02d}"
    BGM_PRESETS[f"sl{_n}"] = {
        "file": f"shortlist/{_n}.mp3",
        "label": f"节奏短名单 {_n}",
        "vibe": f"assets/music/shortlist/{_n}.mp3",
        "group": "shortlist",
    }

# a1.m4a … a18.m4a (a10 may be absent on disk — resolve skips missing)
for _i in range(1, 19):
    BGM_PRESETS[f"a{_i}"] = {
        "file": f"a{_i}.m4a",
        "label": f"本地曲库 a{_i}",
        "vibe": f"assets/music/a{_i}.m4a",
        "group": "local",
    }

# Optional CC BY (only if fetch_bgm.py re-downloaded the files)
_CC_BY: dict[str, dict[str, Any]] = {
    "carefree": {
        "file": "bgm_carefree.mp3",
        "label": "轻松明亮 · Carefree",
        "vibe": "CC BY 备选（需 fetch_bgm.py）",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "group": "cc_by",
    },
    "easy_lemon": {
        "file": "bgm_easy_lemon.mp3",
        "label": "柔和俏皮 · Easy Lemon",
        "vibe": "CC BY 备选（需 fetch_bgm.py）",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "group": "cc_by",
    },
    "life_of_riley": {
        "file": "bgm_life_of_riley.mp3",
        "label": "温暖愉快 · Life of Riley",
        "vibe": "CC BY 备选（需 fetch_bgm.py）",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "group": "cc_by",
    },
    "summer_day": {
        "file": "bgm_summer_day.mp3",
        "label": "夏日通透 · Summer Day",
        "vibe": "CC BY 备选（需 fetch_bgm.py）",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "group": "cc_by",
    },
    "dreamlike": {
        "file": "bgm_dreamlike.mp3",
        "label": "柔和梦幻 · Dreamlike",
        "vibe": "CC BY 备选（需 fetch_bgm.py）",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "group": "cc_by",
    },
    "bittersweet": {
        "file": "bgm_bittersweet.mp3",
        "label": "轻情绪 · Bittersweet",
        "vibe": "CC BY 备选（需 fetch_bgm.py）",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "group": "cc_by",
    },
}
BGM_PRESETS.update(_CC_BY)

# Default BGM preset
DEFAULT_BGM = "random"
LEGACY_ALIASES: dict[str, str] = {
    "pop_hook": "a1",
    "carefree": "a1",
    "easy_lemon": "a1",
    "life_of_riley": "a1",
    "summer_day": "a1",
    "dreamlike": "a1",
    "bittersweet": "a1",
}

CURATED_IDS = [k for k, v in BGM_PRESETS.items() if v.get("group") == "curated"]
SHORTLIST_IDS = [k for k, v in BGM_PRESETS.items() if v.get("group") == "shortlist"]
LOCAL_IDS = [k for k, v in BGM_PRESETS.items() if v.get("group") == "local"]


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg"}


def get_all_present_audio_files(assets: Path) -> list[Path]:
    """Scan assets/music/ for all existing audio files on disk."""
    music_dir = Path(assets) / "music"
    if not music_dir.is_dir():
        return []
    audio_files: list[Path] = []
    for p in music_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            audio_files.append(p.resolve())
    return sorted(audio_files)


def scan_and_register_audio_files(assets: Path) -> None:
    """Dynamically discover any audio files under assets/music/ and register them into BGM_PRESETS."""
    music_dir = Path(assets) / "music"
    if not music_dir.is_dir():
        return
    existing_files: set[Path] = set()
    for meta in list(BGM_PRESETS.values()):
        p = music_dir / meta["file"]
        if p.is_file():
            existing_files.add(p.resolve())

    for path in get_all_present_audio_files(assets):
        if path not in existing_files:
            rel = path.relative_to(music_dir).as_posix()
            stem = path.stem
            key = stem
            suffix = 1
            while key in BGM_PRESETS and (music_dir / BGM_PRESETS[key]["file"]).resolve() != path:
                key = f"{stem}_{suffix}"
                suffix += 1
            BGM_PRESETS[key] = {
                "file": rel,
                "label": f"本地曲目 {path.name}",
                "vibe": f"assets/music/{rel}",
                "group": "local",
            }
            existing_files.add(path)


def list_bgm_ids() -> list[str]:
    return list(BGM_PRESETS.keys())


def list_bgm_ids_present(assets: Path) -> list[str]:
    """Preset ids and dynamically discovered audio files whose files exist on disk."""
    scan_and_register_audio_files(assets)
    music_dir = Path(assets) / "music"
    out: list[str] = []
    for key, meta in BGM_PRESETS.items():
        if (music_dir / meta["file"]).is_file():
            out.append(key)
    return out


def _resolve_preset_file(key: str, music_dir: Path) -> Path | None:
    meta = BGM_PRESETS.get(key)
    if not meta:
        return None
    path = music_dir / meta["file"]
    if path.is_file():
        return path.resolve()
    # legacy alias when CC BY file was deleted
    alt = LEGACY_ALIASES.get(key)
    if alt and alt in BGM_PRESETS:
        alt_path = music_dir / BGM_PRESETS[alt]["file"]
        if alt_path.is_file():
            return alt_path.resolve()
    return None


def resolve_bgm_path(
    bgm: str,
    assets: Path,
    *,
    rng: random.Random | None = None,
) -> Path:
    """
    Resolve pack.audio.bgm to a local file.

    Accepts:
      - preset id: pop_hook / sl01 / a3 / carefree (if present or aliased)
      - random / random_soft / auto / random_pop: curated first, else any present
      - shortlist/01.mp3 or 01.mp3 under shortlist/
      - filename under assets/music/ (incl. curated/)
      - absolute or relative path
    """
    raw = str(bgm or "").strip()
    if not raw:
        raise ValueError("pack.audio.bgm is empty")

    scan_and_register_audio_files(assets)
    music_dir = Path(assets) / "music"
    raw_norm = raw.replace("\\", "/")
    key = raw_norm.split("/")[-1]
    key_stem = Path(key).stem
    for prefix in ("bgm_",):
        if key_stem.startswith(prefix):
            key_stem = key_stem[len(prefix) :]

    if key in ("random", "random_soft", "auto", "random_pop"):
        r = rng or random.Random()
        all_files = get_all_present_audio_files(assets)
        if all_files:
            return r.choice(all_files)
        present = list_bgm_ids_present(assets)
        curated = [i for i in CURATED_IDS if i in present]
        pool = curated or present
        if not pool:
            raise FileNotFoundError(
                "No BGM files found under assets/music "
                "(expected audio files like *.m4a, *.mp3, *.wav, etc.)"
            )
        pick = r.choice(pool)
        return (music_dir / BGM_PRESETS[pick]["file"]).resolve()

    # shortlist number: "01", "1", "sl01", "shortlist/01"
    short_n: str | None = None
    if key_stem.isdigit() and 1 <= int(key_stem) <= 12:
        short_n = f"{int(key_stem):02d}"
    elif key_stem.startswith("sl") and key_stem[2:].isdigit():
        short_n = f"{int(key_stem[2:]):02d}"
    if short_n:
        for c in (
            music_dir / "shortlist" / f"{short_n}.mp3",
            music_dir / f"{short_n}.mp3",
        ):
            if c.is_file():
                return c.resolve()
        # fall through to preset slXX if registered

    # preset id
    if key_stem in BGM_PRESETS:
        found = _resolve_preset_file(key_stem, music_dir)
        if found is not None:
            return found
        present = list_bgm_ids_present(assets)
        if present:
            return (music_dir / BGM_PRESETS[present[0]]["file"]).resolve()
        raise FileNotFoundError(
            f"BGM preset {key_stem!r} file missing: "
            f"{music_dir / BGM_PRESETS[key_stem]['file']}"
        )

    # aN style already covered by presets; path candidates
    candidates = [
        Path(raw),
        music_dir / raw_norm,
        music_dir / Path(raw).name,
        music_dir / "curated" / Path(raw).name,
        music_dir / "shortlist" / Path(raw).name,
        music_dir / f"bgm_{key_stem}.mp3",
        music_dir / f"{key_stem}.mp3",
        music_dir / f"{key_stem}.m4a",
        music_dir / "curated" / f"bgm_{key_stem}.mp3",
        music_dir / "curated" / f"{key_stem}.mp3",
        music_dir / "shortlist" / f"{key_stem}.mp3",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()

    present = list_bgm_ids_present(assets)
    known = ", ".join(present[:24]) + ("…" if len(present) > 24 else "")
    raise FileNotFoundError(
        f"BGM not found: {bgm!r}. Use a path, or preset id among: {known}, or 'random'"
    )
