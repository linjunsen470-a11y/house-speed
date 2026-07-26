import json
import shutil
import subprocess
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
from walkthrough_edit.pack import (
    _resolve_sticker_path,
    collect_text_content,
    compute_sticker_windows,
    pack_video,
    render_title_overlay,
    resolve_font,
)
from walkthrough_edit.render import (
    atempo_chain,
    estimate_output_duration,
    select_video_codec,
)
from walkthrough_edit.text_styles import get_style, list_styles


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


class PackTests(unittest.TestCase):
    def test_styles_exist(self):
        names = list_styles()
        self.assertIn("douyin_estate", names)
        self.assertIn("douyin_fire", names)
        self.assertIn("douyin_pink", names)
        self.assertGreaterEqual(len(names), 6)
        style = get_style("douyin_fire")
        self.assertIn("stroke_outer", style)
        self.assertEqual(style["kind"], "douyin")
        self.assertEqual(get_style("douyin_estate")["kind"], "estate")

    def test_legacy_style_aliases(self):
        # Old plain styles map to punchy douyin skins
        self.assertEqual(get_style("bar_dark")["fill"], get_style("douyin_fire")["fill"])

    def test_unknown_style_raises(self):
        with self.assertRaisesRegex(ValueError, "Unknown text style"):
            get_style("not_a_real_style")

    def test_render_title_overlay_png(self):
        cfg = load_config(None)
        font = resolve_font(cfg, Path("assets").resolve())
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "title.png"
            path = render_title_overlay(
                ["绿湖全新未入住", "101平三房", "业主忍痛割爱", "单价5XXX"],
                "douyin_fire",
                540,
                960,
                font,
                out,
            )
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 2000)

    def test_pack_config_defaults(self):
        cfg = load_config(None)
        self.assertIn("pack", cfg)
        self.assertFalse(cfg["pack"]["enabled"])
        self.assertEqual(cfg["pack"]["style"], "douyin_estate")
        self.assertEqual(cfg["pack"]["sticker"]["style"], "dm_estate_cta")
        self.assertEqual(cfg["pack"]["sticker"]["text"], "私信了解")
        self.assertEqual(cfg["pack"]["sticker"]["enter"], "slide_up")
        self.assertEqual(cfg["pack"]["layout"]["highlights_mode"], "join")
        self.assertEqual(cfg["pack"]["text_motion"]["enter"], "fade")
        self.assertEqual(cfg["pack"]["text_motion"]["bounce_px"], 0)
        self.assertEqual(cfg["pack"]["text_motion"]["pulse"], 0)
        self.assertTrue(cfg["pack"]["sticker"]["repeat_at_end"])
        estate = get_style("douyin_estate")["roles"]
        # Two families: accent (title + warmer price) vs secondary (highlights)
        self.assertEqual(estate["title"]["stroke_inner"][:3], (255, 255, 255))
        self.assertEqual(estate["price"]["stroke_inner"][:3], (255, 255, 255))
        self.assertEqual(estate["title"]["stroke_outer"][2], 175)  # blue ring family
        self.assertNotEqual(estate["price"]["fill"], estate["title"]["fill"])
        self.assertTrue(estate["price"].get("sparkle"))
        self.assertFalse(estate["price"].get("underline", False))
        self.assertNotEqual(estate["highlights"]["fill"], estate["title"]["fill"])
        self.assertTrue(cfg["pack"]["text_motion"]["sparkle_anim"])

    def test_structured_text_content(self):
        pack = {"text": {
            "title": "\u7eff\u6e56\u5168\u65b0\u672a\u5165\u4f4f",
            "highlights": ["101\u5e73\u4e09\u623f", "\u4e1a\u4e3b\u6025\u552e"],
            "price": "\u5355\u4ef75XXXX",
        }}
        content = collect_text_content(pack, {})
        self.assertEqual(content["title"], "\u7eff\u6e56\u5168\u65b0\u672a\u5165\u4f4f")
        self.assertEqual(content["highlights"], ["101\u5e73\u4e09\u623f", "\u4e1a\u4e3b\u6025\u552e"])
        self.assertEqual(content["price"], "\u5355\u4ef75XXXX")

    def test_legacy_lines_map_to_roles(self):
        content = collect_text_content(
            {"text": {"lines": ["title", "fact one", "fact two", "price"]}}, {}
        )
        self.assertEqual(content, {
            "title": "title", "highlights": ["fact one", "fact two"], "price": "price"
        })

    def test_estate_overlay_fits_safe_width_and_optional_roles(self):
        from PIL import Image

        cfg = load_config(None)
        font = resolve_font(cfg, Path("assets").resolve())
        cases = [
            {"title": "A very long estate headline that must shrink independently", "highlights": ["fact"], "price": "price"},
            {"title": "title only", "highlights": [], "price": ""},
            {"title": "", "highlights": [], "price": "price only"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            for i, content in enumerate(cases):
                out = Path(directory) / f"estate_{i}.png"
                lines = [content["title"], *content["highlights"], content["price"]]
                render_title_overlay(
                    lines, "douyin_estate", 540, 960, font, out, content=content
                )
                bbox = Image.open(out).getchannel("A").getbbox()
                self.assertIsNotNone(bbox)
                self.assertGreaterEqual(bbox[0], 24)
                self.assertLessEqual(bbox[2], 516)

    def test_estate_highlights_stack_mode(self):
        from PIL import Image

        cfg = load_config(None)
        font = resolve_font(cfg, Path("assets").resolve())
        content = {
            "title": "绿湖全新未入住",
            "highlights": ["101平三房", "业主忍痛割爱"],
            "price": "单价5XXX",
        }
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "stack.png"
            render_title_overlay(
                [], "douyin_estate", 540, 960, font, out,
                content=content,
                layout={"y_rel": 0.255, "max_width_rel": 0.88, "highlights_mode": "stack"},
            )
            bbox = Image.open(out).getchannel("A").getbbox()
            self.assertIsNotNone(bbox)
            self.assertGreaterEqual(bbox[0], 20)
            self.assertLessEqual(bbox[2], 520)
            # Stacked highlights should produce a taller block than join
            out_join = Path(directory) / "join.png"
            render_title_overlay(
                [], "douyin_estate", 540, 960, font, out_join,
                content=content,
                layout={"y_rel": 0.255, "max_width_rel": 0.88, "highlights_mode": "join"},
            )
            h_stack = bbox[3] - bbox[1]
            h_join = Image.open(out_join).getchannel("A").getbbox()
            self.assertIsNotNone(h_join)
            self.assertGreater(h_stack, h_join[3] - h_join[1] - 5)

    def test_pack_enter_validation(self):
        from walkthrough_edit.config import _validate

        cfg = load_config(None)
        cfg["pack"]["sticker"]["enter"] = "zoom"
        with self.assertRaises(ValueError):
            _validate(cfg)
        cfg = load_config(None)
        cfg["pack"]["text_motion"]["enter"] = "slide"
        with self.assertRaises(ValueError):
            _validate(cfg)
        cfg = load_config(None)
        cfg["pack"]["layout"]["highlights_mode"] = "wrap"
        with self.assertRaises(ValueError):
            _validate(cfg)

    def test_sticker_windows_full_and_short_video(self):
        sticker = {
            "start": 4.5, "duration": 2.5,
            "repeat_at_end": True, "end_lead": 2.8,
        }
        self.assertEqual(
            compute_sticker_windows(20.733, sticker),
            [(4.5, 7.0), (17.933, 20.733)],
        )
        self.assertEqual(compute_sticker_windows(6.0, sticker), [(3.2, 6.0)])
        sticker["repeat_at_end"] = False
        self.assertEqual(compute_sticker_windows(6.0, sticker), [(4.5, 6.0)])

    def test_sticker_disabled_and_custom_file(self):
        cfg = load_config(None)
        assets = Path("assets").resolve()
        cfg["pack"]["sticker"]["enabled"] = False
        self.assertIsNone(_resolve_sticker_path(cfg, assets))
        with tempfile.TemporaryDirectory() as directory:
            custom = Path(directory) / "cta.png"
            custom.write_bytes(b"not-decoded-during-resolution")
            cfg["pack"]["sticker"].update({"enabled": True, "file": str(custom)})
            resolved = _resolve_sticker_path(cfg, assets)
            self.assertEqual(resolved[0], custom.resolve())

    def test_sticker_text_configurable(self):
        cfg = load_config(None)
        self.assertEqual(cfg["pack"]["sticker"]["text"], "私信了解")
        cfg["pack"]["sticker"]["text"] = "私"
        from walkthrough_edit.config import _validate

        _validate(cfg)
        self.assertEqual(cfg["pack"]["sticker"]["text"], "私")
        cfg["pack"]["sticker"]["text"] = "私信了解"
        _validate(cfg)
        self.assertEqual(cfg["pack"]["sticker"]["text"], "私信了解")
        cfg["pack"]["sticker"]["text"] = "太长了超过八个字不行吧"
        with self.assertRaises(ValueError):
            _validate(cfg)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_estate_cta_bakes_text(self):
        cfg = load_config(None)
        assets = Path("assets").resolve()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            cfg["pack"]["sticker"].update({"style": "dm_estate_cta", "text": "私", "file": ""})
            path, meta = _resolve_sticker_path(cfg, assets, work_dir=work)
            self.assertTrue(path.is_file())
            self.assertEqual(meta.get("text"), "私")
            self.assertIn("dm_estate_cta_", path.name)
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_pack_integration_media_probe(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "packed.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=white:s=320x568:r=12:d=1.5",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=1.5",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(source),
            ], check=True)
            cfg = load_config(None)
            cfg["pack"]["text"].update({
                "title": "Test property", "highlights": ["Two rooms"], "price": "5XXX"
            })
            cfg["pack"]["sticker"]["enabled"] = False
            cfg["pack"]["audio"]["bgm"] = str(source.resolve())
            pack_video(source, output, cfg, root / "work", project_root=Path.cwd())
            raw = subprocess.check_output([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
                "-of", "json", str(output),
            ], text=True)
            data = json.loads(raw)
            streams = data["streams"]
            video = next(stream for stream in streams if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (320, 568))
            self.assertEqual(video["r_frame_rate"], "12/1")
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in streams))
            self.assertAlmostEqual(float(data["format"]["duration"]), 1.5, delta=0.12)
    def test_bgm_resolve_presets(self):
        from walkthrough_edit.music_catalog import (
            BGM_PRESETS,
            DEFAULT_BGM,
            list_bgm_ids_present,
            resolve_bgm_path,
        )

        assets = Path(__file__).resolve().parents[1] / "assets"
        present = list_bgm_ids_present(assets)
        self.assertIn("pop_hook", present, "curated pop_hook should exist")
        self.assertEqual(DEFAULT_BGM, "pop_hook")
        # Every present preset resolves
        for key in present:
            p = resolve_bgm_path(key, assets)
            self.assertTrue(p.is_file(), key)
        # Default curated
        p = resolve_bgm_path("pop_hook", assets)
        self.assertTrue(p.is_file())
        self.assertTrue(p.name.startswith("bgm_pop_"))
        # shortlist + local library ids when files exist
        if "sl01" in present:
            self.assertTrue(resolve_bgm_path("sl01", assets).is_file())
            self.assertTrue(resolve_bgm_path("01", assets).is_file())
        if "a1" in present:
            self.assertTrue(resolve_bgm_path("a1", assets).is_file())
        # legacy carefree aliases to pop_hook when CC BY file absent
        p_legacy = resolve_bgm_path("carefree", assets)
        self.assertTrue(p_legacy.is_file())
        p2 = resolve_bgm_path("random", assets, rng=__import__("random").Random(0))
        self.assertTrue(p2.is_file())
        # catalog still lists missing CC BY for optional fetch_bgm
        self.assertIn("carefree", BGM_PRESETS)


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
