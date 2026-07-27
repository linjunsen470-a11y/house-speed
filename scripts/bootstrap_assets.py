#!/usr/bin/env python3
"""Download open fonts, generate DM stickers, create a demo BGM for testing."""
from __future__ import annotations

import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
STICKERS = ASSETS / "stickers"
MUSIC = ASSETS / "music"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"download {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "clip-bootstrap/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
        out.write(resp.read())
    print(f"  -> {dest} ({dest.stat().st_size} bytes)")


def fetch_smiley_sans() -> None:
    target = FONTS / "SmileySans-Oblique.ttf"
    if target.is_file() and target.stat().st_size > 10_000:
        print(f"skip existing {target.name}")
        return
    # Latest known release zip
    url = "https://github.com/atelier-anchor/smiley-sans/releases/download/v2.0.1/smiley-sans-v2.0.1.zip"
    zpath = FONTS / "smiley-sans.zip"
    _download(url, zpath)
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            if name.lower().endswith((".ttf", ".otf")) and "SmileySans" in name:
                data = zf.read(name)
                out = FONTS / Path(name).name
                out.write_bytes(data)
                print(f"  extracted {out.name}")
    zpath.unlink(missing_ok=True)


def generate_stickers() -> None:
    sys.path.insert(0, str(ROOT))
    from walkthrough_edit.stickers_gen import ensure_builtin_stickers

    # Downloads Noto Animated Emoji GIFs (if needed) and builds APNG badges
    ensure_builtin_stickers(STICKERS, force=False)


def demo_bgm() -> None:
    """Fallback sine demo if network BGM fetch fails."""
    MUSIC.mkdir(parents=True, exist_ok=True)
    out = MUSIC / "demo_bgm.mp3"
    if out.is_file() and out.stat().st_size > 1000:
        print(f"skip existing {out.name}")
        return
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=45",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=45",
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=first,volume=0.08,"
        "afade=t=in:d=1,afade=t=out:st=43:d=2",
        "-c:a", "libmp3lame", "-q:a", "6",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    print(f"wrote {out}")


def fetch_bgm_library() -> None:
    fetch_script = Path(__file__).with_name("fetch_bgm.py")
    subprocess.run([sys.executable, str(fetch_script)], check=True)


def main() -> int:
    FONTS.mkdir(parents=True, exist_ok=True)
    STICKERS.mkdir(parents=True, exist_ok=True)
    MUSIC.mkdir(parents=True, exist_ok=True)
    fetch_smiley_sans()
    generate_stickers()
    try:
        fetch_bgm_library()
    except Exception as exc:
        print(f"BGM library fetch failed ({exc}); writing demo_bgm only")
        demo_bgm()
    else:
        demo_bgm()  # keep tiny test tone as optional fallback
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
