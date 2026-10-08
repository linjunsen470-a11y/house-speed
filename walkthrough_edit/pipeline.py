"""End-to-end walkthrough edit pipeline."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
import zipfile
from typing import Any

from .analyze import analyze_motion
from .classify import build_segments
from .config import load_config, merge_config_file
from .render import (
    check_tools,
    estimate_output_duration,
    export_review,
    export_video,
    probe_duration,
    probe_media,
)


@dataclass
class PipelineResult:
    input_path: Path
    output_path: Path | None
    segments: list[dict]
    duration_in: float
    duration_out_est: float
    duration_out: float | None
    work_dir: Path
    dry_run: bool
    cache_hit: bool = False
    summary_path: Path | None = None
    review_path: Path | None = None
    summary: dict[str, Any] | None = None


def _print_plan(segs: list[dict]) -> tuple[float, float]:
    print("=== Segment plan (complete source timeline preserved) ===")
    total_in = total_out = 0.0
    for i, s in enumerate(segs, 1):
        d = s["t1"] - s["t0"]
        o = d / s["speed"]
        total_in += d
        total_out += o
        print(
            f"{i:2d}. {s['t0']:6.2f}-{s['t1']:6.2f}s  "
            f"{s['kind']:4s}  {s['speed']:.2f}x  "
            f"({d:.2f}s -> {o:.2f}s)"
        )
    ratio = (total_out / total_in * 100) if total_in else 0
    print(f"\nIn: {total_in:.2f}s  Out≈{total_out:.2f}s  ({ratio:.1f}% duration)")
    return total_in, total_out


def _work_dir(input_path: Path, configured: str) -> Path:
    digest = hashlib.sha1(str(input_path.resolve()).encode("utf-8")).hexdigest()[:8]
    return Path(configured) / f"{input_path.stem}-{digest}"


# Bump when row schema / features used by classify change (invalidates cache).
_ANALYSIS_CACHE_VERSION = 3


def _analysis_fingerprint(input_path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    stat = input_path.stat()
    return {
        "path": str(input_path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "analysis": cfg["analysis"],
        "cache_version": _ANALYSIS_CACHE_VERSION,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    """Write a small metadata file atomically in its destination directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            file.write(text)
            temporary = Path(file.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _read_motion(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            rows.append({
                "idx": int(row["idx"]), "t": float(row["t"]),
                "mean": float(row["mean"]), "std": float(row["std"]),
                "edge": float(row["edge"]), "motion": float(row["motion"]),
            })
    if not rows:
        raise ValueError(f"Empty analysis cache: {path}")
    for index, row in enumerate(rows):
        if row["idx"] != index or not all(math.isfinite(row[key]) for key in ("t", "mean", "std", "edge", "motion")):
            raise ValueError(f"Invalid analysis cache row {index}: {path}")
        if row["t"] < 0 or (index == 0 and abs(row["t"]) > 1e-6) or (index > 0 and row["t"] <= rows[index - 1]["t"]):
            raise ValueError(f"Invalid analysis cache timestamps: {path}")
    return rows


def _write_motion(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(
                file, fieldnames=["idx", "t", "mean", "std", "edge", "motion"]
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "idx": row["idx"],
                    "t": row["t"],
                    "mean": row["mean"],
                    "std": row["std"],
                    "edge": row["edge"],
                    "motion": row["motion"],
                })
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _features_path(work: Path) -> Path:
    return work / "analysis_features.npz"


def _write_features(path: Path, rows: list[dict]) -> None:
    """Sidecar for appearance fingerprints + phase-correlation flow."""
    import numpy as np

    appearance = np.stack(
        [np.asarray(r["appearance"], dtype=np.float32) for r in rows], axis=0
    )
    scalar_keys = (
        "flow_dx",
        "flow_dy",
        "flow_response",
        "flow_magnitude",
        "flow_direction_consistency",
        "entropy",
        "sharpness",
        "information_score",
        "camera_motion_score",
    )
    scalars = {
        key: np.array([float(r.get(key, 0.0)) for r in rows], dtype=np.float32)
        for key in scalar_keys
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp.npz",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            np.savez_compressed(file, appearance=appearance, **scalars)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _attach_features(rows: list[dict], path: Path) -> bool:
    """Merge appearance/flow from npz into rows. Returns False if missing/mismatch."""
    import numpy as np

    if not path.is_file():
        return False
    scalar_keys = (
        "flow_dx",
        "flow_dy",
        "flow_response",
        "flow_magnitude",
        "flow_direction_consistency",
        "entropy",
        "sharpness",
        "information_score",
        "camera_motion_score",
    )
    try:
        with np.load(path, allow_pickle=False) as data:
            appearance = np.asarray(data["appearance"], dtype=np.float32).copy()
            arrays = {
                key: np.asarray(data[key], dtype=np.float32).copy()
                for key in scalar_keys
            }
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return False
    n = len(rows)
    if appearance.ndim != 2 or appearance.shape[0] != n or appearance.shape[1] == 0:
        return False
    if any(array.ndim != 1 or len(array) != n for array in arrays.values()):
        return False
    if not np.isfinite(appearance).all() or any(
        not np.isfinite(array).all() for array in arrays.values()
    ):
        return False
    for i, row in enumerate(rows):
        row["appearance"] = appearance[i]
        for key, array in arrays.items():
            row[key] = float(array[i])
    return True


def _load_analysis(
    input_path: Path,
    cfg: dict[str, Any],
    work: Path,
    reanalyze: bool,
) -> tuple[list[dict], float, bool]:
    motion_path = work / "motion.csv"
    meta_path = work / "analysis_cache.json"
    features_path = _features_path(work)
    fingerprint = _analysis_fingerprint(input_path, cfg)
    cache_enabled = bool((cfg.get("cache") or {}).get("enabled", True))
    if cache_enabled and not reanalyze and motion_path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                raise ValueError("Invalid analysis cache metadata")
            if meta.get("fingerprint") == fingerprint:
                rows = _read_motion(motion_path)
                cached_fps = float(meta["fps"])
                if not math.isfinite(cached_fps) or cached_fps <= 0:
                    raise ValueError("Invalid cached frame rate")
                if any(abs(row["t"] - row["idx"] / cached_fps) > 1e-6 for row in rows):
                    raise ValueError("Cached timestamps do not match the frame rate")
                if _attach_features(rows, features_path):
                    return rows, cached_fps, True
                # Old cache without features → fall through and reanalyze
        except (OSError, KeyError, TypeError, ValueError, csv.Error, json.JSONDecodeError):
            pass

    rows, fps = analyze_motion(str(input_path), cfg)
    if cache_enabled or cfg["io"].get("save_motion_csv", True):
        _write_motion(motion_path, rows)
    if cache_enabled:
        _write_features(features_path, rows)
        _atomic_write_text(
            meta_path,
            json.dumps({"fingerprint": fingerprint, "fps": fps}, indent=2),
        )
    return rows, fps, False


def _kind_durations(segs: list[dict]) -> dict[str, float]:
    result = {"room": 0.0, "move": 0.0, "fast": 0.0}
    for seg in segs:
        result.setdefault(seg["kind"], 0.0)
        result[seg["kind"]] += float(seg["t1"]) - float(seg["t0"])
    return {key: round(value, 6) for key, value in result.items()}


def run_pipeline(
    input_path: str | Path,
    output_path: str | Path | None = None,
    config_path: str | Path | None = "config.yaml",
    dry_run: bool = False,
    cfg: dict[str, Any] | None = None,
    edit_config_path: str | Path | None = None,
    reanalyze: bool = False,
    review_path: str | Path | bool | None = None,
) -> PipelineResult:
    """
    Analyze → classify → export variable-speed video.

    If config_path exists it is loaded; missing file falls back to defaults
    when config_path is None, otherwise raises.

    """
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input not found: {input_path}")
    check_tools()

    main_config_used: Path | None = None
    if cfg is None:
        if config_path is not None and Path(config_path).is_file():
            cfg = load_config(config_path)
            main_config_used = Path(config_path)
        elif config_path is None:
            cfg = load_config(None)
        else:
            if str(config_path) == "config.yaml":
                cfg = load_config(None)
            else:
                cfg = load_config(config_path)

    sidecar_used: Path | None = None
    if edit_config_path is not None:
        sidecar_used = Path(edit_config_path)
        cfg = merge_config_file(cfg, sidecar_used)
    else:
        candidate = input_path.with_name(f"{input_path.stem}.edit.yaml")
        if candidate.is_file():
            sidecar_used = candidate
            cfg = merge_config_file(cfg, candidate)

    io = cfg["io"]
    suffix = str(io["output_suffix"])
    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}{suffix}.mp4")
    else:
        output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different")

    work = _work_dir(input_path, str(io["work_dir"]))
    resolved_review: Path | None = None
    if review_path:
        resolved_review = work / "review.mp4" if review_path is True else Path(review_path)
        if resolved_review.resolve() in {input_path.resolve(), output_path.resolve()}:
            raise ValueError("Review path must differ from input and final output paths")
    work.mkdir(parents=True, exist_ok=True)

    print(f"Input : {input_path}")
    print(f"Config: {main_config_used or '(defaults)'}")
    if sidecar_used:
        print(f"Edit config: {sidecar_used}")
    print("Analyze motion...")
    rows, fps, cache_hit = _load_analysis(input_path, cfg, work, reanalyze)
    state = "cache hit" if cache_hit else "analyzed"
    print(f"  frames={len(rows)} fps={fps:.2f} ({state})")
    motion_csv = work / "motion.csv"
    if motion_csv.is_file():
        print(f"  motion -> {motion_csv}")

    duration = probe_duration(input_path)
    segs = build_segments(rows, duration, cfg)
    total_in, _ = _print_plan(segs)
    total_out_est = estimate_output_duration(segs, fps)

    warnings: list[str] = []
    # Many trim/concat nodes slow FFmpeg; soft warn only (no behavior change).
    if len(segs) > 80:
        msg = (
            f"segment count is high ({len(segs)}); export may be slow. "
            "Try raising segments.min_duration or simplifying pacing.room_hold_ramp."
        )
        warnings.append(msg)
        print(f"  warning: {msg}")

    if io.get("save_segments_json", True):
        seg_path = work / "segments.json"
        _atomic_write_text(seg_path, json.dumps(segs, indent=2, ensure_ascii=False))
        print(f"  segments -> {seg_path}")

    if resolved_review is not None:
        # --review always encodes a proxy, even alongside --dry-run (final
        # export is still skipped when dry_run is true).
        print(f"\nReview -> {resolved_review}")
        export_review(input_path, resolved_review, segs, cfg, work, fps=fps)

    summary_path = work / "summary.json"
    summary = {
        "status": "analyzed" if dry_run else "rendering",
        "input": {
            "path": str(input_path.resolve()),
            "size": input_path.stat().st_size,
            "fps": fps,
            "duration": duration,
        },
        "config": {
            "main": str(main_config_used.resolve()) if main_config_used else None,
            "edit": str(sidecar_used.resolve()) if sidecar_used else None,
        },
        "cache_hit": cache_hit,
        "segment_count": len(segs),
        "max_speed_limit": float(cfg.get("max_speed", 1.30)),
        "max_speed_used": max(float(segment["speed"]) for segment in segs),
        "kind_durations": _kind_durations(segs),
        "duration_out_est": total_out_est,
        "duration_out": None,
        "output_path": str(output_path.resolve()),
        "review_path": str(resolved_review.resolve()) if resolved_review else None,
        "work_dir": str(work.resolve()),
        "warnings": warnings,
        "deleted_intervals": [],
    }

    if dry_run:
        print("\n[dry-run] skip ffmpeg export")
        _atomic_write_text(summary_path, json.dumps(summary, indent=2, ensure_ascii=False))
        result = PipelineResult(
            input_path=input_path,
            output_path=output_path,
            segments=segs,
            duration_in=total_in,
            duration_out_est=total_out_est,
            duration_out=None,
            work_dir=work,
            dry_run=True,
            cache_hit=cache_hit,
            summary_path=summary_path,
            review_path=resolved_review,
            summary=summary,
        )
        return result

    filter_script = work / "filter_complex.txt"
    if not io.get("save_filter_script", True):
        filter_script = work / ".filter_complex.tmp.txt"

    print(f"\nExport -> {output_path}")
    src_size = input_path.stat().st_size
    _atomic_write_text(summary_path, json.dumps(summary, indent=2, ensure_ascii=False))
    try:
        out_dur = export_video(
            input_path,
            output_path,
            segs,
            cfg,
            filter_script=filter_script,
            log_path=work / "ffmpeg.log",
            fps=fps,
        )
    except Exception as exc:
        summary.update({"status": "failed", "error": str(exc)})
        _atomic_write_text(summary_path, json.dumps(summary, indent=2, ensure_ascii=False))
        raise
    finally:
        if not io.get("save_filter_script", True) and filter_script.exists():
            filter_script.unlink(missing_ok=True)

    out_size = output_path.stat().st_size if output_path.is_file() else 0
    tolerance = max(2.0 / fps, 0.05)
    if abs(out_dur - total_out_est) > tolerance:
        warnings.append(
            f"Output duration differs from estimate by {abs(out_dur-total_out_est):.3f}s"
        )
    output_media = probe_media(output_path)
    summary.update({
        "status": "complete",
        "duration_out": out_dur,
        "output_media": output_media,
        "warnings": warnings,
    })
    _atomic_write_text(summary_path, json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Done -> {output_path} ({out_dur:.2f}s)")
    if src_size and out_size:
        print(
            f"  size: {src_size/1e6:.2f} MB -> {out_size/1e6:.2f} MB "
            f"({out_size/src_size*100:.0f}% of source)"
        )
    return PipelineResult(
        input_path=input_path,
        output_path=output_path,
        segments=segs,
        duration_in=total_in,
        duration_out_est=total_out_est,
        duration_out=out_dur,
        work_dir=work,
        dry_run=False,
        cache_hit=cache_hit,
        summary_path=summary_path,
        review_path=resolved_review,
        summary=summary,
    )
