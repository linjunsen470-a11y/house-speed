"""Local-first TTS for pack voiceover (edge-tts optional, silence fallback)."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Callable


def _sha1_text(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def probe_audio_duration(path: Path) -> float:
    """Return media duration via ffprobe; 0.0 on failure."""
    try:
        raw = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
        return max(0.0, float(raw))
    except (subprocess.CalledProcessError, ValueError, OSError):
        return 0.0


def write_silence_wav(path: Path, duration: float, *, sample_rate: int = 24000) -> Path:
    """Write a mono 16-bit PCM silence WAV (no ffmpeg required)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = max(1, int(round(max(0.05, duration) * sample_rate)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return path


def _edge_tts_available() -> bool:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False
    return True


def synthesize_edge_tts(
    text: str,
    out_path: Path,
    *,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+8%",
) -> Path:
    """Synthesize with edge-tts (requires network + package)."""
    import asyncio

    import edge_tts

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
        await communicate.save(str(out_path))

    asyncio.run(_run())
    if not out_path.is_file() or out_path.stat().st_size < 32:
        raise RuntimeError("edge-tts produced empty audio")
    return out_path


def resolve_engine(requested: str) -> str:
    """
    auto → edge if importable, else silence.
    edge → edge (caller handles failure).
    silence → silence.
    """
    eng = str(requested or "auto").strip().lower()
    if eng in {"silence", "silent", "none", "mute"}:
        return "silence"
    if eng in {"edge", "edge-tts", "edge_tts"}:
        return "edge"
    # auto
    if _edge_tts_available():
        return "edge"
    return "silence"


SynthFn = Callable[..., Path]


def synthesize_line(
    text: str,
    cache_dir: Path,
    *,
    engine: str = "auto",
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+8%",
    duration_hint: float | None = None,
) -> tuple[Path, float, str]:
    """
    Synthesize one line.

    Returns (path, duration_sec, engine_used).
    On edge failure, falls back to silence of duration_hint (or 1.2s).
    """
    speech = str(text or "").strip()
    if not speech:
        raise ValueError("TTS text is empty")

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolve_engine(engine)
    key = _sha1_text(resolved, voice, rate, speech)
    out = cache_dir / f"vo_{key[:16]}.mp3"
    # Silence uses wav; edge uses mp3
    if resolved == "silence":
        out = cache_dir / f"vo_{key[:16]}.wav"
        hint = float(duration_hint) if duration_hint and duration_hint > 0 else 1.2
        if not out.is_file():
            write_silence_wav(out, hint)
        dur = probe_audio_duration(out) or hint
        return out, dur, "silence"

    # edge
    meta_path = cache_dir / f"vo_{key[:16]}.json"
    if out.is_file() and out.stat().st_size > 32:
        dur = probe_audio_duration(out)
        if dur > 0.05:
            return out, dur, "edge"

    try:
        synthesize_edge_tts(speech, out, voice=voice, rate=rate)
        dur = probe_audio_duration(out)
        if dur <= 0.05:
            raise RuntimeError("edge-tts duration unreadable")
        meta_path.write_text(
            json.dumps({"engine": "edge", "voice": voice, "rate": rate, "text": speech}, ensure_ascii=False),
            encoding="utf-8",
        )
        return out, dur, "edge"
    except Exception:
        # Soft-fail to silence so pack still completes
        hint = float(duration_hint) if duration_hint and duration_hint > 0 else 1.2
        silent = cache_dir / f"vo_{key[:16]}_silence.wav"
        write_silence_wav(silent, hint)
        dur = probe_audio_duration(silent) or hint
        return silent, dur, "silence"


def synthesize_cues(
    cues: list[dict[str, Any]],
    cache_dir: Path,
    *,
    engine: str = "auto",
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+8%",
) -> tuple[list[dict[str, Any]], str]:
    """
    Fill each cue with audio path + measured duration; re-layout starts/ends
    is the caller's job if durations change significantly.

    Returns (updated_cues, primary_engine).
    """
    engines: list[str] = []
    out: list[dict[str, Any]] = []
    for cue in cues:
        speech = str(cue.get("speech") or cue.get("text") or "").strip()
        hint = max(0.05, float(cue["end"]) - float(cue["start"]))
        path, dur, used = synthesize_line(
            speech,
            cache_dir,
            engine=engine,
            voice=voice,
            rate=rate,
            duration_hint=hint,
        )
        engines.append(used)
        updated = dict(cue)
        updated["audio"] = str(path.resolve())
        updated["audio_duration"] = round(dur, 3)
        # Keep planned window unless measured audio is meaningfully different;
        # pack assembler uses audio_duration for adelay length, start for place.
        out.append(updated)
    primary = "edge" if engines and all(e == "edge" for e in engines) else (
        "silence" if engines and all(e == "silence" for e in engines) else "mixed"
    )
    if not engines:
        primary = "none"
    return out, primary


def assemble_voice_track(
    cues: list[dict[str, Any]],
    duration: float,
    out_path: Path,
    *,
    work_dir: Path | None = None,
) -> Path | None:
    """
    Build a full-length mono voice track with each cue delayed to cue.start.

    Returns path, or None if no usable cue audio / ffmpeg missing.
    """
    if duration <= 0:
        return None
    usable = [
        c
        for c in cues
        if Path(str(c.get("audio") or "")).is_file() and float(c.get("end", 0)) > float(c.get("start", 0))
    ]
    if not usable:
        return None
    if not shutil.which("ffmpeg"):
        return None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir) if work_dir else out_path.parent
    work.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(f".{out_path.stem}.part{out_path.suffix}")
    temporary.unlink(missing_ok=True)

    # Base silence + each clip
    inputs: list[str] = [
        "-f",
        "lavfi",
        "-t",
        f"{duration:.6f}",
        "-i",
        "anullsrc=channel_layout=mono:sample_rate=24000",
    ]
    for cue in usable:
        inputs += ["-i", str(Path(str(cue["audio"])).resolve())]

    filter_parts: list[str] = []
    mix_labels = ["[0:a]"]
    for i, cue in enumerate(usable):
        idx = i + 1
        delay_ms = max(0, int(round(float(cue["start"]) * 1000)))
        # adelay needs per-channel ms; apad+atrim keep length stable in amix
        label = f"[vo{i}]"
        filter_parts.append(
            f"[{idx}:a]aformat=sample_rates=24000:channel_layouts=mono,"
            f"adelay={delay_ms}|{delay_ms},apad,atrim=0:{duration:.6f},"
            f"asetpts=PTS-STARTPTS{label}"
        )
        mix_labels.append(label)

    n = len(mix_labels)
    filter_parts.append(
        "".join(mix_labels)
        + f"amix=inputs={n}:duration=first:dropout_transition=0:normalize=0[outa]"
    )
    filter_script = work / "vo_mix_filter.txt"
    filter_script.write_text(";\n".join(filter_parts), encoding="utf-8")

    # Prefer filter_complex_script for long paths (Windows)
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        *inputs,
        "-filter_complex_script",
        str(filter_script),
        "-map",
        "[outa]",
        "-c:a",
        "pcm_s16le",
        "-t",
        f"{duration:.6f}",
        str(temporary),
    ]
    log_path = work / "vo_mix_ffmpeg.log"
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_file,
                check=False,
            )
        if proc.returncode != 0 or not temporary.is_file():
            temporary.unlink(missing_ok=True)
            return None
        temporary.replace(out_path)
        return out_path
    except OSError:
        temporary.unlink(missing_ok=True)
        return None


