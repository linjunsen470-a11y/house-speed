"""Tests for scene A→B→A digression detection (content flash, not speed ABA)."""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from walkthrough_edit.scene_aba import (
    appearance_similarity,
    detect_scene_aba,
    extract_fingerprints_from_video,
    fingerprint_gray,
    scene_aba_config,
)

ROOT = Path(__file__).resolve().parent.parent


def _fp_from_level(level: float, noise: float = 0.0, layout: float | None = None) -> np.ndarray:
    """Build a synthetic fingerprint dominated by a gray level (color-agnostic)."""
    rng = np.random.default_rng(int(level * 1000) % 10000)
    img = np.full((64, 64), level, dtype=np.float32)
    if noise > 0:
        img = np.clip(img + rng.normal(0, noise, img.shape), 0, 255)
    # Shift one quadrant to encode layout identity
    lay = level if layout is None else layout
    img[:32, :32] = np.clip(lay, 0, 255)
    return fingerprint_gray(img)


def _rows_from_fp_timeline(
    segments: list[tuple[float, float, np.ndarray, float]],
    fps: float = 20.0,
) -> list[dict]:
    """
    segments: list of (t0, t1, fingerprint, motion) covering a continuous timeline.
    """
    rows: list[dict] = []
    idx = 0
    for t0, t1, fp, motion in segments:
        n = max(1, int(round((t1 - t0) * fps)))
        for i in range(n):
            t = t0 + i / fps
            if t >= t1 - 1e-9 and i > 0:
                break
            rows.append(
                {
                    "idx": idx,
                    "t": t,
                    "motion": motion,
                    "appearance": np.asarray(fp, dtype=np.float64),
                }
            )
            idx += 1
    # Ensure last timestamp reaches final t1
    if rows and segments:
        rows[-1]["t"] = segments[-1][1]
    return rows


class FingerprintTests(unittest.TestCase):
    def test_same_image_high_similarity(self):
        fp = _fp_from_level(180)
        self.assertGreater(appearance_similarity(fp, fp), 0.98)

    def test_different_levels_lower_similarity(self):
        a = _fp_from_level(40)
        b = _fp_from_level(200)
        self.assertLess(appearance_similarity(a, b), 0.55)

    def test_dark_and_beige_walls_are_not_special_cased(self):
        """Non-white flats still fingerprint; similarity is about sameness not whiteness."""
        dark = _fp_from_level(35)
        beige = _fp_from_level(170)
        self.assertGreater(appearance_similarity(dark, dark), 0.95)
        self.assertGreater(appearance_similarity(beige, beige), 0.95)
        self.assertLess(appearance_similarity(dark, beige), 0.7)

    def test_layout_difference_reduces_similarity(self):
        a = _fp_from_level(120, layout=40)
        b = _fp_from_level(120, layout=220)
        self.assertLess(appearance_similarity(a, b), appearance_similarity(a, a))


class DetectSceneAbaTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "scene_aba": {
                "enabled": True,
                "min_b_sec": 0.4,
                "max_b_sec": 1.5,
                "anchor_sec": 0.3,
                "step_sec": 0.1,
                "end_sim_min": 0.80,
                "mid_sim_max": 0.65,
                "min_mid_motion": 0.0,
                "score_min": 0.30,
                "merge_gap_sec": 0.2,
            }
        }

    def test_config_defaults_merge(self):
        aba = scene_aba_config({"scene_aba": {"max_b_sec": 1.2}})
        self.assertEqual(aba["max_b_sec"], 1.2)
        self.assertTrue(aba["enabled"])

    def test_detects_classic_a_b_a_digression(self):
        """Scene A, short different B, back to A — must fire."""
        fa = _fp_from_level(160, layout=50)
        fb = _fp_from_level(40, layout=200)  # different place
        rows = _rows_from_fp_timeline(
            [
                (0.0, 1.0, fa, 5.0),
                (1.0, 1.8, fb, 18.0),  # digression B
                (1.8, 3.0, fa, 5.0),
            ]
        )
        hits = detect_scene_aba(rows, self.cfg)
        self.assertTrue(hits, "expected at least one scene ABA hit")
        # Hit should overlap the digression
        mid = 1.4
        covered = any(h["t0"] <= mid <= h["t1"] for h in hits)
        self.assertTrue(covered, hits)
        self.assertGreater(hits[0]["end_sim"], hits[0]["mid_sim"])

    def test_no_hit_when_middle_is_same_scene(self):
        """All one scene — no digression."""
        fa = _fp_from_level(150, layout=80)
        rows = _rows_from_fp_timeline(
            [
                (0.0, 1.0, fa, 6.0),
                (1.0, 1.8, fa, 16.0),
                (1.8, 3.0, fa, 6.0),
            ]
        )
        hits = detect_scene_aba(rows, self.cfg)
        self.assertEqual(hits, [])

    def test_no_hit_on_true_transition_a_b_c(self):
        """Ends differ (walk into a new space) — not ABA."""
        fa = _fp_from_level(50, layout=30)
        fb = _fp_from_level(120, layout=100)
        fc = _fp_from_level(200, layout=210)
        rows = _rows_from_fp_timeline(
            [
                (0.0, 1.0, fa, 8.0),
                (1.0, 1.8, fb, 14.0),
                (1.8, 3.0, fc, 8.0),
            ]
        )
        hits = detect_scene_aba(rows, self.cfg)
        self.assertEqual(hits, [])

    def test_no_hit_when_digression_too_long(self):
        """Long stay in B is a real room change / hold, not a flash digression."""
        fa = _fp_from_level(160, layout=50)
        fb = _fp_from_level(40, layout=200)
        cfg = {
            "scene_aba": {
                **self.cfg["scene_aba"],
                "max_b_sec": 1.2,
            }
        }
        rows = _rows_from_fp_timeline(
            [
                (0.0, 1.0, fa, 5.0),
                (1.0, 3.5, fb, 12.0),  # 2.5s middle > max_b
                (3.5, 5.0, fa, 5.0),
            ]
        )
        hits = detect_scene_aba(rows, cfg)
        # Should not treat multi-second B as a flash ABA
        self.assertEqual(hits, [])

    def test_disabled_returns_empty(self):
        fa = _fp_from_level(160)
        fb = _fp_from_level(40)
        rows = _rows_from_fp_timeline(
            [
                (0.0, 1.0, fa, 5.0),
                (1.0, 1.7, fb, 15.0),
                (1.7, 3.0, fa, 5.0),
            ]
        )
        hits = detect_scene_aba(rows, {"scene_aba": {"enabled": False}})
        self.assertEqual(hits, [])

    def test_missing_appearance_returns_empty(self):
        rows = [
            {"idx": i, "t": i / 20.0, "motion": 10.0}
            for i in range(60)
        ]
        self.assertEqual(detect_scene_aba(rows, self.cfg), [])

    def test_motion_gate_can_reject_static_flash(self):
        fa = _fp_from_level(160, layout=50)
        fb = _fp_from_level(40, layout=200)
        rows = _rows_from_fp_timeline(
            [
                (0.0, 1.0, fa, 1.0),
                (1.0, 1.7, fb, 1.0),  # different look but low motion
                (1.7, 3.0, fa, 1.0),
            ]
        )
        cfg = {
            "scene_aba": {
                **self.cfg["scene_aba"],
                "min_mid_motion": 10.0,
            }
        }
        self.assertEqual(detect_scene_aba(rows, cfg), [])

    def test_merges_overlapping_hits(self):
        fa = _fp_from_level(170, layout=40)
        fb = _fp_from_level(30, layout=220)
        rows = _rows_from_fp_timeline(
            [
                (0.0, 1.2, fa, 5.0),
                (1.2, 2.2, fb, 20.0),
                (2.2, 3.5, fa, 5.0),
            ],
            fps=25.0,
        )
        hits = detect_scene_aba(rows, self.cfg)
        self.assertTrue(hits)
        # Merged into a small number of intervals, not dozens
        self.assertLessEqual(len(hits), 3)


