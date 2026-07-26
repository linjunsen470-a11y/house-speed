#!/usr/bin/env python3
"""Score local BGM candidates for short-video completion-friendly popular vibe."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "assets" / "music"
OUT = MUSIC / "_audit.json"


def ffprobe(path: Path) -> dict:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate,size",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        timeout=30,
    )
    return json.loads(raw)


def ffmpeg_vol(path: Path, t: float = 10.0) -> tuple[float | None, float | None]:
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-t",
            str(t),
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=60,
    )
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    mean = maxv = None
    for line in err.splitlines():
        if "mean_volume:" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0].strip())
        if "max_volume:" in line:
            maxv = float(line.split("max_volume:")[1].split("dB")[0].strip())
    return mean, maxv


def silence_ratio(path: Path, t: float = 12.0) -> float:
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-t",
            str(t),
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-40dB:d=0.3",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=60,
    )
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    sil = 0.0
    for line in err.splitlines():
        if "silence_duration:" in line:
            m = re.search(r"silence_duration:\s*([0-9.]+)", line)
            if m:
                sil += float(m.group(1))
    return min(1.0, sil / max(t, 0.1))


def score_row(r: dict) -> int:
    mean = r["mean"]
    maxv = r["maxv"]
    sil = r["sil"]
    dur = r["dur"]
    mean0 = r.get("mean0")
    score = 0
    # Prefer loopable popular lengths for 15–40s walkthrough packs
    if 15 <= dur <= 90:
        score += 25
    elif 90 < dur <= 180:
        score += 20
    elif 8 <= dur < 15:
        score += 12
    elif 180 < dur <= 300:
        score += 12
    else:
        score += 5
    # Popular short-video BGM loudness band
    if -22 <= mean <= -12:
        score += 20
    elif -26 <= mean < -22 or -12 < mean <= -10:
        score += 12
    else:
        score += 4
    if maxv <= -0.5:
        score += 8
    elif maxv <= 0:
        score += 4
    else:
        score -= 5
    # Continuous groove helps retention
    if sil <= 0.05:
        score += 20
    elif sil <= 0.15:
        score += 12
    elif sil <= 0.3:
        score += 5
    else:
        score -= 10
    # Opening energy (hook) without being empty
    if mean0 is not None:
        if -24 <= mean0 <= -10:
            score += 15
        elif -30 <= mean0 < -24:
            score += 8
        else:
            score += 3
    dr = maxv - mean
    if 8 <= dr <= 22:
        score += 10
    elif 5 <= dr < 8 or 22 < dr <= 28:
        score += 5
    r["dr"] = round(dr, 1)
    return score


def main() -> int:
    files = sorted(
        p
        for p in MUSIC.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
    )
    print(f"candidates={len(files)}")
    rows: list[dict] = []
    for p in files:
        size = p.stat().st_size
        if size < 30_000:
            rows.append({"name": p.name, "size": size, "skip": "too_small"})
            print(f"skip small {p.name}")
            continue
        try:
            info = ffprobe(p)
        except Exception as exc:
            rows.append({"name": p.name, "size": size, "skip": f"probe:{exc}"})
            continue
        dur = float((info.get("format") or {}).get("duration") or 0)
        if dur < 3:
            rows.append({"name": p.name, "size": size, "dur": dur, "skip": "too_short"})
            continue
        if dur > 600:
            rows.append({"name": p.name, "size": size, "dur": dur, "skip": "too_long"})
            continue
        t = min(12.0, dur)
        mean, maxv = ffmpeg_vol(p, t)
        sil = silence_ratio(p, t)
        mean0, _ = ffmpeg_vol(p, min(3.0, dur))
        row = {
            "name": p.name,
            "path": str(p.resolve()),
            "size": size,
            "dur": round(dur, 2),
            "mean": mean,
            "maxv": maxv,
            "sil": round(sil, 3),
            "mean0": mean0,
            "skip": None,
        }
        if mean is None or maxv is None:
            row["skip"] = "no_loudness"
            rows.append(row)
            continue
        row["score"] = score_row(row)
        rows.append(row)
        print(
            f"ok score={row['score']:3d} {dur:6.1f}s mean={mean:6.1f} "
            f"sil={sil:.2f} {p.name[:48]}"
        )

    scored = [r for r in rows if not r.get("skip")]
    scored.sort(key=lambda x: (-x["score"], x["name"]))
    print("\n=== TOP 12 (完播友好 / 流行向代理分) ===")
    for i, r in enumerate(scored[:12], 1):
        print(
            f"{i:2d}. score={r['score']:3d} dur={r['dur']:6.1f}s "
            f"mean={r['mean']:6.1f} peak={r['maxv']:5.1f} sil={r['sil']:.2f}  {r['name']}"
        )

    OUT.write_text(
        json.dumps({"scored": scored, "all": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {OUT} analyzed={len(scored)} skipped={len(rows)-len(scored)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
