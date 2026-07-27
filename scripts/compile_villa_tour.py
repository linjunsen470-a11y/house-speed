#!/usr/bin/env python3
"""
别墅全楼层带看视频 · 自动变速精选、楼层角标、黑转拼接与全套包装脚本

楼层带看顺序：1F ➔ 2F ➔ 3F ➔ -1F ➔ -2F

输出分辨率默认对齐源素材（取各层最小宽高，不升采样），避免 540p 源被拉成 1080p 虚大体积。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Ensure project root is in sys.path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from walkthrough_edit.config import load_config
from walkthrough_edit.pipeline import run_pipeline
from walkthrough_edit.pack import pack_video, resolve_font
from walkthrough_edit.render import probe_duration, probe_media
from PIL import Image, ImageDraw, ImageFont


def _even(n: int) -> int:
    """H.264 friendly even dimension."""
    n = max(2, int(n))
    return n if n % 2 == 0 else n - 1


def resolve_output_canvas(source_paths: list[Path]) -> tuple[int, int]:
    """
    Match source resolution: use the *minimum* width/height among inputs
    (never upscale small phone footage to 1080).
    """
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
                print(f"  源分辨率 {p.name}: {w}x{h}")
        except Exception as exc:
            print(f"  警告: 无法探测 {p.name}: {exc}")
    if not widths:
        return 540, 960
    return _even(min(widths)), _even(min(heights))


def create_floor_badge(
    floor_label: str, out_png: Path, font_path: str, *, canvas_w: int
) -> None:
    """生成带有金边半透明气泡效果的楼层说明角标 PNG（随成片宽度缩放）。"""
    # ~36px at 1080w → scale down for 540w
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


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    floor_segments = [
        {"floor": "1F", "video": "1-包括前花园后花园.mp4", "badge": "1F | 首层大厅客厅"},
        {"floor": "2F", "video": "2b.mp4", "badge": "2F | 奢华居住套房"},
        {"floor": "3F", "video": "3b-主人房.mp4", "badge": "3F | 主卧套房露台"},
        {"floor": "-1F", "video": "-1a.mp4", "badge": "-1F | 地下采光夹层"},
        {"floor": "-2F", "video": "-2.mp4", "badge": "-2F | 地下车库多功能厅"},
    ]

    work_base = root / "frames" / "villa_master"
    work_base.mkdir(parents=True, exist_ok=True)
    badge_dir = work_base / "badges"
    badge_dir.mkdir(exist_ok=True)

    cfg_base = load_config(root / "config.yaml")
    font_path = resolve_font(cfg_base, root / "assets")

    src_paths = [root / item["video"] for item in floor_segments]
    print("==========================================")
    print(" 输出画布：对齐源素材（取最小宽高，不升采样）")
    print("==========================================")
    out_w, out_h = resolve_output_canvas(src_paths)
    print(f">>> 成片画布: {out_w}x{out_h}")

    print("\n==========================================")
    print(" 阶段 1：逐楼层画面 CV 分析与智能变速剪辑")
    print("==========================================")

    edited_clips = []
    for item in floor_segments:
        floor = item["floor"]
        src = root / item["video"]
        print(f"\n>>> 处理楼层 [{floor}] 素材: {src.name}")

        res = run_pipeline(
            input_path=src,
            config_path=root / "config.yaml",
            pack=False,
            dry_run=False,
        )

        speed_raw = res.work_dir / "speed_raw.mp4"
        if not speed_raw.is_file():
            speed_raw = res.output_path

        edited_clips.append({
            "floor": floor,
            "path": speed_raw,
            "badge_text": item["badge"],
        })
        print(f"  └─ [{floor}] 变速完成: {speed_raw} (时长: {res.duration_out:.2f}s)")

    print("\n==========================================")
    print(" 阶段 2：楼层说明贴纸叠加 + 黑转拼接（源分辨率）")
    print("==========================================")

    norm_dir = work_base / "normalized"
    norm_dir.mkdir(exist_ok=True)

    badge_x = max(16, int(50 * (out_w / 1080.0)))
    badge_y = max(24, int(70 * (out_h / 1920.0)))

    norm_clips = []
    for idx, item in enumerate(edited_clips):
        floor = item["floor"]
        src_path = item["path"]
        badge_text = item["badge_text"]
        dur = probe_duration(src_path)

        badge_png = badge_dir / f"badge_{floor}.png"
        create_floor_badge(badge_text, badge_png, font_path, canvas_w=out_w)

        out_path = norm_dir / f"norm_{idx}_{floor}.mp4"

        fade_in_len = 0.25 if idx > 0 else 0.0
        fade_out_len = 0.25 if idx < len(edited_clips) - 1 else 0.0

        # 不升采样：scale 到画布内，不足则 pad（与最小源一致时通常几乎无黑边）
        vfilters = [
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease",
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
            "fps=30",
            "format=yuv420p",
        ]
        if fade_in_len > 0:
            vfilters.append(f"fade=t=in:st=0:d={fade_in_len}")
        if fade_out_len > 0 and dur > fade_out_len:
            vfilters.append(f"fade=t=out:st={dur - fade_out_len:.3f}:d={fade_out_len}")

        vfilter_str = ",".join(vfilters)
        filter_complex = (
            f"[0:v]{vfilter_str}[bg];"
            f"[bg][1:v]overlay=x={badge_x}:y={badge_y}:format=auto[outv]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(src_path),
            "-i", str(badge_png),
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            str(out_path),
        ]
        print(f"  拼接贴纸 [{badge_text}] ➔ {out_path.name} ({out_w}x{out_h} crf23)")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        norm_clips.append(out_path)

    concat_list_path = work_base / "concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for p in norm_clips:
            f.write(f"file '{p.resolve().as_posix()}'\n")

    concat_raw_path = work_base / "speed_raw_concat.mp4"
    print("\n>>> 拼接所有带楼层贴纸的片段 ->", concat_raw_path.name)
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-c", "copy",
        str(concat_raw_path),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    concat_dur = probe_duration(concat_raw_path)
    concat_mb = concat_raw_path.stat().st_size / (1024 * 1024)
    print(f"  拼接完成！总时长: {concat_dur:.2f} 秒  体积: {concat_mb:.2f} MB")

    print("\n==========================================")
    print(" 阶段 3：包装成片 (花字 + BGM，分辨率跟输入)")
    print("==========================================")

    merged_cfg = load_config(root / "config.yaml")
    # Pack 侧编码：跟源体量，避免二次虚大
    merged_cfg.setdefault("encode", {})
    merged_cfg["encode"]["match_source"] = False
    merged_cfg["encode"]["crf"] = 23
    merged_cfg["encode"]["preset"] = "medium"
    merged_cfg["encode"]["pixel_format"] = "yuv420p"
    merged_cfg["encode"]["video_codec"] = "libx264"

    merged_cfg["pack"]["style"] = "douyin_estate"
    merged_cfg["pack"]["text"] = {
        "title": "奢华5层双拼别墅",
        "highlights": [
            "5层立体空间",
            "私家花园",
            "地下多功能厅",
        ],
        "price": "详情私信了解",
    }
    merged_cfg["pack"]["layout"] = {
        "y_rel": 0.255,
        "max_width_rel": 0.88,
        "highlights_mode": "join",
    }
    merged_cfg["pack"]["text_motion"] = {
        "enter": "fade",
        "duration": 0.35,
        "sparkle_anim": True,
    }
    merged_cfg["pack"]["sticker"] = {
        "style": "dm_estate_cta",
        "text": "私信了解",
        "enter": "slide_up",
        "enter_ms": 280,
        "start": 4.5,
        "duration": 2.5,
        "repeat_at_end": True,
        "end_lead": 2.8,
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
    }
    merged_cfg["pack"]["audio"] = {
        "bgm": "a7",
        "volume": 0.80,
    }

    final_output = root / "villa_master_edited2.mp4"
    print(f">>> 渲染成片: {final_output.name} ({out_w}x{out_h})")
    out_dur = pack_video(
        input_path=concat_raw_path,
        output_path=final_output,
        cfg=merged_cfg,
        work_dir=work_base,
    )
    final_mb = final_output.stat().st_size / (1024 * 1024)

    print("\n==========================================")
    print(" 阶段 4：轻量版（同分辨率 CRF 再压一档，不硬卡 14MB@高清）")
    print("==========================================")

    compressed_output = root / "villa_master_compressed2.mp4"
    # 同画布再压一档，体积自然贴近源片量级；可微信传输且不毁画质
    cmd_compress = [
        "ffmpeg", "-y",
        "-i", str(final_output),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        str(compressed_output),
    ]
    print(f">>> 轻量版: {compressed_output.name} (crf=26, {out_w}x{out_h})")
    subprocess.run(cmd_compress, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    comp_mb = compressed_output.stat().st_size / (1024 * 1024)

    print("\n==========================================")
    print(" SUCCESS: 别墅整体带看导出完成")
    print(f" 画布: {out_w}x{out_h}（对齐源最小分辨率）")
    print(f" 1. 成片: {final_output.resolve()}")
    print(f"    {out_dur:.2f}s  {final_mb:.2f} MB")
    print(f" 2. 轻量: {compressed_output.resolve()}")
    print(f"    {comp_mb:.2f} MB")
    print("==========================================")


if __name__ == "__main__":
    main()