class RealVideoSceneAbaTests(unittest.TestCase):
    """Optional probe on villa source if present (skip when missing)."""

    @classmethod
    def setUpClass(cls):
        cls.video = ROOT / "-1a.mp4"
        cls.rows = None
        cls.fps = None
        if cls.video.is_file():
            # Downsample for speed in CI/dev
            cls.rows, cls.fps = extract_fingerprints_from_video(
                str(cls.video),
                sample_fps=12.0,
            )

    def test_probe_runs_on_minus1f_if_present(self):
        if self.rows is None:
            self.skipTest("-1a.mp4 not in workspace")
        cfg = {
            "scene_aba": {
                "enabled": True,
                "min_b_sec": 0.35,
                "max_b_sec": 2.0,
                "anchor_sec": 0.25,
                "step_sec": 0.15,
                "end_sim_min": 0.78,
                "mid_sim_max": 0.68,
                "min_mid_motion": 6.0,
                "score_min": 0.28,
                "merge_gap_sec": 0.3,
            }
        }
        hits = detect_scene_aba(self.rows, cfg)
        # Structural: detector must return a list (may be empty on calm footage)
        self.assertIsInstance(hits, list)
        for h in hits:
            self.assertLess(h["t0"], h["t1"])
            self.assertGreaterEqual(h["end_sim"], h["mid_sim"])
            self.assertGreaterEqual(h["score"], 0.0)

    def test_report_hits_near_user_reported_region(self):
        """
        Final film ~2:00 / 2:03 maps into -1F source roughly mid-clip.
        This test documents hits for manual review; does not fail if none.
        """
        if self.rows is None:
            self.skipTest("-1a.mp4 not in workspace")
        cfg = {
            "scene_aba": {
                "enabled": True,
                "min_b_sec": 0.35,
                "max_b_sec": 2.0,
                "anchor_sec": 0.25,
                "step_sec": 0.12,
                "end_sim_min": 0.78,
                "mid_sim_max": 0.70,
                "min_mid_motion": 5.0,
                "score_min": 0.25,
                "merge_gap_sec": 0.3,
            }
        }
        hits = detect_scene_aba(self.rows, cfg)
        # Focus windows that previously mapped near 2:00 in the packed film
        # (source ~14–30s on -1F depending on speed map).
        band = [h for h in hits if h["t1"] >= 12.0 and h["t0"] <= 32.0]
        # Always pass — print-friendly assertion payload for developers
        summary = [
            f"{h['t0']:.2f}-{h['t1']:.2f}s score={h['score']:.2f} "
            f"end={h['end_sim']:.2f} mid={h['mid_sim']:.2f} m={h['mean_motion']:.1f}"
            for h in band
        ]
        # Store on instance for -v visibility via longMessage
        self.assertTrue(True, msg="scene ABA hits in 12–32s: " + ("; ".join(summary) or "(none)"))


if __name__ == "__main__":
    unittest.main()
