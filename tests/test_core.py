import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from evaluate_segments import evaluate
from walkthrough_edit.classify import (
    absorb_short_auto,
    apply_scene_aba_demote,
    apply_static_hold_boost,
    build_segments,
    classify_frames,
    demote_informative_fast,
    demote_sandwich_fast,
    is_flat_surface,
    labels_to_segments,
    normalize_timeline,
    promote_corridor_rooms,
    promote_flat_rooms,
    validate_timeline,
)
from walkthrough_edit.config import load_config
from walkthrough_edit.pipeline import run_pipeline
from walkthrough_edit.render import (
    atempo_chain,
    estimate_output_duration,
    select_video_codec,
)
from walkthrough_edit.scene_aba import fingerprint_gray


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
        cfg["pacing"]["static_boost_enabled"] = False
        cfg["overrides"] = [{"start": 2, "end": 3, "kind": "fast"}]
        rows = [
            {"idx": i, "t": i / 30, "mean": 100, "std": 50, "edge": 0.1, "motion": 0}
            for i in range(30)
        ]
        result = build_segments(rows, 1.0, cfg)
        room_speed = float(cfg["speeds"]["room"])
        self.assertEqual(result, [segment(0.0, 1.0, "room", room_speed)])

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


class DualGateClassifyTests(unittest.TestCase):
    """Content protect + corridor promote (dual-gate) regression tests."""

    def _rows(self, specs: list[tuple[float, float, float]], fps: float = 30.0):
        """Build analysis rows from (motion, edge, std) samples, one per frame."""
        rows = []
        for i, (motion, edge, std) in enumerate(specs):
            rows.append(
                {
                    "idx": i,
                    "t": i / fps,
                    "mean": 100.0,
                    "std": std,
                    "edge": edge,
                    "motion": motion,
                }
            )
        return rows

    def test_high_structure_pan_is_room_not_fast(self):
        """Living-room look-around: high motion + high edge must stay room."""
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = False
        # ~1.5s of high-motion, content-rich frames
        n = 45
        rows = self._rows([(18.0, 0.13, 60.0)] * n)
        labels = classify_frames(rows, cfg)
        self.assertTrue(all(lb == "room" for lb in labels), labels[:5])
        segs = build_segments(rows, n / 30.0, cfg)
        room_speed = float(cfg["speeds"]["room"])
        self.assertTrue(all(s["kind"] == "room" for s in segs), segs)
        # High-structure pans stay room kind (may use room base speed, not fast)
        self.assertTrue(
            all(float(s["speed"]) <= room_speed + 1e-6 for s in segs), segs
        )

    def test_blank_wall_is_fast(self):
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = False
        n = 30
        rows = self._rows([(4.0, 0.02, 20.0)] * n)
        labels = classify_frames(rows, cfg)
        self.assertTrue(all(lb == "fast" for lb in labels))

    def test_high_std_colored_wall_is_still_fast(self):
        """
        Painted / shadowed walls: low edge but elevated std must not stay room.
        Regression for -1a ~7.5–9.8s style flats.
        """
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = False
        # edge 0.028 < wall_edge_max 0.035, std 52 > wall_std_max 45
        n = 45
        rows = self._rows([(6.5, 0.028, 52.0)] * n)
        labels = classify_frames(rows, cfg)
        self.assertTrue(
            all(lb == "fast" for lb in labels),
            f"expected flat wall→fast, got {set(labels)}",
        )
        segs = build_segments(rows, n / 30.0, cfg)
        self.assertTrue(all(s["kind"] == "fast" for s in segs), segs)
        self.assertTrue(is_flat_surface(0.028, 52.0, wall_edge_max=0.035, wall_std_max=45.0))
        self.assertFalse(is_flat_surface(0.08, 52.0, wall_edge_max=0.035, wall_std_max=45.0))

    def test_promote_flat_room_segment(self):
        cfg = load_config(None)
        n = 30
        rows = self._rows([(5.0, 0.025, 55.0)] * n)
        segs = [{"t0": 0.0, "t1": 1.0, "kind": "room", "speed": 1.0}]
        out = promote_flat_rooms(segs, rows, cfg)
        self.assertEqual(out[0]["kind"], "fast")

    def test_low_struct_dash_is_fast(self):
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = False
        n = 30
        rows = self._rows([(20.0, 0.04, 40.0)] * n)
        labels = classify_frames(rows, cfg)
        self.assertTrue(all(lb == "fast" for lb in labels), set(labels))

    def test_sustained_mid_struct_walk_is_move_or_fast(self):
        """Corridor: mid edge + sustained walk should not stay room."""
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = False
        # 2s of corridor walking
        n = 60
        rows = self._rows([(9.0, 0.08, 48.0)] * n)
        segs = build_segments(rows, n / 30.0, cfg)
        kinds = {s["kind"] for s in segs}
        self.assertTrue(kinds & {"move", "fast"}, segs)
        self.assertNotIn("room", kinds, segs)

    def test_sandwich_fast_between_high_struct_rooms_demoted(self):
        cfg = load_config(None)
        speeds = cfg["speeds"]
        rows = []
        # 0–1s high-struct room, 1–2s mid fast, 2–3s high-struct room
        for i in range(90):
            t = i / 30.0
            if t < 1.0 or t >= 2.0:
                edge, motion = 0.14, 8.0
            else:
                edge, motion = 0.10, 16.0
            rows.append(
                {
                    "idx": i,
                    "t": t,
                    "mean": 100.0,
                    "std": 55.0,
                    "edge": edge,
                    "motion": motion,
                }
            )
        segs = [
            segment(0.0, 1.0, "room", speeds["room"]),
            segment(1.0, 2.0, "fast", speeds["fast"]),
            segment(2.0, 3.0, "room", speeds["room"]),
        ]
        out = demote_sandwich_fast(
            segs,
            rows,
            content_struct_min=0.115,
            max_fast_dur=2.5,
            speeds=speeds,
            require_high_struct_neighbors=True,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "room")
        self.assertAlmostEqual(out[0]["t0"], 0.0)
        self.assertAlmostEqual(out[0]["t1"], 3.0)

    def test_promote_corridor_room_run(self):
        cfg = load_config(None)
        n = 60  # 2s
        rows = self._rows([(9.5, 0.085, 50.0)] * n)
        segs = [segment(0.0, 2.0, "room", 1.0)]
        out = promote_corridor_rooms(segs, rows, cfg)
        self.assertEqual(len(out), 1)
        self.assertIn(out[0]["kind"], ("move", "fast"))

    def test_default_speeds_support_natural_walkthrough(self):
        cfg = load_config(None)
        self.assertEqual(cfg["speeds"], {"room": 1.0, "move": 1.18, "fast": 1.30})

    def test_static_hold_boost_raises_speed(self):
        """Near-zero motion room holds get boosted past base room speed."""
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = True
        cfg["pacing"]["static_boost_enabled"] = True
        cfg["pacing"]["static_motion_max"] = 4.5
        cfg["pacing"]["static_min_sec"] = 0.5
        cfg["pacing"]["static_boost_speed"] = 2.7
        n = 60  # 2s at 30fps
        rows = self._rows([(1.0, 0.12, 50.0)] * n)  # very low motion, high edge
        segs = [{"t0": 0.0, "t1": 2.0, "kind": "room", "speed": float(cfg["speeds"]["room"])}]
        out = apply_static_hold_boost(segs, rows, cfg)
        self.assertTrue(out)
        self.assertTrue(
            any(float(s["speed"]) >= 2.7 - 1e-6 for s in out),
            out,
        )

    def test_informative_score_protects_fast_segment(self):
        cfg = load_config(None)
        speeds = cfg["speeds"]
        segs = [segment(0.0, 1.0, "fast", speeds["fast"])]
        rows = self._rows([(18.0, 0.05, 50.0)] * 30)
        for row in rows:
            row["information_score"] = 0.9
        protected = demote_informative_fast(segs, rows, cfg)
        self.assertEqual(protected[0]["kind"], "room")
        for row in rows:
            row["information_score"] = 0.2
        self.assertEqual(demote_informative_fast(segs, rows, cfg)[0]["kind"], "fast")

    def test_scene_aba_demotes_digression_middle(self):
        """Appearance A→B→A forces digression middle off fast (local CV path)."""
        cfg = load_config(None)
        cfg["pacing"]["enabled"] = False
        cfg["pacing"]["static_boost_enabled"] = False
        cfg["scene_aba"]["enabled"] = True
        cfg["scene_aba"]["use_flow_return"] = False
        # Distinct scene fingerprints: A bright, B dark
        fp_a = fingerprint_gray(np.full((64, 64), 200.0, dtype=np.float32))
        fp_b = fingerprint_gray(np.full((64, 64), 30.0, dtype=np.float32))
        fps = 20.0
        rows: list[dict] = []
        # 0–1s A, 1–2s B digression, 2–3s A
        for i in range(int(3 * fps)):
            t = i / fps
            if 1.0 <= t < 2.0:
                fp, motion, edge = fp_b, 12.0, 0.09
            else:
                fp, motion, edge = fp_a, 6.0, 0.14
            rows.append(
                {
                    "idx": i,
                    "t": t,
                    "mean": 100.0,
                    "std": 50.0,
                    "edge": edge,
                    "motion": motion,
                    "appearance": fp,
                    "flow_dx": 0.0,
                    "flow_dy": 0.0,
                }
            )
        speeds = cfg["speeds"]
        segs = [
            segment(0.0, 1.0, "room", speeds["room"]),
            segment(1.0, 2.0, "fast", speeds["fast"]),
            segment(2.0, 3.0, "room", speeds["room"]),
        ]
        out = apply_scene_aba_demote(segs, rows, 3.0, cfg)
        mid = [s for s in out if s["t0"] < 1.5 < s["t1"] or abs(s["t0"] - 1.0) < 0.05]
        self.assertTrue(mid, out)
        self.assertTrue(all(s["kind"] == "room" for s in mid), out)

    def test_scene_aba_skips_without_appearance(self):
        """No fingerprint → no-op (old caches / unit rows)."""
        cfg = load_config(None)
        segs = [
            segment(0.0, 1.0, "room", 1.35),
            segment(1.0, 2.0, "fast", 3.5),
            segment(2.0, 3.0, "room", 1.35),
        ]
        rows = self._rows([(10.0, 0.1, 50.0)] * 90)
        out = apply_scene_aba_demote(segs, rows, 3.0, cfg)
        self.assertEqual([s["kind"] for s in out], ["room", "fast", "room"])


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
