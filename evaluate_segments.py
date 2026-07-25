#!/usr/bin/env python3
"""Evaluate an automatic segment plan against lightweight hand labels."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import yaml


KINDS = {"room", "move", "fast"}


def _load_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("segments JSON must contain a list")
    return data


def _load_labels(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = data.get("labels") or []
    if not isinstance(data, list):
        raise ValueError("label YAML must be a list or contain a labels list")
    labels: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not {"start", "end", "kind"} <= item.keys():
            raise ValueError(f"label {index} needs start, end and kind")
        start, end, kind = float(item["start"]), float(item["end"]), str(item["kind"])
        if start < 0 or end <= start or kind not in KINDS:
            raise ValueError(f"label {index} is invalid")
        labels.append({"start": start, "end": end, "kind": kind})
    return labels


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _validate_segments(segments: list[dict]) -> list[dict]:
    """Normalize and validate auto segment dicts before scoring."""
    if not isinstance(segments, list):
        raise ValueError("segments must be a list")
    cleaned: list[dict] = []
    for index, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"segment {index} must be an object")
        missing = {"t0", "t1", "kind", "speed"} - seg.keys()
        if missing:
            raise ValueError(
                f"segment {index} missing fields: {sorted(missing)}"
            )
        t0, t1 = float(seg["t0"]), float(seg["t1"])
        kind = str(seg["kind"])
        speed = float(seg["speed"])
        if t1 <= t0:
            raise ValueError(f"segment {index} has non-positive duration")
        if kind not in KINDS:
            raise ValueError(
                f"segment {index} kind {kind!r} not in {sorted(KINDS)}"
            )
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError(f"segment {index} speed must be a finite number > 0")
        cleaned.append({"t0": t0, "t1": t1, "kind": kind, "speed": speed})
    return cleaned


def evaluate(segments: list[dict], labels: list[dict]) -> dict[str, Any]:
    segments = _validate_segments(segments)
    confusion = {truth: {pred: 0.0 for pred in KINDS} for truth in KINDS}
    total_labeled = 0.0
    correct = 0.0
    false_acceleration = 0.0
    correction_intervals = 0

    for label in labels:
        truth = label["kind"]
        label_duration = label["end"] - label["start"]
        total_labeled += label_duration
        interval_correct = 0.0
        for seg in segments:
            seconds = _overlap(
                label["start"], label["end"],
                float(seg["t0"]), float(seg["t1"]),
            )
            if seconds <= 0:
                continue
            predicted = str(seg["kind"])
            confusion[truth][predicted] += seconds
            if predicted == truth:
                correct += seconds
                interval_correct += seconds
            if truth == "room" and predicted != "room":
                false_acceleration += seconds
        if interval_correct < label_duration - 1e-6:
            correction_intervals += 1

    # Covered source duration (works for partial/gapped segment lists too)
    duration_in = sum(float(s["t1"]) - float(s["t0"]) for s in segments)
    duration_out = sum(
        (float(s["t1"]) - float(s["t0"])) / float(s["speed"])
        for s in segments
    )
    rounded_confusion = {
        truth: {pred: round(seconds, 3) for pred, seconds in row.items()}
        for truth, row in confusion.items()
    }
    return {
        "labeled_seconds": round(total_labeled, 3),
        "accuracy": round(correct / total_labeled, 4) if total_labeled else None,
        "false_acceleration_seconds": round(false_acceleration, 3),
        "correction_intervals": correction_intervals,
        "compression_ratio": round(duration_out / duration_in, 4) if duration_in else None,
        "confusion_seconds": rounded_confusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segments", help="Generated segments.json")
    parser.add_argument("labels", help="Hand-labeled YAML time ranges")
    parser.add_argument("--json", action="store_true", help="Emit compact JSON")
    args = parser.parse_args()
    try:
        report = evaluate(
            _load_json(Path(args.segments)),
            _load_labels(Path(args.labels)),
        )
    except Exception as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
