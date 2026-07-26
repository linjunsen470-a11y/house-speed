#!/usr/bin/env python3
"""
Select rhythmic pure-music BGM candidates from assets/music.

Pipeline:
  1) basic file hygiene (size/duration)
  2) speech-likeness gate (reject dialogue / talk)
  3) rhythm regularity + groove energy score
  4) print shortlist for human pick

No claim of "完播最优" — only technical filters for:
  rhythmic / dynamic / non-speech music suitable as pad under silent walkthroughs.
"""
from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "assets" / "music"
OUT_JSON = MUSIC / "_rhythmic_shortlist.json"
SR = 22050
ANALYZE_SEC = 24.0


def decode_mono(path: Path, seconds: float = ANALYZE_SEC) -> np.ndarray | None:
    """Decode first N seconds to mono float32 via ffmpeg."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-t",
        str(seconds),
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        str(SR),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size < SR * 3:
        return None
    # peak normalize lightly for stable features
    peak = float(np.max(np.abs(audio)) + 1e-9)
    return (audio / peak).astype(np.float32)


def frame_signal(x: np.ndarray, win: int, hop: int) -> np.ndarray:
    n = 1 + max(0, (len(x) - win) // hop)
    if n < 8:
        return np.zeros((0, win), dtype=np.float32)
    out = np.stack([x[i * hop : i * hop + win] for i in range(n) if i * hop + win <= len(x)])
    return out


def stft_mag(frames: np.ndarray) -> np.ndarray:
    window = np.hanning(frames.shape[1]).astype(np.float32)
    spec = np.fft.rfft(frames * window[None, :], axis=1)
    return np.abs(spec).astype(np.float32) + 1e-10


def speech_music_features(x: np.ndarray) -> dict[str, float]:
    """Heuristic speech vs music features on mono PCM."""
    win = 2048
    hop = 512
    frames = frame_signal(x, win, hop)
    if frames.shape[0] < 20:
        return {"ok": 0.0}

    # RMS / ZCR per frame
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    zcr = np.mean(np.abs(np.diff(np.sign(frames), axis=1)) > 0, axis=1)

    mag = stft_mag(frames)
    freqs = np.fft.rfftfreq(win, 1.0 / SR)
    # band energies
    def band(lo: float, hi: float) -> np.ndarray:
        m = (freqs >= lo) & (freqs < hi)
        return np.sum(mag[:, m], axis=1)

    e_low = band(40, 250)  # kick/bass region
    e_mid = band(250, 2000)  # speech body + mids
    e_high = band(2000, 6000)  # sibilance / hats
    e_tot = e_low + e_mid + e_high + 1e-10
    low_ratio = float(np.mean(e_low / e_tot))
    mid_ratio = float(np.mean(e_mid / e_tot))
    high_ratio = float(np.mean(e_high / e_tot))

    # Spectral flatness (noise-like vs tonal); speech often mid, music more tonal overall
    log_mean = np.mean(np.log(mag), axis=1)
    mean_log = np.log(np.mean(mag, axis=1) + 1e-10)
    flatness = float(np.mean(np.exp(log_mean - mean_log)))

    # Flux of mid band — speech syllable rate ~2–8 Hz peaks in envelope spectrum
    mid_env = e_mid / (np.max(e_mid) + 1e-10)
    mid_env = mid_env - np.mean(mid_env)
    if len(mid_env) >= 32:
        env_fft = np.abs(np.fft.rfft(mid_env * np.hanning(len(mid_env))))
        env_freqs = np.fft.rfftfreq(len(mid_env), hop / SR)
        # speech syllable-ish 2–7 Hz
        speech_band = (env_freqs >= 2.0) & (env_freqs <= 7.0)
        music_beat_band = (env_freqs >= 0.8) & (env_freqs <= 2.5)  # 48–150 BPM fundamental-ish
        speech_mod = float(np.max(env_fft[speech_band]) if np.any(speech_band) else 0.0)
        beat_mod = float(np.max(env_fft[music_beat_band]) if np.any(music_beat_band) else 0.0)
        mod_sum = speech_mod + beat_mod + 1e-10
        speech_mod_ratio = speech_mod / mod_sum
        beat_mod_ratio = beat_mod / mod_sum
    else:
        speech_mod_ratio = 0.5
        beat_mod_ratio = 0.5

    # Autocorr of onset envelope for rhythm regularity
    onset = np.maximum(0.0, np.diff(rms, prepend=rms[0]))
    onset = onset / (np.max(onset) + 1e-10)
    # limit lag to 0.3–1.5s (40–200 BPM)
    min_lag = int(0.30 * SR / hop)
    max_lag = int(1.50 * SR / hop)
    max_lag = min(max_lag, len(onset) - 2)
    rhythm_strength = 0.0
    tempo_bpm = 0.0
    if max_lag > min_lag + 3:
        # normalized autocorr
        o = onset - np.mean(onset)
        denom = float(np.dot(o, o) + 1e-10)
        best = 0.0
        best_lag = min_lag
        for lag in range(min_lag, max_lag):
            v = float(np.dot(o[lag:], o[:-lag]) / denom)
            if v > best:
                best = v
                best_lag = lag
        rhythm_strength = max(0.0, best)
        tempo_bpm = 60.0 / (best_lag * hop / SR)

    rms_cv = float(np.std(rms) / (np.mean(rms) + 1e-10))
    zcr_mean = float(np.mean(zcr))
    zcr_std = float(np.std(zcr))

    # Speech score: high mid, high zcr, syllable modulation, weak bass, weak beat autocorr
    speech = 0.0
    speech += 2.2 * mid_ratio
    speech += 1.5 * high_ratio
    speech -= 2.0 * low_ratio
    speech += 3.0 * min(zcr_mean * 8.0, 1.5)
    speech += 1.8 * speech_mod_ratio
    speech -= 2.2 * beat_mod_ratio
    speech -= 2.5 * rhythm_strength
    speech += 0.4 * min(rms_cv, 1.5)
    # music with strong bass + rhythm should go negative speech

    # Rhythm / groove score for short-video BGM
    groove = 0.0
    groove += 3.5 * rhythm_strength
    groove += 1.8 * beat_mod_ratio
    groove += 1.5 * low_ratio
    groove += 0.8 * min(high_ratio * 2.0, 1.0)  # hats/drive ok
    groove -= 1.2 * speech_mod_ratio
    groove -= 1.5 * max(0.0, zcr_mean - 0.12) * 5.0
    if 70 <= tempo_bpm <= 140:
        groove += 1.0
    elif 60 <= tempo_bpm < 70 or 140 < tempo_bpm <= 160:
        groove += 0.4

    # Energy not dead
    if float(np.mean(rms)) > 0.02:
        groove += 0.3

    return {
        "ok": 1.0,
        "speech_score": float(speech),
        "groove_score": float(groove),
        "rhythm_strength": float(rhythm_strength),
        "tempo_bpm": float(tempo_bpm),
        "low_ratio": low_ratio,
        "mid_ratio": mid_ratio,
        "high_ratio": high_ratio,
        "zcr_mean": zcr_mean,
        "zcr_std": zcr_std,
        "flatness": flatness,
        "speech_mod_ratio": float(speech_mod_ratio),
        "beat_mod_ratio": float(beat_mod_ratio),
        "rms_cv": rms_cv,
    }


def probe_duration(path: Path) -> float:
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
            timeout=20,
        )
        return float(raw.strip() or 0)
    except Exception:
        return 0.0


def main() -> int:
    files = sorted(
        p
        for p in MUSIC.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
        and not p.name.startswith("_")
        and p.name not in {"demo_bgm.mp3"}
        and "curated" not in str(p)
    )
    # also skip known cc-by long beds if we want only user lib — keep them but mark
    results: list[dict] = []
    rejected_speech: list[dict] = []
    rejected_other: list[dict] = []

    print(f"scanning {len(files)} files…")
    for i, path in enumerate(files, 1):
        size = path.stat().st_size
        if size < 80_000:
            rejected_other.append({"name": path.name, "reason": "too_small"})
            continue
        dur = probe_duration(path)
        if dur < 12 or dur > 360:
            rejected_other.append({"name": path.name, "reason": f"duration={dur:.1f}"})
            continue

        audio = decode_mono(path)
        if audio is None:
            rejected_other.append({"name": path.name, "reason": "decode_fail"})
            continue

        feat = speech_music_features(audio)
        if not feat.get("ok"):
            rejected_other.append({"name": path.name, "reason": "feat_fail"})
            continue

        speech = feat["speech_score"]
        groove = feat["groove_score"]
        rhythm = feat["rhythm_strength"]

        row = {
            "name": path.name,
            "path": str(path.resolve()),
            "dur": round(dur, 2),
            "size_kb": size // 1024,
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in feat.items() if k != "ok"},
        }

        # Hard reject likely dialogue / talk
        # Tuned: speech_score high OR (high mid + high zcr + weak rhythm)
        is_speech = (
            speech >= 1.35
            or (speech >= 0.85 and rhythm < 0.12 and feat["mid_ratio"] > 0.42)
            or (feat["zcr_mean"] > 0.18 and rhythm < 0.10 and feat["low_ratio"] < 0.18)
        )
        if is_speech:
            row["reason"] = "speech_like"
            rejected_speech.append(row)
            print(f"[{i}/{len(files)}] REJECT speech  s={speech:+.2f} r={rhythm:.2f}  {path.name[:48]}")
            continue

        # Prefer rhythmic / dynamic music
        if rhythm < 0.08 and groove < 0.8:
            row["reason"] = "weak_rhythm"
            rejected_other.append(row)
            print(f"[{i}/{len(files)}] REJECT weak    g={groove:+.2f} r={rhythm:.2f}  {path.name[:48]}")
            continue

        # Combined rank for user shortlist (not "完播 magic")
        rank = (
            2.4 * rhythm
            + 1.2 * max(0.0, groove)
            + 0.6 * feat["low_ratio"]
            + 0.4 * feat["beat_mod_ratio"]
            - 0.9 * max(0.0, speech)
        )
        # slight preference for usable loop lengths
        if 20 <= dur <= 90:
            rank += 0.35
        elif 90 < dur <= 180:
            rank += 0.15

        row["rank"] = round(float(rank), 4)
        results.append(row)
        print(
            f"[{i}/{len(files)}] KEEP  rank={rank:+.2f} s={speech:+.2f} "
            f"r={rhythm:.2f} bpm≈{feat['tempo_bpm']:.0f}  {path.name[:44]}"
        )

    results.sort(key=lambda r: -r["rank"])
    # top shortlist
    shortlist = results[:18]

    OUT_JSON.write_text(
        json.dumps(
            {
                "shortlist": shortlist,
                "all_music": results,
                "rejected_speech": [
                    {"name": r["name"], "speech_score": r.get("speech_score"), "rhythm_strength": r.get("rhythm_strength")}
                    for r in rejected_speech
                ],
                "rejected_other_count": len(rejected_other),
                "notes": (
                    "Hard-filtered speech-like files. Rank emphasizes rhythm_strength "
                    "and groove; please A/B listen shortlist before using in pack."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n======== 人声/对话类已剔除 ========")
    print(f"speech-like rejected: {len(rejected_speech)}")
    print(f"other rejected: {len(rejected_other)}")
    print(f"music kept: {len(results)}")
    print("\n======== 节奏向短名单（请你试听挑选）========")
    for i, r in enumerate(shortlist, 1):
        print(
            f"{i:2d}. rank={r['rank']:+.2f}  bpm≈{r['tempo_bpm']:.0f}  "
            f"rhythm={r['rhythm_strength']:.2f}  speech={r['speech_score']:+.2f}  "
            f"dur={r['dur']:.0f}s  {r['name']}"
        )
    print(f"\nwrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
