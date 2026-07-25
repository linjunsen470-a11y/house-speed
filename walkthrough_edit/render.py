"""FFmpeg filter build and export."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# Process-wide caches (FFmpeg capability probes are expensive).
_ENCODER_NAMES: set[str] | None = None
_FILTER_COMPLEX_FILE_FLAG: str | None = None
_ENCODER_LINE_RE = re.compile(r"^\s*[VAS][.F\w]+\s+(\S+)")


def check_tools() -> None:
    """Fail early with an actionable message when FFmpeg tools are unavailable."""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            f"Missing required command(s): {', '.join(missing)}. "
            "Install FFmpeg and add its bin directory to PATH."
        )


def select_video_codec(source_codec: str, requested: str = "auto") -> str:
    """Resolve the encoder; auto follows the source codec family."""
    requested = requested.lower()
    if requested in ("libx264", "h264"):
        return "libx264"
    if requested in ("libx265", "hevc", "h265"):
        return "libx265"
    if requested != "auto":
        raise ValueError(f"Unsupported video codec: {requested}")
    return "libx265" if source_codec.lower() in ("hevc", "h265") else "libx264"


def _list_encoders() -> set[str]:
    """Parse `ffmpeg -encoders` into a set of encoder names (cached)."""
    global _ENCODER_NAMES
    if _ENCODER_NAMES is not None:
        return _ENCODER_NAMES
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to list FFmpeg encoders:\n"
            + ((result.stderr or result.stdout or "").strip() or "unknown error")
        )
    names: set[str] = set()
    for line in (result.stdout or "").splitlines():
        match = _ENCODER_LINE_RE.match(line)
        if match:
            names.add(match.group(1))
    _ENCODER_NAMES = names
    return names


def ensure_encoder(codec: str) -> None:
    """Confirm the selected FFmpeg build exposes the requested encoder."""
    names = _list_encoders()
    if codec not in names:
        raise RuntimeError(
            f"FFmpeg encoder {codec} is unavailable; install a build that includes it "
            "or choose another encode.video_codec."
        )


def estimate_output_duration(
    segs: list[dict], fps: float | None = None
) -> float:
    """
    Estimate edited output duration in seconds.

    When ``fps`` is provided, quantize onto the source frame grid
    (``ceil(seconds * fps) / fps``) so the value matches FFmpeg's
    frame-based filter output and the pipeline summary check.
    """
    seconds = sum(
        (float(s["t1"]) - float(s["t0"])) / float(s["speed"]) for s in segs
    )
    if fps is None or fps <= 0:
        return seconds
    return math.ceil(seconds * fps - 1e-9) / fps


def _filter_complex_file_args(filter_script: Path) -> list[str]:
    """
    Prefer modern ``-/filter_complex <path>`` (read filtergraph from file);
    fall back to deprecated ``-filter_complex_script`` on older FFmpeg builds.

    FFmpeg 7+/8 help lists ``-filter_complex_script`` as deprecated in favor of
    the generic ``-/option file`` form applied to ``filter_complex``.
    """
    global _FILTER_COMPLEX_FILE_FLAG
    if _FILTER_COMPLEX_FILE_FLAG is None:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "full"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        help_text = (result.stdout or "") + (result.stderr or "")
        # Deprecation note, or modern builds that document the slash form.
        if (
            "-/filter_complex" in help_text
            or "use -/filter_complex" in help_text
            or "filter_complex_script <filename>  deprecated" in help_text
        ):
            _FILTER_COMPLEX_FILE_FLAG = "-/filter_complex"
        else:
            _FILTER_COMPLEX_FILE_FLAG = "-filter_complex_script"
    return [_FILTER_COMPLEX_FILE_FLAG, str(filter_script)]


def _escape_drawtext_text(text: str) -> str:
    """Escape text for FFmpeg drawtext filter option values."""
    return (
        text.replace("\\", "\\\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace("%", r"\%")
    )


def _escape_drawtext_path(path: str) -> str:
    """Escape a filesystem path for use as drawtext fontfile=..."""
    # Forward slashes are accepted on Windows FFmpeg builds; still escape ':'.
    normalized = path.replace("\\", "/")
    return normalized.replace(":", r"\:").replace("'", r"\'")


def resolve_review_font(configured: str | None = None) -> str | None:
    """
    Resolve a TrueType/OpenType font for review burn-in.

    Returns an absolute path, or None if no usable font is found.
    """
    if configured and str(configured).strip():
        path = Path(str(configured).strip())
        if path.is_file():
            return str(path.resolve())
        return None

    candidates = [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
        Path("/Library/Fonts/Arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return None


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


def build_review_filter(
    segs: list[dict],
    has_audio: bool,
    width: int,
    fps: int = 12,
    fontfile: str | None = None,
) -> str:
    """Build a low-resolution filter with source ranges and decisions burned in."""
    parts: list[str] = []
    concat_in: list[str] = []
    for i, seg in enumerate(segs):
        t0, t1 = float(seg["t0"]), float(seg["t1"])
        speed = float(seg["speed"])
        label = f"src {t0:.2f}-{t1:.2f}s | {seg['kind']} {speed:.2f}x"
        if fontfile:
            text = _escape_drawtext_text(label)
            font = _escape_drawtext_path(fontfile)
            draw = (
                f"drawtext=fontfile='{font}':text='{text}':"
                f"x=16:y=16:fontsize=22:fontcolor=white:"
                f"box=1:boxcolor=black@0.60:boxborderw=8,"
            )
        else:
            # No font available: keep the proxy usable without burn-in.
            draw = ""
        parts.append(
            f"[0:v]trim=start={t0:.6f}:end={t1:.6f},"
            f"setpts=PTS-STARTPTS,fps={int(fps)},scale={int(width)}:-2,"
            f"{draw}"
            f"setpts=PTS/{speed:.6f}[v{i}]"
        )
        if has_audio:
            tempo = atempo_chain(speed)
            chain = (
                f"[0:a]atrim=start={t0:.6f}:end={t1:.6f},"
                "asetpts=PTS-STARTPTS"
            )
            if tempo != "anull":
                chain += f",{tempo}"
            parts.append(chain + f"[a{i}]")
            concat_in.append(f"[v{i}][a{i}]")
        else:
            concat_in.append(f"[v{i}]")
    if has_audio:
        parts.append(
            "".join(concat_in) + f"concat=n={len(segs)}:v=1:a=1[outv][outa]"
        )
    else:
        parts.append("".join(concat_in) + f"concat=n={len(segs)}:v=1:a=0[outv]")
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


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def probe_media(path: str | Path) -> dict[str, Any]:
    """Probe codec and bitrates for size-aware re-encode."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,bit_rate:stream=index,codec_type,codec_name,bit_rate,width,height",
        "-of",
        "json",
        str(path),
    ]
    data = json.loads(subprocess.check_output(cmd, text=True))
    fmt = data.get("format") or {}
    v = next(
        (s for s in data.get("streams") or [] if s.get("codec_type") == "video"),
        {},
    )
    a = next(
        (s for s in data.get("streams") or [] if s.get("codec_type") == "audio"),
        None,
    )
    fmt_br = _int_or_zero(fmt.get("bit_rate"))
    v_br = _int_or_zero(v.get("bit_rate"))
    a_br = _int_or_zero(a.get("bit_rate") if a else None)
    if not v_br and fmt_br:
        # estimate video share if stream bitrate missing
        v_br = max(fmt_br - (a_br or 48_000), fmt_br // 2)
    return {
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "format_bitrate": fmt_br,
        "video_codec": (v.get("codec_name") or "").lower(),
        "video_bitrate": v_br,
        "audio_bitrate": a_br,
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "has_audio": a is not None,
    }


def _kbps(bits_per_sec: int) -> str:
    kb = max(32, int(round(bits_per_sec / 1000.0)))
    return f"{kb}k"


def build_video_audio_args(input_path: Path, enc: dict[str, Any]) -> tuple[list[str], str]:
    """
    Build ffmpeg encode args.

    match_source=true (default): re-encode near the source bitrate/codec family
    so edited files are not several times larger than WeChat/HEVC originals.
    Filters force re-encode (cannot stream-copy).
    """
    match_source = bool(enc.get("match_source", True))
    preset = str(enc.get("preset", "medium"))
    pix = str(enc.get("pixel_format", "yuv420p"))
    note_parts: list[str] = []

    if match_source:
        info = probe_media(input_path)
        scale = float(enc.get("bitrate_scale", 1.0))
        src_v = info["video_codec"]
        force_codec = str(enc.get("video_codec", "auto")).lower()
        vcodec = select_video_codec(src_v, force_codec)

        v_br = int(info["video_bitrate"] * scale) if info["video_bitrate"] else 0
        if v_br < 80_000:
            # fallback floor for tiny probes
            v_br = max(int((info["format_bitrate"] or 400_000) * 0.85 * scale), 120_000)

        a_src = info["audio_bitrate"] or 48_000
        a_br = int(a_src * scale) if a_src else 48_000
        a_br = max(32_000, min(a_br, 96_000))  # keep small for talk/ambient

        # Bitrate mode ≈ original size density; maxrate allows short peaks
        v_args = [
            "-c:v",
            vcodec,
            "-preset",
            preset,
            "-b:v",
            _kbps(v_br),
            "-maxrate",
            _kbps(int(v_br * 1.25)),
            "-bufsize",
            _kbps(int(v_br * 2.0)),
            "-pix_fmt",
            pix,
        ]
        if vcodec == "libx265":
            # Better mobile/WeChat compatibility
            v_args += ["-tag:v", "hvc1", "-x265-params", "log-level=error"]

        a_args = [
            "-c:a",
            str(enc.get("audio_codec", "aac")),
            "-b:a",
            _kbps(a_br),
        ]
        note_parts.append(
            f"match_source {vcodec} v≈{_kbps(v_br)} a≈{_kbps(a_br)} "
            f"(src {src_v} v≈{_kbps(info['video_bitrate'] or 0)})"
        )
        return v_args + a_args, "; ".join(note_parts)

    # Legacy CRF quality mode (often much larger than phone/WeChat HEVC)
    vcodec = str(enc.get("video_codec", "libx264"))
    if vcodec == "auto":
        vcodec = "libx264"
    if vcodec in ("hevc", "h265"):
        vcodec = "libx265"
    crf = str(enc.get("crf", 28))
    v_args = [
        "-c:v",
        vcodec,
        "-preset",
        preset,
        "-crf",
        crf,
        "-pix_fmt",
        pix,
    ]
    if vcodec == "libx265":
        v_args += ["-tag:v", "hvc1", "-x265-params", "log-level=error"]
    a_args = [
        "-c:a",
        str(enc.get("audio_codec", "aac")),
        "-b:a",
        str(enc.get("audio_bitrate", "64k")),
    ]
    note_parts.append(f"crf_mode {vcodec} crf={crf}")
    return v_args + a_args, "; ".join(note_parts)


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Tolerate short-lived Windows scanner/probe file locks."""
    last_error: PermissionError | None = None
    for attempt in range(10):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.1 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _without_audio_args(args: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if token in ("-c:a", "-b:a"):
            skip_next = True
            continue
        cleaned.append(token)
    return cleaned


def _run_ffmpeg_atomic(
    input_path: Path,
    output_path: Path,
    filter_script: Path,
    av_args: list[str],
    has_audio: bool,
    movflags: str,
    log_path: Path,
    expected_duration: float,
) -> float:
    """Render to a temporary sibling, validate it, then atomically replace output."""
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.part{output_path.suffix}")
    temporary.unlink(missing_ok=True)

    codec = av_args[av_args.index("-c:v") + 1]
    ensure_encoder(codec)
    if not has_audio:
        av_args = _without_audio_args(av_args)

    cmd = [
        "ffmpeg", "-y", "-nostdin", "-i", str(input_path),
        *_filter_complex_file_args(filter_script),
        "-map", "[outv]",
    ]
    if has_audio:
        cmd += ["-map", "[outa]"]
    cmd += av_args
    # Cap slightly above the estimate so frame quantization cannot clip the tail
    # while still bounding runaway encodes. Use the same estimate as the pipeline.
    duration_cap = expected_duration
    if expected_duration > 0:
        duration_cap = expected_duration + max(0.05, expected_duration * 0.002)
    cmd += [
        "-movflags", movflags,
        "-t", f"{duration_cap:.6f}",
        "-progress", "pipe:1", "-nostats",
        str(temporary),
    ]

    last_percent = -10
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=log_file,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_ms" and expected_duration > 0:
                    try:
                        seconds = float(value) / 1_000_000.0
                    except ValueError:
                        continue
                    percent = min(100, int(seconds / expected_duration * 100))
                    bucket = percent // 10 * 10
                    if bucket >= last_percent + 10:
                        print(f"  progress: {bucket}%")
                        last_percent = bucket
            returncode = process.wait()
        if returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(tail or "ffmpeg failed")
        duration = probe_duration(temporary)
        if duration <= 0:
            raise RuntimeError("FFmpeg produced an unreadable or empty output")
        _replace_with_retry(temporary, output_path)
        return duration
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError:
            pass
        raise


def export_video(
    input_path: Path,
    output_path: Path,
    segs: list[dict],
    cfg: dict[str, Any],
    filter_script: Path | None = None,
    log_path: Path | None = None,
    fps: float | None = None,
) -> float:
    """Run ffmpeg and return output duration seconds."""
    audio = has_audio_stream(input_path)
    fc = build_filter(segs, has_audio=audio)
    if filter_script is None:
        filter_script = output_path.with_suffix(".filter.txt")
    if log_path is None:
        log_path = filter_script.with_name("ffmpeg.log")
    filter_script.parent.mkdir(parents=True, exist_ok=True)
    filter_script.write_text(fc, encoding="utf-8")

    enc = cfg["encode"]
    av_args, enc_note = build_video_audio_args(input_path, enc)
    print(f"  encode: {enc_note}")
    expected = estimate_output_duration(segs, fps)
    return _run_ffmpeg_atomic(
        input_path, output_path, filter_script, av_args, audio,
        str(enc.get("movflags", "+faststart")), log_path, expected,
    )


def export_review(
    input_path: Path,
    output_path: Path,
    segs: list[dict],
    cfg: dict[str, Any],
    work_dir: Path,
    fps: float | None = None,
) -> float:
    """Export a disposable annotated review proxy."""
    audio = has_audio_stream(input_path)
    review_cfg = cfg.get("review") or {}
    width = int(review_cfg.get("width", 540))
    review_fps = int(review_cfg.get("fps", 12))
    fontfile = resolve_review_font(str(review_cfg.get("fontfile") or "") or None)
    if fontfile is None:
        print(
            "  warning: no review font found; proxy will omit burned-in labels "
            "(set review.fontfile in config.yaml)"
        )
    filter_script = work_dir / "review_filter_complex.txt"
    filter_script.write_text(
        build_review_filter(segs, audio, width, review_fps, fontfile=fontfile),
        encoding="utf-8",
    )
    av_args = [
        "-c:v", "libx264",
        "-preset", str(review_cfg.get("preset", "ultrafast")),
        "-b:v", str(review_cfg.get("video_bitrate", "900k")),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
    ]
    # Prefer source fps for duration estimate; fall back to review fps grid.
    expected = estimate_output_duration(segs, fps if fps and fps > 0 else float(review_fps))
    return _run_ffmpeg_atomic(
        input_path, output_path, filter_script, av_args, audio,
        "+faststart", work_dir / "review_ffmpeg.log", expected,
    )
