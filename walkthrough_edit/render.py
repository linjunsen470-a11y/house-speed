"""FFmpeg filter build and export."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def atempo_chain(speed: float) -> str:
    """Build atempo chain (each factor in [0.5, 2.0])."""
    if abs(speed - 1.0) < 1e-6:
        return "anull"
    factors: list[float] = []
    remaining = float(speed)
    while remaining > 2.0 + 1e-9:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={f:.6f}" for f in factors)


def build_filter(segs: list[dict], has_audio: bool) -> str:
    parts: list[str] = []
    concat_in: list[str] = []
    for i, s in enumerate(segs):
        t0, t1, sp = float(s["t0"]), float(s["t1"]), float(s["speed"])
        parts.append(
            f"[0:v]trim=start={t0:.4f}:end={t1:.4f},"
            f"setpts=(PTS-STARTPTS)/{sp:.4f}[v{i}]"
        )
        if has_audio:
            audio_f = atempo_chain(sp)
            if audio_f == "anull":
                parts.append(
                    f"[0:a]atrim=start={t0:.4f}:end={t1:.4f},"
                    f"asetpts=PTS-STARTPTS[a{i}]"
                )
            else:
                parts.append(
                    f"[0:a]atrim=start={t0:.4f}:end={t1:.4f},"
                    f"asetpts=PTS-STARTPTS,{audio_f}[a{i}]"
                )
            concat_in.append(f"[v{i}][a{i}]")
        else:
            concat_in.append(f"[v{i}]")
    n = len(segs)
    if has_audio:
        parts.append("".join(concat_in) + f"concat=n={n}:v=1:a=1[outv][outa]")
    else:
        parts.append("".join(concat_in) + f"concat=n={n}:v=1:a=0[outv]")
    return ";\n".join(parts)


def probe_duration(path: str | Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def has_audio_stream(path: str | Path) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return bool(out)


def export_video(
    input_path: Path,
    output_path: Path,
    segs: list[dict],
    cfg: dict[str, Any],
    filter_script: Path | None = None,
) -> float:
    """Run ffmpeg and return output duration seconds."""
    audio = has_audio_stream(input_path)
    fc = build_filter(segs, has_audio=audio)

    if filter_script is None:
        filter_script = output_path.with_suffix(".filter.txt")
    filter_script.parent.mkdir(parents=True, exist_ok=True)
    filter_script.write_text(fc, encoding="utf-8")

    enc = cfg["encode"]
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-filter_complex_script",
        str(filter_script),
        "-map",
        "[outv]",
    ]
    if audio:
        cmd += [
            "-map",
            "[outa]",
            "-c:a",
            str(enc["audio_codec"]),
            "-b:a",
            str(enc["audio_bitrate"]),
        ]
    cmd += [
        "-c:v",
        str(enc["video_codec"]),
        "-preset",
        str(enc["preset"]),
        "-crf",
        str(enc["crf"]),
        "-pix_fmt",
        str(enc["pixel_format"]),
        "-movflags",
        str(enc["movflags"]),
        str(output_path),
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        err = r.stderr[-4000:] if r.stderr else "ffmpeg failed"
        raise RuntimeError(err)
    return probe_duration(output_path)