def merge_duck_windows(
    cues: list[dict[str, Any]],
    *,
    hold_gap: float = 0.45,
) -> list[tuple[float, float]]:
    """Merge adjacent cue intervals when gap < hold_gap (avoids BGM pump)."""
    intervals: list[tuple[float, float]] = []
    for cue in cues:
        s = float(cue.get("start", 0))
        e = float(cue.get("end", 0))
        if e > s:
            intervals.append((s, e))
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged: list[list[float]] = [[intervals[0][0], intervals[0][1]]]
    hold = max(0.0, float(hold_gap))
    for s, e in intervals[1:]:
        if s <= merged[-1][1] + hold:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def duck_volume_expr(
    cues: list[dict[str, Any]],
    *,
    normal: float,
    duck: float,
    attack: float = 0.18,
    release: float = 0.45,
    hold_gap: float = 0.45,
) -> str:
    """
    Build ffmpeg volume=… expression with linear attack/release ramps.

    Adjacent cues within hold_gap share one duck window so BGM does not
    bounce between sentences.
    """
    normal_v = max(0.0, float(normal))
    duck_v = max(0.0, float(duck))
    atk = max(0.02, float(attack))
    rel = max(0.02, float(release))
    windows = merge_duck_windows(cues, hold_gap=hold_gap)
    if not windows:
        return f"volume={normal_v:.4f}"

    # Build nested if from the outside (t-axis left→right handled by nesting reverse).
    # For each window [s,e]:
    #   t < s            → (outer)
    #   s..s+atk         → lerp normal→duck
    #   s+atk..e         → duck
    #   e..e+rel         → lerp duck→normal
    #   else             → normal
    expr = f"{normal_v:.4f}"
    for s, e in reversed(windows):
        s_atk = s + atk
        e_rel = e + rel
        # lerp attack: duck + (normal-duck)*(1 - (t-s)/atk) = normal + (duck-normal)*(t-s)/atk
        # ffmpeg expr: normal_v+(duck_v-normal_v)*(t-s)/atk
        attack_lerp = (
            f"{normal_v:.4f}+({duck_v:.4f}-{normal_v:.4f})*(t-{s:.3f})/{atk:.3f}"
        )
        release_lerp = (
            f"{duck_v:.4f}+({normal_v:.4f}-{duck_v:.4f})*(t-{e:.3f})/{rel:.3f}"
        )
        expr = (
            f"if(lt(t\\,{s:.3f})\\,{expr}\\,"
            f"if(lt(t\\,{s_atk:.3f})\\,{attack_lerp}\\,"
            f"if(lt(t\\,{e:.3f})\\,{duck_v:.4f}\\,"
            f"if(lt(t\\,{e_rel:.3f})\\,{release_lerp}\\,{expr}))))"
        )
    return f"volume='{expr}':eval=frame"


