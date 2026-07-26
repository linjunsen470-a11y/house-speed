#!/usr/bin/env python3
"""
房产带看视频 · 自动变速剪辑 CLI

不删除任何片段：房间 1x，转角/空墙加速。参数见 config.yaml。

示例:
  python edit_speed.py 2.mp4
  python edit_speed.py 2.mp4 -c config.yaml -o 2_edited.mp4
  python edit_speed.py 2.mp4 --dry-run
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import sys
import traceback
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Auto variable-speed edit for walkthrough videos "
            "(keeps all frames; rooms 1x; transitions faster)."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Input video path (e.g. 2.mp4)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output path (default: <stem>_edited.mp4 from config)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Analyze and print segment plan; skip final export "
            "(review proxy still encodes if --review is set)"
        ),
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Ignore a matching motion cache and analyze frames again",
    )
    parser.add_argument(
        "--edit-config",
        default=None,
        help="Per-video YAML layer (default: <input-stem>.edit.yaml if present)",
    )
    parser.add_argument(
        "--review",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help=(
            "Create an annotated low-res review proxy (optional path); "
            "encodes even with --dry-run"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print only a machine-readable JSON result",
    )
    parser.add_argument(
        "--pack",
        action="store_true",
        help=(
            "After speed-edit, burn full-video title + DM sticker and "
            "replace audio with BGM only (see pack.* in config / .edit.yaml)"
        ),
    )
    parser.add_argument(
        "--pack-only",
        action="store_true",
        help="Skip analyze/export; re-pack existing work/*/speed_raw.mp4",
    )
    parser.add_argument(
        "--list-styles",
        action="store_true",
        help="List built-in title styles, stickers, and BGM presets, then exit",
    )
    args = parser.parse_args(argv)

    if args.list_styles:
        root = Path(__file__).resolve().parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from walkthrough_edit.music_catalog import BGM_PRESETS
        from walkthrough_edit.stickers_gen import STICKER_SPECS
        from walkthrough_edit.text_styles import STYLES, list_styles

        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print("Title styles (pack.style):")
        for key in list_styles():
            print(f"  {key:16s}  {STYLES[key].get('label', '')}")
        print("\nSticker styles (pack.sticker.style):")
        for key, meta in STICKER_SPECS.items():
            print(f"  {key:16s}  {meta.get('label', '')}")
        from walkthrough_edit.music_catalog import list_bgm_ids_present

        print("\nBGM presets (pack.audio.bgm):")
        print("  random           优先 curated，否则 shortlist / a*")
        assets = root / "assets"
        present = set(list_bgm_ids_present(assets))
        for key, meta in BGM_PRESETS.items():
            mark = " " if key in present else "!"
            print(
                f"  {mark}{key:15s}  {meta.get('label', '')}  — {meta.get('vibe', '')}"
            )
        print("  (! = 文件不在磁盘；CC BY 可 python scripts/fetch_bgm.py)")
        return 0

    if not args.input:
        parser.print_help()
        print("\nError: please provide an input video, e.g. python edit_speed.py 2.mp4")
        return 2

    # Allow running from project root without install
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from walkthrough_edit.pipeline import run_pipeline

    config_path = args.config
    if config_path and not Path(config_path).is_file():
        # fall back to project-root config
        alt = root / config_path
        if alt.is_file():
            config_path = str(alt)
        else:
            if not args.json:
                print(f"Warning: config not found ({args.config}), using built-in defaults")
            config_path = None

    try:
        kwargs = {
            "input_path": args.input,
            "output_path": args.output,
            "config_path": config_path,
            "dry_run": args.dry_run,
            "edit_config_path": args.edit_config,
            "reanalyze": args.reanalyze,
            "review_path": args.review,
            "pack": True if args.pack else None,
            "pack_only": bool(args.pack_only),
        }
        if args.json:
            with redirect_stdout(io.StringIO()):
                result = run_pipeline(**kwargs)
            print(json.dumps(result.summary, ensure_ascii=False))
        else:
            run_pipeline(**kwargs)
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
            return 1
        print(f"Error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
