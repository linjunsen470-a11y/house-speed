"""End-to-end walkthrough edit pipeline."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyze import analyze_motion
from .classify import build_segments
from .config import load_config
from .render import export_video, probe_duration


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


def run_pipeline(
    input_path: str | Path,
    output_path: str | Path | None = None,
    config_path: str | Path | None = "config.yaml",
    dry_run: bool = False,
    cfg: dict[str, Any] | None = None,
) -> PipelineResult:
    """
    Analyze → classify → (optional) export.

    If config_path exists it is loaded; missing file falls back to defaults
    when config_path is None, otherwise raises.
    """
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input not found: {input_path}")

    if cfg is None:
        if config_path is not None and Path(config_path).is_file():
            cfg = load_config(config_path)
        elif config_path is None:
            cfg = load_config(None)
        else:
            # try defaults if default name missing
            if str(config_path) == "config.yaml":
                cfg = load_config(None)
            else:
                cfg = load_config(config_path)

    io = cfg["io"]
    suffix = str(io["output_suffix"])
    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}{suffix}.mp4")
    else:
        output_path = Path(output_path)

    work = Path(str(io["work_dir"])) / input_path.stem
    work.mkdir(parents=True, exist_ok=True)

    print(f"Input : {input_path}")
    print(f"Config: {config_path or '(defaults)'}")
    print(f"Analyze motion...")
    rows, fps = analyze_motion(str(input_path), cfg)
    print(f"  frames={len(rows)} fps={fps:.2f}")

    if io.get("save_motion_csv", True):
        motion_csv = work / "motion.csv"
        with open(motion_csv, "w", newline="", encoding="utf-8") as f:
            wri = csv.DictWriter(
                f, fieldnames=["idx", "t", "mean", "std", "edge", "motion"]
            )
            wri.writeheader()
            wri.writerows(rows)
        print(f"  motion -> {motion_csv}")

    duration = probe_duration(input_path)
    segs = build_segments(rows, duration, cfg)
    total_in, total_out_est = _print_plan(segs)

    if io.get("save_segments_json", True):
        seg_path = work / "segments.json"
        seg_path.write_text(
            json.dumps(segs, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  segments -> {seg_path}")

    if dry_run:
        print("\n[dry-run] skip ffmpeg export")
        return PipelineResult(
            input_path=input_path,
            output_path=output_path,
            segments=segs,
            duration_in=total_in,
            duration_out_est=total_out_est,
            duration_out=None,
            work_dir=work,
            dry_run=True,
        )

    filter_script = work / "filter_complex.txt"
    if not io.get("save_filter_script", True):
        filter_script = work / ".filter_complex.tmp.txt"

    print(f"\nExport -> {output_path}")
    out_dur = export_video(
        input_path,
        output_path,
        segs,
        cfg,
        filter_script=filter_script,
    )
    if not io.get("save_filter_script", True) and filter_script.exists():
        filter_script.unlink(missing_ok=True)

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
    )
