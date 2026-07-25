import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluate_segments import evaluate
from walkthrough_edit.classify import (
    absorb_short_auto,
    build_segments,
    labels_to_segments,
    normalize_timeline,
    validate_timeline,
)
from walkthrough_edit.config import load_config
from walkthrough_edit.pipeline import run_pipeline
from walkthrough_edit.render import (
    atempo_chain,
    estimate_output_duration,
    select_video_codec,
)


def segment(t0, t1, kind, speed):
    return {"t0": t0, "t1": t1, "kind": kind, "speed": speed}


class CodecTests(unittest.TestCase):
    def test_auto_follows_h264(self):
        self.assertEqual(select_video_codec("h264", "auto"), "libx264")

    def test_auto_follows_hevc(self):
        self.assertEqual(select_video_codec("hevc", "auto"), "libx265")

    def test_explicit_codec_wins(self):
        self.assertEqual(select_video_codec("hevc", "h264"), "libx264")
        self.assertEqual(select_video_codec("h264", "h265"), "libx265")

    def test_atempo_extremes(self):
        self.assertEqual(atempo_chain(4.0), "atempo=2.000000,atempo=2.000000")
        self.assertEqual(atempo_chain(0.25), "atempo=0.500000,atempo=0.500000")


class SegmentTests(unittest.TestCase):
    def test_middle_short_uses_longer_right_neighbor(self):
        segs = [
            segment(0, 1, "room", 1.0),
            segment(1, 1.1, "fast", 3.5),
            segment(1.1, 4, "move", 2.2),
        ]
        result = absorb_short_auto(segs, 0.4)
        self.assertEqual(result[0], segment(0, 1, "room", 1.0))
        self.assertEqual(result[1], segment(1, 4, "move", 2.2))

    def test_short_edges_are_absorbed(self):
        segs = [
            segment(0, 0.1, "fast", 3.5),
            segment(0.1, 2, "room", 1.0),
            segment(2, 2.1, "fast", 3.5),
        ]
        self.assertEqual(absorb_short_auto(segs, 0.4), [segment(0, 2.1, "room", 1.0)])

    def test_short_manual_override_is_preserved(self):
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = False
        cfg["overrides"] = [{"start": 0.45, "end": 0.55, "kind": "fast"}]
        rows = [
            {"idx": i, "t": i / 30, "mean": 100, "std": 50, "edge": 0.1, "motion": 0}
            for i in range(30)
        ]
        result = build_segments(rows, 1.0, cfg)
        manual = [s for s in result if s["kind"] == "fast"]
        self.assertEqual(len(manual), 1)
        self.assertAlmostEqual(manual[0]["t0"], 0.45)
        self.assertAlmostEqual(manual[0]["t1"], 0.55)

    def test_override_outside_duration_is_clipped_away(self):
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = False
        cfg["overrides"] = [{"start": 2, "end": 3, "kind": "fast"}]
        rows = [
            {"idx": i, "t": i / 30, "mean": 100, "std": 50, "edge": 0.1, "motion": 0}
            for i in range(30)
        ]
        result = build_segments(rows, 1.0, cfg)
        self.assertEqual(result, [segment(0.0, 1.0, "room", 1.0)])

    def test_normalized_timeline_is_continuous(self):
        result = normalize_timeline(
            [segment(0, 0.3333333333, "room", 1), segment(0.3333334, 1, "fast", 2)],
            1.0,
        )
        self.assertEqual(result[0]["t1"], result[1]["t0"])
        validate_timeline(result, 1.0)

    def test_labels_to_segments_clamps_past_duration(self):
        """OpenCV timestamps past ffprobe duration must not create invalid segs."""
        cfg = load_config(None)
        rows = [
            {"idx": 0, "t": 0.0},
            {"idx": 1, "t": 0.5},
            {"idx": 2, "t": 1.05},  # past duration=1.0
            {"idx": 3, "t": 1.10},
        ]
        labels = ["room", "room", "fast", "fast"]
        segs = labels_to_segments(rows, labels, duration=1.0, cfg=cfg)
        validate_timeline(normalize_timeline(segs, 1.0), 1.0)
        self.assertTrue(all(s["t1"] <= 1.0 + 1e-9 for s in segs))
        self.assertTrue(all(s["t1"] > s["t0"] for s in segs))

    def test_estimate_output_duration_frame_aware(self):
        segs = [segment(0, 1, "room", 1.0), segment(1, 2, "fast", 2.0)]
        # continuous = 1 + 0.5 = 1.5; at 30fps ceil(45)/30 = 1.5
        self.assertAlmostEqual(estimate_output_duration(segs, 30.0), 1.5)
        self.assertAlmostEqual(estimate_output_duration(segs, None), 1.5)


class ConfigAndCliTests(unittest.TestCase):
    def test_invalid_canny_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text("analysis:\n  canny_low: 200\n  canny_high: 100\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Canny"):
                load_config(path)

    def test_invalid_codec_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text("encode:\n  video_codec: magic\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "video_codec"):
                load_config(path)

    def test_input_output_same_path_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "same.mp4"
            path.write_bytes(b"placeholder")
            with patch("walkthrough_edit.pipeline.check_tools"):
                with self.assertRaisesRegex(ValueError, "different"):
                    run_pipeline(path, path, config_path=None, dry_run=True)


class EvaluationTests(unittest.TestCase):
    def test_false_acceleration_is_reported(self):
        segments = [
            segment(0, 1, "room", 1.0),
            segment(1, 2, "fast", 3.5),
        ]
        labels = [{"start": 0.5, "end": 1.5, "kind": "room"}]
        report = evaluate(segments, labels)
        self.assertEqual(report["accuracy"], 0.5)
        self.assertEqual(report["false_acceleration_seconds"], 0.5)
        self.assertEqual(report["correction_intervals"], 1)
        self.assertEqual(report["compression_ratio"], round((1 + 1 / 3.5) / 2.0, 4))

    def test_unknown_kind_raises(self):
        with self.assertRaisesRegex(ValueError, "kind"):
            evaluate(
                [segment(0, 1, "balcony", 1.0)],
                [{"start": 0, "end": 1, "kind": "room"}],
            )

    def test_invalid_speed_raises(self):
        with self.assertRaisesRegex(ValueError, "speed"):
            evaluate(
                [segment(0, 1, "room", 0.0)],
                [{"start": 0, "end": 1, "kind": "room"}],
            )


if __name__ == "__main__":
    unittest.main()
