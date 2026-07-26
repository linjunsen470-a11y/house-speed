#!/usr/bin/env python3
"""Download built-in short-video BGM presets (Kevin MacLeod / CC BY 4.0)."""
from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "assets" / "music"

# Stable filenames used by walkthrough_edit.music_catalog
TRACKS: list[tuple[str, str]] = [
    ("bgm_carefree.mp3", "Carefree.mp3"),
    ("bgm_easy_lemon.mp3", "Easy%20Lemon.mp3"),
    ("bgm_life_of_riley.mp3", "Life%20of%20Riley.mp3"),
    ("bgm_summer_day.mp3", "Summer%20Day.mp3"),
    ("bgm_dreamlike.mp3", "Dreamlike.mp3"),
    ("bgm_bittersweet.mp3", "Bittersweet.mp3"),
]
BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"download {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "clip-bgm/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())
    print(f"  -> {dest.name} ({dest.stat().st_size // 1024} KB)")


def _loudnorm(path: Path) -> None:
    tmp = path.with_suffix(".norm.mp3")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "libmp3lame", "-q:a", "4",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True)
        if tmp.is_file() and tmp.stat().st_size > 50_000:
            tmp.replace(path)
            print(f"  loudnorm {path.name}")
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"  loudnorm skip: {exc}")
        tmp.unlink(missing_ok=True)


def main() -> int:
    MUSIC.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv
    for filename, remote in TRACKS:
        dest = MUSIC / filename
        if dest.is_file() and dest.stat().st_size > 100_000 and not force:
            print(f"skip existing {filename}")
            continue
        _download(BASE + remote, dest)
        _loudnorm(dest)
    print("done. See assets/music/README.md for CC BY attribution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
