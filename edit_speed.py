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
import sys
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
        help="Only analyze and print segment plan; do not run ffmpeg",
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
    if config_path and not Path(config_path).is_file():
        # fall back to project-root config
        alt = root / config_path
        if alt.is_file():
            config_path = str(alt)
        else:
            print(f"Warning: config not found ({args.config}), using built-in defaults")
            config_path = None

    try:
        run_pipeline(
            input_path=args.input,
            output_path=args.output,
            config_path=config_path,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
