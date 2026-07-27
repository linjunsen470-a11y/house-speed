"""End-to-end walkthrough edit pipeline."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
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


def _source_fingerprint(input_path: Path) -> dict[str, Any]:
    stat = input_path.stat()
    return {
        "path": str(input_path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
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


def _stage_a_config_digest(cfg: dict[str, Any]) -> str:
    """Hash only settings that affect analysis, segmentation, or raw rendering."""
    keys = (
        "algorithm",
        "analysis",
        "classify",
        "speeds",
        "segments",
        "scene_aba",
        "pacing",
        "overrides",
        "encode",
    )
    payload = {key: cfg.get(key) for key in keys}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _raw_provenance_path(work: Path) -> Path:
    return work / "speed_raw.meta.json"


def _write_raw_provenance(
    path: Path,
    input_path: Path,
    raw_path: Path,
    cfg: dict[str, Any],
) -> None:
    payload = {
        "version": 1,
        "source": _source_fingerprint(input_path),
        "stage_a_config_sha256": _stage_a_config_digest(cfg),
        "raw": {
            "path": str(raw_path.resolve()),
            "size": raw_path.stat().st_size,
            "mtime_ns": raw_path.stat().st_mtime_ns,
        },
    }
    _atomic_write_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False),
    )


def _validate_pack_only_source(
    input_path: Path,
    raw_path: Path,
    work: Path,
    cfg: dict[str, Any],
) -> list[str]:
    """Reject a raw intermediate known to belong to another source revision."""
    warnings_out: list[str] = []
    current_source = _source_fingerprint(input_path)
    provenance_path = _raw_provenance_path(work)
    if provenance_path.is_file():
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            if provenance.get("source") != current_source:
                raise ValueError(
                    "pack-only intermediate is stale: the source video changed; "
                    "run once without --pack-only"
                )
            raw_meta = provenance.get("raw") or {}
            if int(raw_meta.get("size") or -1) != raw_path.stat().st_size:
                raise ValueError(
                    "pack-only intermediate does not match its provenance; "
                    "run once without --pack-only"
                )
            if provenance.get("stage_a_config_sha256") != _stage_a_config_digest(cfg):
                warnings_out.append(
                    "Stage-A config changed since speed_raw.mp4 was rendered; "
                    "reusing it because --pack-only explicitly requests reuse"
                )
            return warnings_out
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Invalid pack-only provenance {provenance_path}: {exc}"
            ) from exc

    # Backward compatibility for intermediates produced before provenance.
    cache_meta = work / "analysis_cache.json"
    if cache_meta.is_file():
        try:
            cached = json.loads(cache_meta.read_text(encoding="utf-8"))
            old_source = cached.get("fingerprint") or {}
            comparable = {key: old_source.get(key) for key in current_source}
            if comparable != current_source:
                raise ValueError(
                    "legacy pack-only intermediate is stale: the source video changed; "
                    "run once without --pack-only"
                )
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid legacy analysis cache {cache_meta}: {exc}") from exc
    warnings_out.append(
        "speed_raw.mp4 has no provenance metadata; source identity was checked "
        "with the legacy cache where possible"
    )
    return warnings_out


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
    except (OSError, KeyError, ValueError):
        return False
    n = len(rows)
    if appearance.ndim != 2 or appearance.shape[0] != n:
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
            if meta.get("fingerprint") == fingerprint:
                rows = _read_motion(motion_path)
                if _attach_features(rows, features_path):
                    return rows, float(meta["fps"]), True
                # Old cache without features → fall through and reanalyze
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
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
        warnings = _validate_pack_only_source(input_path, raw_path, work, cfg)
        for warning in warnings:
            print(f"  warning: {warning}")
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
    if pack_enabled:
        _write_raw_provenance(
            _raw_provenance_path(work),
            input_path,
            export_target,
            cfg,
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
