import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from evaluate_segments import evaluate_points
from walkthrough_edit.analyze import frame_entropy
from walkthrough_edit.classify import classify_frames_candidate
from walkthrough_edit.config import load_config
from walkthrough_edit.pipeline import (
    _attach_features,
    _write_features,
)


def _feature_row(index: int) -> dict:
    return {
        "idx": index,
        "t": index / 10,
        "mean": 80.0,
        "std": 20.0,
        "edge": 0.05,
        "motion": 5.0,
        "appearance": np.linspace(0, 1, 20, dtype=np.float32),
        "flow_dx": 1.0,
        "flow_dy": -0.5,
        "flow_response": 0.8,
        "flow_magnitude": 1.1,
        "flow_direction_consistency": 0.9,
        "entropy": 3.0,
        "sharpness": 100.0,
        "information_score": 0.5,
        "camera_motion_score": 0.4,
    }


class CacheReliabilityTests(unittest.TestCase):
    def test_feature_cache_roundtrip_and_malformed_length_falls_back(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "features.npz"
            rows = [_feature_row(0), _feature_row(1)]
            _write_features(path, rows)
            target = [
                {key: row[key] for key in ("idx", "t", "mean", "std", "edge", "motion")}
                for row in rows
            ]
            self.assertTrue(_attach_features(target, path))
            self.assertAlmostEqual(target[1]["information_score"], 0.5)

            np.savez_compressed(
                path,
                appearance=np.zeros((2, 20), dtype=np.float32),
                flow_dx=np.zeros(1, dtype=np.float32),
                flow_dy=np.zeros(2, dtype=np.float32),
            )
            self.assertFalse(_attach_features(target, path))


class ConfigWarningTests(unittest.TestCase):
    def test_unknown_config_key_warns_with_suggestion(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("speads:\n  room: 1.5\n", encoding="utf-8")
            with self.assertWarnsRegex(UserWarning, "did you mean 'speeds'"):
                load_config(path)


class EvaluationAndCandidateTests(unittest.TestCase):
    def test_point_evaluation_clamps_rounded_tail_and_checks_speed_range(self):
        segments = [
            {"t0": 0.0, "t1": 1.0, "kind": "room", "speed": 1.35},
            {"t0": 1.0, "t1": 2.0, "kind": "fast", "speed": 3.5},
        ]
        labels = [
            {
                "t": 0.5,
                "vis_kind": "room",
                "acceptable_speed_min": 1.0,
                "acceptable_speed_max": 1.5,
            },
            {
                "t": 2.01,
                "vis_kind": "fast",
                "acceptable_speed_min": 3.0,
                "acceptable_speed_max": 4.0,
            },
        ]
        report = evaluate_points(segments, labels)
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["speed_acceptance_rate"], 1.0)
        self.assertIn("macro_f1", report)
        self.assertIn("speed_transitions_per_minute", report)

    def test_candidate_hysteresis_suppresses_one_frame_flip(self):
        cfg = load_config(None)
        cfg["algorithm"]["mode"] = "candidate"
        rows = []
        for index, info in enumerate([0.8, 0.8, 0.1, 0.8, 0.8]):
            rows.append(
                {
                    "t": index * 0.1,
                    "edge": 0.1,
                    "motion": 2.0,
                    "information_score": info,
                    "camera_motion_score": 0.1,
                    "flow_direction_consistency": 1.0,
                }
            )
        self.assertEqual(classify_frames_candidate(rows, cfg), ["room"] * len(rows))

    def test_entropy_separates_flat_and_textured_frames(self):
        flat = np.zeros((32, 32), dtype=np.uint8)
        textured = np.tile(np.arange(32, dtype=np.uint8) * 8, (32, 1))
        self.assertGreater(frame_entropy(textured), frame_entropy(flat))


if __name__ == "__main__":
    unittest.main()
