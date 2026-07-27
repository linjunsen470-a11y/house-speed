#!/usr/bin/env python3
"""Small leave-one-video-out grid search over cached analysis rows."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import itertools
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluate_segments import (  # noqa: E402
    KINDS,
    _classification_metrics,
    _load_json,
    _load_labels,
    evaluate_points,
    find_dataset_cases,
)
from walkthrough_edit.classify import build_segments  # noqa: E402
from walkthrough_edit.config import load_config  # noqa: E402
from walkthrough_edit.pipeline import _attach_features, _read_motion  # noqa: E402


DEFAULT_GRID = {
    "classify.dash_struct_max": [0.040, 0.048, 0.055],
    "classify.corridor_fast_motion": [13.0, 14.5, 16.0],
    "segments.min_fast_duration": [0.9, 1.2, 1.5],
    "segments.structured_fast_demote_edge_min": [0.055, 0.065, 0.080],
}


def _parse_param(raw: str) -> tuple[str, list[float]]:
    name, sep, values = raw.partition("=")
    if not sep or "." not in name:
        raise ValueError("--param must look like section.key=1,2,3")
    parsed = [float(value) for value in values.split(",") if value.strip()]
    if not parsed:
        raise ValueError(f"{name} has no values")
    return name.strip(), parsed


def _set(cfg: dict[str, Any], dotted: str, value: float) -> None:
    parts = dotted.split(".")
    target: Any = cfg
    for part in parts[:-1]:
        if not isinstance(target, dict) or part not in target:
            raise ValueError(f"unknown tunable config key: {dotted}")
        target = target[part]
    if not isinstance(target, dict) or parts[-1] not in target:
        raise ValueError(f"unknown tunable config key: {dotted}")
    target[parts[-1]] = value


def _aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    confusion = {truth: Counter() for truth in KINDS}
    for report in reports:
        for truth, row in report["confusion"].items():
            for predicted, value in row.items():
                confusion[truth][predicted] += float(value)
    return _classification_metrics(confusion)


def _utility(report: dict[str, Any], false_fast_penalty: float) -> float:
    return float(report["macro_f1"] or 0.0) - false_fast_penalty * float(
        report["room_to_fast_rate"] or 0.0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--labels-root", type=Path, default=ROOT / "eval" / "strict")
    parser.add_argument("--work-root", type=Path, default=ROOT / "frames")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Grid dimension such as classify.dash_struct_max=.04,.048",
    )
    parser.add_argument("--false-fast-penalty", type=float, default=0.5)
    parser.add_argument(
        "--mode",
        choices=("legacy", "candidate"),
        default=None,
        help="Override algorithm.mode for offline A/B tuning",
    )
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.mode is not None:
        cfg["algorithm"]["mode"] = args.mode
    grid = dict(DEFAULT_GRID)
    if args.param:
        grid = dict(_parse_param(raw) for raw in args.param)
    cases = find_dataset_cases(args.labels_root, args.work_root)
    if len(cases) < 2:
        parser.error("leave-one-video-out tuning needs at least two matched videos")

    prepared: dict[str, tuple[list[dict], float, list[dict]]] = {}
    for stem, segments_path, labels_path in cases:
        work = segments_path.parent
        rows = _read_motion(work / "motion.csv")
        _attach_features(rows, work / "analysis_features.npz")
        duration = float(_load_json(segments_path)[-1]["t1"])
        prepared[stem] = (rows, duration, _load_labels(labels_path))

    def evaluate_config(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
        reports: dict[str, dict[str, Any]] = {}
        for stem, (rows, duration, labels) in prepared.items():
            segments = build_segments(rows, duration, candidate)
            reports[stem] = evaluate_points(segments, labels)
        return reports

    baseline_reports = evaluate_config(cfg)
    baseline = _aggregate(list(baseline_reports.values()))
    names = list(grid)
    trials: list[dict[str, Any]] = []
    for values in itertools.product(*(grid[name] for name in names)):
        candidate = deepcopy(cfg)
        params = dict(zip(names, values))
        for name, value in params.items():
            _set(candidate, name, value)
        reports = evaluate_config(candidate)
        aggregate = _aggregate(list(reports.values()))
        trials.append(
            {
                "params": params,
                "metrics": aggregate,
                "utility": _utility(aggregate, args.false_fast_penalty),
                "per_video": reports,
            }
        )
    trials.sort(key=lambda trial: trial["utility"], reverse=True)

    held_out_reports: list[dict[str, Any]] = []
    selected_by_video: dict[str, dict[str, float]] = {}
    for held_out in prepared:
        def training_score(trial: dict[str, Any]) -> float:
            training = _aggregate(
                [
                    report
                    for stem, report in trial["per_video"].items()
                    if stem != held_out
                ]
            )
            return _utility(training, args.false_fast_penalty)

        selected = max(trials, key=training_score)
        held_out_reports.append(selected["per_video"][held_out])
        selected_by_video[held_out] = selected["params"]

    result = {
        "videos": list(prepared),
        "objective": "macro_f1 - penalty * room_to_fast_rate",
        "false_fast_penalty": args.false_fast_penalty,
        "baseline": baseline,
        "best_all_data": {
            "params": trials[0]["params"],
            "metrics": trials[0]["metrics"],
        },
        "leave_one_video_out": {
            "metrics": _aggregate(held_out_reports),
            "selected_params": selected_by_video,
        },
        "top_candidates": [
            {"params": trial["params"], "metrics": trial["metrics"]}
            for trial in trials[: max(1, args.top)]
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
