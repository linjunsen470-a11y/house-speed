#!/usr/bin/env python3
"""
房产带看视频 · 自动变速剪辑 CLI

保留完整源时间段：实景展示较慢，走廊和空墙加速。参数见 config.yaml。

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
            "(preserves source intervals; rooms slower; transitions faster)."
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
        default=None,
        help="Explicit config path (default: current-directory or project config.yaml)",
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
    args = parser.parse_args(argv)

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
    if config_path is None:
        candidate = Path("config.yaml")
        config_path = candidate if candidate.is_file() else root / "config.yaml"
        if not Path(config_path).is_file():
            config_path = None

    try:
        if args.config is not None and not Path(args.config).is_file():
            raise FileNotFoundError(f"Config not found: {args.config}")
        kwargs = {
            "input_path": args.input,
            "output_path": args.output,
            "config_path": config_path,
            "dry_run": args.dry_run,
            "edit_config_path": args.edit_config,
            "reanalyze": args.reanalyze,
            "review_path": args.review,
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
