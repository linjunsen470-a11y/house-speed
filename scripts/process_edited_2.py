#!/usr/bin/env python3
"""
用当前 config + 管线重处理 edited_2/ 下源素材，并打包 251㎡ 别墅成片。

- 单片：对每个源 mp4 写出 *_edited.mp4
- 成片：1F→2F→3F→-1F→-2F 拼接包装 → villa_master_edited2.mp4
- 规则：首秒及封面保持干净无楼层角标；角标从 t=1.0s 淡入。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from walkthrough_edit.config import load_config
from walkthrough_edit.pipeline import run_pipeline
from walkthrough_edit.pack import pack_video, resolve_font
from walkthrough_edit.render import probe_duration, probe_media
from PIL import Image, ImageDraw, ImageFont

MEDIA = root / "legacy" / "edited_2"

STANDALONE = [
    "1-包括前花园后花园.mp4",
    "2a.mp4",
    "2b.mp4",
    "3a-主人房.mp4",
    "3b-主人房.mp4",
    "-1a.mp4",
    "-1b.mp4",
    "-2.mp4",
]

FLOOR_SEGMENTS = [
    {"floor": "1F", "video": "1-包括前花园后花园.mp4", "badge": "1F | 首层奢阔厅院"},
    {"floor": "2F", "video": "2b.mp4", "badge": "2F | 尊享卧室套房"},
    {"floor": "3F", "video": "3b-主人房.mp4", "badge": "3F | 奢华主卧露台"},
    {"floor": "-1F", "video": "-1a.mp4", "badge": "-1F | 地下采光夹层"},
    {"floor": "-2F", "video": "-2.mp4", "badge": "-2F | 地下车库多功能厅"},
]


def _even(n: int) -> int:
    n = max(2, int(n))
    return n if n % 2 == 0 else n - 1


def resolve_output_canvas(source_paths: list[Path]) -> tuple[int, int]:
    widths: list[int] = []
    heights: list[int] = []
    for p in source_paths:
        if not p.is_file():
            continue
        try:
            m = probe_media(p)
            w, h = int(m.get("width") or 0), int(m.get("height") or 0)
            if w > 0 and h > 0:
                widths.append(w)
                heights.append(h)
        except Exception as exc:
            print(f"  警告: 无法探测 {p.name}: {exc}")
    if not widths:
        return 540, 960
    return _even(min(widths)), _even(min(heights))


def create_floor_badge(
    floor_label: str, out_png: Path, font_path: str, *, canvas_w: int
) -> None:
    font_size = max(18, int(36 * (canvas_w / 1080.0)))
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()
    dummy = Image.new("RGBA", (1, 1))
    draw_dummy = ImageDraw.Draw(dummy)
    bbox = draw_dummy.textbbox((0, 0), floor_label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    px = max(12, int(28 * (canvas_w / 1080.0)))
    py = max(6, int(14 * (canvas_w / 1080.0)))
    w, h = tw + px * 2, th + py * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1],
        radius=max(4, h // 2),
        fill=(18, 22, 36, 210),
        outline=(255, 185, 45, 240),
        width=max(2, int(3 * (canvas_w / 1080.0))),
    )
    draw.text((px, max(0, py - 2)), floor_label, font=font, fill=(255, 255, 255, 255))
    img.save(out_png, "PNG")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not MEDIA.is_dir():
        raise SystemExit(f"missing media dir: {MEDIA}")

    cfg_path = root / "config.yaml"
    cfg_base = load_config(cfg_path)
    font_path = resolve_font(cfg_base, root / "assets")

    print("==========================================")
    print(" edited_2 (251㎡) · 单片智能变速")
    print("==========================================")
    for name in STANDALONE:
        src = MEDIA / name
        if not src.is_file():
            print(f"  skip missing {name}")
            continue
        out = MEDIA / f"{src.stem}_edited.mp4"
        print(f"\n>>> {src.name} → {out.name}")
        res = run_pipeline(
            input_path=src,
            output_path=out,
            config_path=cfg_path,
            pack=False,
            dry_run=False,
        )
        print(f"  └─ {res.duration_out:.2f}s → {out}")

    print("\n==========================================")
    print(" edited_2 (251㎡) · 别墅全层打包")
    print("==========================================")
    src_paths = [MEDIA / item["video"] for item in FLOOR_SEGMENTS]
    for p in src_paths:
        if not p.is_file():
            raise SystemExit(f"成片缺少源文件: {p}")

    out_w, out_h = resolve_output_canvas(src_paths)
    print(f">>> 成片画布: {out_w}x{out_h}")

    work_base = root / "frames" / "villa_master_edited2"
    work_base.mkdir(parents=True, exist_ok=True)
    badge_dir = work_base / "badges"
    badge_dir.mkdir(exist_ok=True)
    norm_dir = work_base / "normalized"
    norm_dir.mkdir(exist_ok=True)

    edited_clips = []
    for item in FLOOR_SEGMENTS:
        floor = item["floor"]
        src = MEDIA / item["video"]
        pre = MEDIA / f"{src.stem}_edited.mp4"
        if pre.is_file():
            path = pre
            dur = probe_duration(path)
            print(f"\n>>> 成片素材 [{floor}] 复用 {pre.name} ({dur:.2f}s)")
        else:
            print(f"\n>>> 成片素材 [{floor}] {src.name}")
            res = run_pipeline(
                input_path=src,
                config_path=cfg_path,
                pack=False,
                dry_run=False,
            )
            path = res.work_dir / "speed_raw.mp4"
            if not path.is_file():
                path = res.output_path
            dur = res.duration_out
            print(f"  └─ {path} ({dur:.2f}s)")
        edited_clips.append(
            {"floor": floor, "path": path, "badge_text": item["badge"]}
        )

    badge_x = max(16, int(50 * (out_w / 1080.0)))
    badge_y = max(24, int(70 * (out_h / 1920.0)))
    norm_clips = []
    for idx, item in enumerate(edited_clips):
        floor = item["floor"]
        src_path = item["path"]
        dur = probe_duration(src_path)
        badge_png = badge_dir / f"badge_{floor}.png"
        create_floor_badge(item["badge_text"], badge_png, font_path, canvas_w=out_w)
        out_path = norm_dir / f"norm_{idx}_{floor}.mp4"
        fade_in = 0.25 if idx > 0 else 0.0
        fade_out = 0.25 if idx < len(edited_clips) - 1 else 0.0
        vfilters = [
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease",
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
            "fps=30",
            "format=yuv420p",
        ]
        if fade_in > 0:
            vfilters.append(f"fade=t=in:st=0:d={fade_in}")
        if fade_out > 0 and dur > fade_out:
            vfilters.append(f"fade=t=out:st={dur - fade_out:.3f}:d={fade_out}")
        
        # 首层 (1F) 延时 1.0s 显示角标，保证首帧和封面干净
        enable_str = ":enable='gte(t,1.0)'" if idx == 0 else ""
        fc = (
            f"[0:v]{','.join(vfilters)}[bg];"
            f"[bg][1:v]overlay=x={badge_x}:y={badge_y}{enable_str}:format=auto[outv]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(src_path),
            "-i", str(badge_png),
            "-filter_complex", fc,
            "-map", "[outv]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an",
            str(out_path),
        ]
        print(f"  badge+norm [{item['badge_text']}] → {out_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        norm_clips.append(out_path)

    concat_list = work_base / "concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in norm_clips:
            f.write(f"file '{p.resolve().as_posix()}'\n")
    concat_raw = work_base / "speed_raw_concat.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c", "copy", str(concat_raw),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    concat_dur = probe_duration(concat_raw)
    print(f"\n>>> concat {concat_dur:.2f}s")

    merged_cfg = load_config(cfg_path)
    merged_cfg.setdefault("encode", {})
    merged_cfg["encode"]["match_source"] = False
    merged_cfg["encode"]["crf"] = 23
    merged_cfg["encode"]["preset"] = "medium"
    merged_cfg["encode"]["pixel_format"] = "yuv420p"
    merged_cfg["encode"]["video_codec"] = "libx264"
    merged_cfg["pack"]["style"] = "douyin_estate"
    merged_cfg["pack"]["text"] = {
        "title": "250平奢阔5房抄底价",
        "highlights": ["251㎡五房3厅", "买三层送两层", "25㎡观景露台"],
        "price": "性价比封神 / 私信了解",
    }
    merged_cfg["pack"]["layout"] = {
        "y_rel": 0.255,
        "max_width_rel": 0.88,
        "highlights_mode": "join",
    }
    merged_cfg["pack"]["sticker"] = {
        "enabled": True,
        "style": "dm_estate_cta",
        "text": "私信看房 / 领底价",
        "start": 4.5,
        "duration": 2.5,
        "repeat_at_end": True,
        "end_lead": 2.8,
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
    }
    merged_cfg["pack"]["audio"] = {
        "bgm": "assets/music/a7.m4a",
        "volume": 0.80,
        "fade_in": 0.5,
        "fade_out": 0.8,
    }

    final_out = MEDIA / "villa_master_edited2.mp4"
    print(f"\n>>> 打包成片最终导出 → {final_out}")
    pack_video(
        input_path=concat_raw,
        output_path=final_out,
        cfg=merged_cfg,
        work_dir=work_base,
        project_root=root,
    )
    print(f"\n✅ 完成！成片路径: {final_out} (时长: {probe_duration(final_out):.2f}s)")


if __name__ == "__main__":
    main()
