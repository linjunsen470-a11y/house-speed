import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from evaluate_segments import evaluate_points
from scripts.bootstrap_assets import fetch_bgm_library
from walkthrough_edit.analyze import frame_entropy
from walkthrough_edit.classify import classify_frames_candidate
from walkthrough_edit.config import load_config
from walkthrough_edit.pack import pack_video
from walkthrough_edit.pipeline import (
    _attach_features,
    _validate_pack_only_source,
    _write_features,
    _write_raw_provenance,
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

    def test_pack_only_rejects_changed_source_and_warns_on_stage_config(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = root / "source.mp4"
            raw = root / "speed_raw.mp4"
            source.write_bytes(b"source-v1")
            raw.write_bytes(b"raw")
            cfg = load_config(None)
            meta = root / "speed_raw.meta.json"
            _write_raw_provenance(meta, source, raw, cfg)
            self.assertEqual(_validate_pack_only_source(source, raw, root, cfg), [])

            changed_cfg = load_config(None)
            changed_cfg["speeds"]["room"] = 1.5
            warnings = _validate_pack_only_source(source, raw, root, changed_cfg)
            self.assertTrue(any("Stage-A config changed" in item for item in warnings))

            source.write_bytes(b"source-v2")
            with self.assertRaisesRegex(ValueError, "source video changed"):
                _validate_pack_only_source(source, raw, root, cfg)


class ConfigAndBootstrapTests(unittest.TestCase):
    def test_unknown_config_key_warns_with_suggestion(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("speads:\n  room: 1.5\n", encoding="utf-8")
            with self.assertWarnsRegex(UserWarning, "did you mean 'speeds'"):
                load_config(path)

    @patch("scripts.bootstrap_assets.subprocess.run")
    def test_bgm_bootstrap_propagates_child_failure(self, run):
        fetch_bgm_library()
        self.assertTrue(run.call_args.kwargs["check"])


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


class PackDirectoryTests(unittest.TestCase):
    def test_pack_creates_output_parent_before_ffmpeg(self):
        cfg = load_config(None)
        cfg["pack"]["sticker"]["enabled"] = False
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"x")
            output = root / "new" / "nested" / "out.mp4"
            bgm = root / "bgm.mp3"
            bgm.write_bytes(b"x")

            def fail_after_assert(*args, **kwargs):
                self.assertTrue(output.parent.is_dir())
                raise RuntimeError("stop before external ffmpeg")

            media = {
                "width": 540,
                "height": 960,
                "duration": 1.0,
                "video_codec": "h264",
                "video_bitrate": 400_000,
            }
            with (
                patch("walkthrough_edit.pack.probe_media", return_value=media),
                patch("walkthrough_edit.pack.resolve_font", return_value="font.ttf"),
                patch("walkthrough_edit.pack.render_title_overlay", return_value=None),
                patch("walkthrough_edit.pack._resolve_sticker_path", return_value=None),
                patch("walkthrough_edit.pack.resolve_bgm_path", return_value=bgm),
                patch("walkthrough_edit.pack.ensure_encoder"),
                patch("walkthrough_edit.pack.subprocess.Popen", side_effect=fail_after_assert),
            ):
                with self.assertRaisesRegex(RuntimeError, "stop before"):
                    pack_video(source, output, cfg, root / "work", project_root=root)


if __name__ == "__main__":
    unittest.main()
