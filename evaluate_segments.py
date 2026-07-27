#!/usr/bin/env python3
"""Evaluate one segment plan or a directory of point-labeled videos."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from typing import Any

import yaml


KINDS = {"room", "move", "fast"}
CONTENT_VALUES = {"showcase", "transition", "low_info"}
CAMERA_MOTIONS = {"static", "pan", "walk", "whip"}


def _load_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("segments JSON must contain a list")
    return data


def _load_labels(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        data = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if isinstance(data, dict):
            data = data.get("labels") or []
    if not isinstance(data, list):
        raise ValueError("labels must be a list, JSONL, or contain a labels list")
    return data


def _kind(item: dict[str, Any], index: int) -> str:
    kind = str(item.get("kind") or item.get("vis_kind") or "")
    if kind not in KINDS:
        raise ValueError(f"label {index} kind must be one of {sorted(KINDS)}")
    return kind


def _validate_label_metadata(item: dict[str, Any], index: int) -> None:
    content = item.get("content_value")
    if content is not None and str(content) not in CONTENT_VALUES:
        raise ValueError(
            f"label {index} content_value must be one of {sorted(CONTENT_VALUES)}"
        )
    camera = item.get("camera_motion")
    if camera is not None and str(camera) not in CAMERA_MOTIONS:
        raise ValueError(
            f"label {index} camera_motion must be one of {sorted(CAMERA_MOTIONS)}"
        )
    lo, hi = item.get("acceptable_speed_min"), item.get("acceptable_speed_max")
    if lo is not None and float(lo) <= 0:
        raise ValueError(f"label {index} acceptable_speed_min must be > 0")
    if hi is not None and float(hi) <= 0:
        raise ValueError(f"label {index} acceptable_speed_max must be > 0")
    if lo is not None and hi is not None and float(hi) < float(lo):
        raise ValueError(
            f"label {index} acceptable_speed_max must be >= acceptable_speed_min"
        )


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _validate_segments(segments: list[dict]) -> list[dict]:
    if not isinstance(segments, list):
        raise ValueError("segments must be a list")
    cleaned: list[dict] = []
    previous: float | None = None
    for index, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"segment {index} must be an object")
        missing = {"t0", "t1", "kind", "speed"} - seg.keys()
        if missing:
            raise ValueError(f"segment {index} missing fields: {sorted(missing)}")
        t0, t1 = float(seg["t0"]), float(seg["t1"])
        kind, speed = str(seg["kind"]), float(seg["speed"])
        if t1 <= t0:
            raise ValueError(f"segment {index} has non-positive duration")
        if previous is not None and abs(t0 - previous) > 1e-5:
            raise ValueError(f"segment {index} has a gap or overlap")
        if kind not in KINDS:
            raise ValueError(f"segment {index} kind {kind!r} not in {sorted(KINDS)}")
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError(f"segment {index} speed must be a finite number > 0")
        cleaned.append({"t0": t0, "t1": t1, "kind": kind, "speed": speed})
        previous = t1
    return cleaned


def _segment_at(segments: list[dict], t: float) -> dict:
    if not segments:
        raise ValueError("segment timeline is empty")
    duration = float(segments[-1]["t1"])
    # Hand labels commonly round the last timestamp a few milliseconds upward.
    t = min(max(float(t), float(segments[0]["t0"])), max(0.0, duration - 1e-9))
    for seg in segments:
        if float(seg["t0"]) - 1e-9 <= t < float(seg["t1"]) + 1e-9:
            return seg
    raise ValueError(f"timestamp {t:.6f} is not covered by the segment timeline")


def _classification_metrics(
    confusion: dict[str, Counter[str]],
) -> dict[str, Any]:
    per_class: dict[str, dict[str, float]] = {}
    correct = total = 0.0
    for truth in KINDS:
        correct += float(confusion[truth][truth])
        total += float(sum(confusion[truth].values()))
    for kind in sorted(KINDS):
        tp = float(confusion[kind][kind])
        fp = sum(float(confusion[truth][kind]) for truth in KINDS if truth != kind)
        fn = sum(float(value) for pred, value in confusion[kind].items() if pred != kind)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[kind] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": round(tp + fn, 3),
        }
    room_support = float(sum(confusion["room"].values()))
    room_to_fast = float(confusion["room"]["fast"])
    return {
        "accuracy": round(correct / total, 4) if total else None,
        "macro_f1": round(
            sum(item["f1"] for item in per_class.values()) / len(per_class), 4
        ),
        "per_class": per_class,
        "room_to_fast": round(room_to_fast, 3),
        "room_to_fast_rate": round(room_to_fast / room_support, 4)
        if room_support
        else None,
        "confusion": {
            truth: {
                pred: round(float(confusion[truth][pred]), 3)
                for pred in sorted(KINDS)
            }
            for truth in sorted(KINDS)
        },
    }


def _timeline_metrics(segments: list[dict], short_island_sec: float = 0.75) -> dict[str, Any]:
    duration_in = sum(float(s["t1"]) - float(s["t0"]) for s in segments)
    duration_out = sum(
        (float(s["t1"]) - float(s["t0"])) / float(s["speed"])
        for s in segments
    )
    transitions = sum(
        abs(float(a["speed"]) - float(b["speed"])) > 1e-6
        for a, b in zip(segments, segments[1:])
    )
    short_islands = sum(
        float(seg["t1"]) - float(seg["t0"]) < short_island_sec
        and 0 < index < len(segments) - 1
        and abs(float(seg["speed"]) - float(segments[index - 1]["speed"])) > 1e-6
        and abs(float(seg["speed"]) - float(segments[index + 1]["speed"])) > 1e-6
        for index, seg in enumerate(segments)
    )
    minutes = duration_in / 60.0
    return {
        "duration_in": round(duration_in, 4),
        "duration_out": round(duration_out, 4),
        "compression_ratio": round(duration_out / duration_in, 4)
        if duration_in
        else None,
        "speed_transitions": transitions,
        "speed_transitions_per_minute": round(transitions / minutes, 3)
        if minutes
        else 0.0,
        "short_speed_islands": short_islands,
    }


def evaluate(segments: list[dict], labels: list[dict]) -> dict[str, Any]:
    """Backward-compatible interval evaluation with richer class metrics."""
    segments = _validate_segments(segments)
    confusion = {truth: Counter() for truth in KINDS}
    total_labeled = correct = false_acceleration = 0.0
    correction_intervals = 0
    accepted = accepted_total = 0.0
    for index, label in enumerate(labels):
        if not isinstance(label, dict) or not {"start", "end"} <= label.keys():
            raise ValueError(f"label {index} needs start, end and kind")
        start, end, truth = float(label["start"]), float(label["end"]), _kind(label, index)
        _validate_label_metadata(label, index)
        if start < 0 or end <= start:
            raise ValueError(f"label {index} is invalid")
        label_duration = end - start
        total_labeled += label_duration
        interval_correct = 0.0
        for seg in segments:
            seconds = _overlap(start, end, float(seg["t0"]), float(seg["t1"]))
            if seconds <= 0:
                continue
            predicted = str(seg["kind"])
            confusion[truth][predicted] += seconds
            if predicted == truth:
                correct += seconds
                interval_correct += seconds
            if truth == "room" and predicted != "room":
                false_acceleration += seconds
            lo = label.get("acceptable_speed_min")
            hi = label.get("acceptable_speed_max")
            if lo is not None or hi is not None:
                accepted_total += seconds
                speed = float(seg["speed"])
                if speed >= float(lo if lo is not None else 0.0) and speed <= float(
                    hi if hi is not None else math.inf
                ):
                    accepted += seconds
        if interval_correct < label_duration - 1e-6:
            correction_intervals += 1
    result = {
        "labeled_seconds": round(total_labeled, 3),
        "accuracy": round(correct / total_labeled, 4) if total_labeled else None,
        "false_acceleration_seconds": round(false_acceleration, 3),
        "correction_intervals": correction_intervals,
        "speed_acceptance_rate": round(accepted / accepted_total, 4)
        if accepted_total
        else None,
    }
    result.update(_timeline_metrics(segments))
    result.update(_classification_metrics(confusion))
    return result


def evaluate_points(segments: list[dict], labels: list[dict]) -> dict[str, Any]:
    """Evaluate JSONL/contact-sheet point labels, including acceptable speeds."""
    segments = _validate_segments(segments)
    confusion = {truth: Counter() for truth in KINDS}
    accepted = accepted_total = weighted_correct = weight_total = 0.0
    for index, label in enumerate(labels):
        if not isinstance(label, dict) or "t" not in label:
            raise ValueError(f"point label {index} needs t and kind/vis_kind")
        truth = _kind(label, index)
        _validate_label_metadata(label, index)
        seg = _segment_at(segments, float(label["t"]))
        predicted = str(seg["kind"])
        confusion[truth][predicted] += 1
        weight = float(label.get("confidence", label.get("vis_confidence", 1.0)))
        weight_total += weight
        if predicted == truth:
            weighted_correct += weight
        lo = label.get("acceptable_speed_min")
        hi = label.get("acceptable_speed_max")
        if lo is not None or hi is not None:
            accepted_total += weight
            speed = float(seg["speed"])
            if speed >= float(lo if lo is not None else 0.0) and speed <= float(
                hi if hi is not None else math.inf
            ):
                accepted += weight
    result = _classification_metrics(confusion)
    result.update(_timeline_metrics(segments))
    result.update(
        {
            "labeled_points": len(labels),
            "confidence_weighted_accuracy": round(weighted_correct / weight_total, 4)
            if weight_total
            else None,
            "speed_acceptance_rate": round(accepted / accepted_total, 4)
            if accepted_total
            else None,
        }
    )
    return result


def find_dataset_cases(
    labels_root: Path,
    work_root: Path,
) -> list[tuple[str, Path, Path]]:
    """Return ``(video stem, segments path, labels path)`` for a local dataset."""
    label_paths = sorted(labels_root.glob("*/labels.jsonl"))
    summaries: list[tuple[Path, dict[str, Any]]] = []
    for summary_path in work_root.glob("*/summary.json"):
        try:
            summaries.append(
                (summary_path, json.loads(summary_path.read_text(encoding="utf-8")))
            )
        except (OSError, TypeError, json.JSONDecodeError):
            continue
    cases: list[tuple[str, Path, Path]] = []
    input_root = work_root.resolve().parent
    for labels_path in label_paths:
        stem = labels_path.parent.name
        candidates: list[Path] = []
        for summary_path, summary in summaries:
            input_path = Path(str((summary.get("input") or {}).get("path") or ""))
            if input_path.stem == stem and input_path.parent.resolve() == input_root:
                segment_path = summary_path.parent / "segments.json"
                if segment_path.is_file():
                    candidates.append(segment_path)
        if candidates:
            selected = max(candidates, key=lambda path: path.stat().st_mtime_ns)
            cases.append((stem, selected, labels_path))
    return cases


def evaluate_dataset(labels_root: Path, work_root: Path) -> dict[str, Any]:
    cases = find_dataset_cases(labels_root, work_root)
    if not cases:
        raise ValueError("No matching labels.jsonl and segments.json files found")
    per_video: dict[str, dict[str, Any]] = {}
    aggregate_confusion = {truth: Counter() for truth in KINDS}
    for stem, segments_path, labels_path in cases:
        segments = _load_json(segments_path)
        labels = _load_labels(labels_path)
        report = evaluate_points(segments, labels)
        per_video[stem] = report
        for truth, row in report["confusion"].items():
            for predicted, value in row.items():
                aggregate_confusion[truth][predicted] += float(value)
    overall = _classification_metrics(aggregate_confusion)
    overall["videos"] = len(per_video)
    overall["labeled_points"] = sum(
        int(report["labeled_points"]) for report in per_video.values()
    )
    return {"overall": overall, "per_video": per_video}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segments", nargs="?", help="Generated segments.json")
    parser.add_argument("labels", nargs="?", help="Hand labels YAML or JSONL")
    parser.add_argument("--dataset", type=Path, help="Root containing */labels.jsonl")
    parser.add_argument("--work-root", type=Path, default=Path("frames"))
    parser.add_argument("--json", action="store_true", help="Emit compact JSON")
    args = parser.parse_args()
    try:
        if args.dataset:
            report = evaluate_dataset(args.dataset, args.work_root)
        elif args.segments and args.labels:
            segments = _load_json(Path(args.segments))
            labels = _load_labels(Path(args.labels))
            report = (
                evaluate_points(segments, labels)
                if labels and "t" in labels[0]
                else evaluate(segments, labels)
            )
        else:
            parser.error("provide SEGMENTS LABELS or --dataset LABEL_ROOT")
    except Exception as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.json else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
