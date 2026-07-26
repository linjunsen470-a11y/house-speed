"""End-to-end walkthrough edit pipeline."""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyze import analyze_motion
from .classify import build_segments
from .config import load_config, merge_config_file
from .pack import pack_video
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
    print("=== Segment plan (no frames discarded) ===")
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


def _analysis_fingerprint(input_path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    stat = input_path.stat()
    return {
        "path": str(input_path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "analysis": cfg["analysis"],
        "cache_version": 1,
    }


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
    return rows


def _write_motion(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file, fieldnames=["idx", "t", "mean", "std", "edge", "motion"]
        )
        writer.writeheader()
        writer.writerows(rows)


def _load_analysis(
    input_path: Path,
    cfg: dict[str, Any],
    work: Path,
    reanalyze: bool,
) -> tuple[list[dict], float, bool]:
    motion_path = work / "motion.csv"
    meta_path = work / "analysis_cache.json"
    fingerprint = _analysis_fingerprint(input_path, cfg)
    cache_enabled = bool((cfg.get("cache") or {}).get("enabled", True))
    if cache_enabled and not reanalyze and motion_path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("fingerprint") == fingerprint:
                return _read_motion(motion_path), float(meta["fps"]), True
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass

    rows, fps = analyze_motion(str(input_path), cfg)
    if cache_enabled or cfg["io"].get("save_motion_csv", True):
        _write_motion(motion_path, rows)
    if cache_enabled:
        meta_path.write_text(
            json.dumps({"fingerprint": fingerprint, "fps": fps}, indent=2),
            encoding="utf-8",
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
    pack: bool | None = None,
    pack_only: bool = False,
) -> PipelineResult:
    """
    Analyze → classify → (optional) export → (optional) pack overlay.

    If config_path exists it is loaded; missing file falls back to defaults
    when config_path is None, otherwise raises.

    pack=True/False overrides config pack.enabled.
    pack_only=True reuses work/speed_raw.mp4 and only runs packaging.
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

    if pack is not None:
        cfg.setdefault("pack", {})["enabled"] = bool(pack)
    pack_enabled = bool((cfg.get("pack") or {}).get("enabled", False)) or pack_only

    io = cfg["io"]
    suffix = str(io["output_suffix"])
    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}{suffix}.mp4")
    else:
        output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different")

    work = _work_dir(input_path, str(io["work_dir"]))
    work.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parent.parent
    raw_path = work / "speed_raw.mp4"

    print(f"Input : {input_path}")
    print(f"Config: {main_config_used or '(defaults)'}")
    if sidecar_used:
        print(f"Edit config: {sidecar_used}")
    if pack_enabled:
        print(f"Pack  : enabled (style={(cfg.get('pack') or {}).get('style', 'bar_dark')})")

    # --- pack-only: skip analyze/export if intermediate exists ---
    if pack_only:
        if not raw_path.is_file():
            raise FileNotFoundError(
                f"pack-only requires existing intermediate: {raw_path}. "
                "Run once without --pack-only first."
            )
        segs: list[dict] = []
        seg_file = work / "segments.json"
        if seg_file.is_file():
            segs = json.loads(seg_file.read_text(encoding="utf-8"))
        fps = 30.0
        cache_meta = work / "analysis_cache.json"
        if cache_meta.is_file():
            try:
                fps = float(json.loads(cache_meta.read_text(encoding="utf-8")).get("fps") or 30)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        total_in = sum(float(s["t1"]) - float(s["t0"]) for s in segs) if segs else 0.0
        total_out_est = estimate_output_duration(segs, fps) if segs else probe_duration(raw_path)
        warnings: list[str] = []
        print(f"\nPack-only -> {output_path}")
        out_dur = pack_video(
            raw_path, output_path, cfg, work, project_root=project_root
        )
        summary_path = work / "summary.json"
        summary: dict[str, Any] = {
            "status": "complete",
            "pack_only": True,
            "duration_out": out_dur,
            "output_path": str(output_path.resolve()),
            "work_dir": str(work.resolve()),
            "warnings": warnings,
        }
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Done -> {output_path} ({out_dur:.2f}s)")
        return PipelineResult(
            input_path=input_path,
            output_path=output_path,
            segments=segs,
            duration_in=total_in,
            duration_out_est=total_out_est,
            duration_out=out_dur,
            work_dir=work,
            dry_run=False,
            cache_hit=True,
            summary_path=summary_path,
            review_path=None,
            summary=summary,
        )

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
        seg_path.write_text(
            json.dumps(segs, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  segments -> {seg_path}")

    resolved_review: Path | None = None
    if review_path:
        # --review always encodes a proxy, even alongside --dry-run (final
        # export is still skipped when dry_run is true).
        resolved_review = (
            work / "review.mp4" if review_path is True else Path(review_path)
        )
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
        "kind_durations": _kind_durations(segs),
        "duration_out_est": total_out_est,
        "duration_out": None,
        "output_path": str(output_path.resolve()),
        "review_path": str(resolved_review.resolve()) if resolved_review else None,
        "work_dir": str(work.resolve()),
        "pack_enabled": pack_enabled,
        "warnings": warnings,
        "deleted_intervals": [],
    }

    if dry_run:
        print("\n[dry-run] skip ffmpeg export")
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
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

    export_target = raw_path if pack_enabled else output_path
    print(f"\nExport -> {export_target}")
    src_size = input_path.stat().st_size
    out_dur = export_video(
        input_path,
        export_target,
        segs,
        cfg,
        filter_script=filter_script,
        log_path=work / "ffmpeg.log",
        fps=fps,
    )
    if not io.get("save_filter_script", True) and filter_script.exists():
        filter_script.unlink(missing_ok=True)

    if pack_enabled:
        print(f"\nPack -> {output_path}")
        out_dur = pack_video(
            export_target, output_path, cfg, work, project_root=project_root
        )

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
        "pack_enabled": pack_enabled,
    })
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
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
