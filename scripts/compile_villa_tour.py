#!/usr/bin/env python3
"""
别墅全楼层带看视频 · 自动变速精选、楼层角标、黑转拼接与全套包装脚本

楼层带看顺序：1F ➔ 2F ➔ 3F ➔ -1F ➔ -2F
"""
import json
import os
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
from walkthrough_edit.render import probe_duration
from PIL import Image, ImageDraw, ImageFont


def create_floor_badge(floor_label: str, out_png: Path, font_path: str) -> None:
    """生成带有金边半透明气泡效果的楼层说明角标 PNG 图片。"""
    font_size = 36
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    dummy = Image.new("RGBA", (1, 1))
    draw_dummy = ImageDraw.Draw(dummy)
    bbox = draw_dummy.textbbox((0, 0), floor_label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    px, py = 28, 14
    w, h = tw + px * 2, th + py * 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 深色半透明底 + 亮橙金边框
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1],
        radius=h // 2,
        fill=(18, 22, 36, 210),
        outline=(255, 185, 45, 240),
        width=3,
    )
    # 高清白字
    draw.text((px, py - 2), floor_label, font=font, fill=(255, 255, 255, 255))
    img.save(out_png, "PNG")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # Target floor sequence in user approved order with floor stickers
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

    print("==========================================")
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
            "badge_text": item["badge"]
        })
        print(f"  └─ [{floor}] 变速完成: {speed_raw} (时长: {res.duration_out:.2f}s)")

    print("\n==========================================")
    print(" 阶段 2：楼层说明贴纸叠加 + 0.5s 优雅黑转拼接")
    print("==========================================")

    norm_dir = work_base / "normalized"
    norm_dir.mkdir(exist_ok=True)

    norm_clips = []
    for idx, item in enumerate(edited_clips):
        floor = item["floor"]
        src_path = item["path"]
        badge_text = item["badge_text"]
        dur = probe_duration(src_path)

        # 生成楼层说明角标贴纸
        badge_png = badge_dir / f"badge_{floor}.png"
        create_floor_badge(badge_text, badge_png, font_path)

        out_path = norm_dir / f"norm_{idx}_{floor}.mp4"

        # 淡入淡出黑转参数
        fade_in_len = 0.25 if idx > 0 else 0.0
        fade_out_len = 0.25 if idx < len(edited_clips) - 1 else 0.0

        # FFmpeg filter: 缩放裁剪 + 贴上左上角楼层说明角标 (x=50, y=70) + 黑转
        vfilters = [
            "scale=1080:1920:force_original_aspect_ratio=decrease",
            "pad=1080:1920:(1080-iw)/2:(1920-ih)/2:color=black",
            "setsar=1",
            "fps=30",
        ]

        if fade_in_len > 0:
            vfilters.append(f"fade=t=in:st=0:d={fade_in_len}")
        if fade_out_len > 0 and dur > fade_out_len:
            vfilters.append(f"fade=t=out:st={dur - fade_out_len:.3f}:d={fade_out_len}")

        vfilter_str = ",".join(vfilters)
        # 链式叠加楼层贴纸
        filter_complex = f"[0:v]{vfilter_str}[bg];[bg][1:v]overlay=x=50:y=70:format=auto[outv]"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(src_path),
            "-i", str(badge_png),
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-an",
            str(out_path)
        ]
        print(f"  拼接贴纸 [{badge_text}] ➔ {out_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        norm_clips.append(out_path)

    # Concatenate normalized clips with floor stickers burned in
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
        str(concat_raw_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    concat_dur = probe_duration(concat_raw_path)
    print(f"  拼接完成！总时长: {concat_dur:.2f} 秒")

    print("\n==========================================")
    print(" 阶段 3：更新视频包装 (修改文案 + 更换 BGM)")
    print("==========================================")

    merged_cfg = load_config(root / "config.yaml")
    merged_cfg["pack"]["style"] = "douyin_estate"
    merged_cfg["pack"]["text"] = {
        "title": "奢华5层双拼别墅",  # 修改首行标题
        "highlights": [
            "5层立体空间",
            "私家花园",
            "地下多功能厅"
        ],
        "price": "详情私信了解"       # 修改第三行文案
    }
    merged_cfg["pack"]["layout"] = {
        "y_rel": 0.255,
        "max_width_rel": 0.88,
        "highlights_mode": "join"
    }
    merged_cfg["pack"]["text_motion"] = {
        "enter": "fade",
        "duration": 0.35,
        "sparkle_anim": True
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
        "y": 0.90
    }
    merged_cfg["pack"]["audio"] = {
        "bgm": "a7",  # 优先使用 music 根目录下的本地音频文件 a7.m4a (182s 完整无缝长音轨)
        "volume": 0.80
    }

    final_output = root / "villa_master_edited2.mp4"
    print(f">>> 开始渲染全新精装原画成片: {final_output.name}")
    out_dur = pack_video(
        input_path=concat_raw_path,
        output_path=final_output,
        cfg=merged_cfg,
        work_dir=work_base,
    )

    print("\n==========================================")
    print(" 阶段 4：轻量压缩版本导出 (< 15MB 适合微信传输)")
    print("==========================================")

    compressed_output = root / "villa_master_compressed2.mp4"
    target_size_bytes = 14 * 1024 * 1024  # 14MB target
    target_total_bitrate = int((target_size_bytes * 8) / out_dur)
    audio_bitrate = 96000
    video_bitrate = max(200000, target_total_bitrate - audio_bitrate)

    cmd_compress = [
        "ffmpeg", "-y",
        "-i", str(final_output),
        "-c:v", "libx264",
        "-b:v", f"{video_bitrate}",
        "-maxrate", f"{int(video_bitrate * 1.2)}",
        "-bufsize", f"{int(video_bitrate * 2)}",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "96k",
        str(compressed_output)
    ]
    print(f">>> 开始压制轻量版本: {compressed_output.name} (目标码率: {video_bitrate//1000}k)")
    subprocess.run(cmd_compress, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    comp_size = compressed_output.stat().st_size / (1024 * 1024)

    print("\n==========================================")
    print(" SUCCESS: 别墅整体带看全流程剪辑与包装导出完成！")
    print(f" 1. 原画精装版: {final_output.resolve()} ({out_dur:.2f}s)")
    print(f" 2. 轻量压缩版: {compressed_output.resolve()} ({comp_size:.2f}MB)")
    print("==========================================")

if __name__ == "__main__":
    main()