def sidechain_duck_filter(
    *,
    bgm_label: str,
    voice_label: str,
    out_label: str,
    bgm_normal: float,
    vo_volume: float,
    threshold: float = 0.02,
    ratio: float = 8.0,
    attack_s: float = 0.18,
    release_s: float = 0.45,
    duration: float,
) -> list[str]:
    """
    Return filter_complex lines: sidechain-compress BGM from VO, then amix.

    Labels should include brackets, e.g. '[2:a]', '[3:a]', '[outa]'.
    """
    thr = max(0.001, float(threshold))
    rat = max(1.0, float(ratio))
    atk = max(0.01, float(attack_s))
    rel = max(0.05, float(release_s))
    bn = max(0.0, float(bgm_normal))
    vv = max(0.0, float(vo_volume))
    d = max(0.05, float(duration))
    return [
        (
            f"{bgm_label}atrim=0:{d:.6f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={bn:.4f}[bgm_pre]"
        ),
        (
            f"{voice_label}atrim=0:{d:.6f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={vv:.4f}[vo_pre]"
        ),
        "[vo_pre]asplit=2[vo_key][vo_dry]",
        (
            f"[bgm_pre][vo_key]sidechaincompress="
            f"threshold={thr:.4f}:ratio={rat:.2f}:attack={atk:.3f}:release={rel:.3f}:"
            f"level_sc=1:mix=1[bgm_sc]"
        ),
        (
            f"[bgm_sc][vo_dry]amix=inputs=2:duration=first:"
            f"dropout_transition=0:normalize=0{out_label}"
        ),
    ]

