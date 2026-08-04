#!/usr/bin/env python3
"""
黄埔悦辰壹号 · 双片精剪成片

源素材：
  234.mp4  小区外景开场（全保留）
  456.mp4  室内带看（删入户/过道过渡，裁短出点）

流程：裁切保留段 → 单片智能变速 → 竖屏归一 + 黑转拼接 → 分时段花字包装
  片头副标题：小区绿化好
  室内副标题：107㎡舒适户型 · 黄埔刚需优选
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from copy import deepcopy

from walkthrough_edit.config import load_config
from walkthrough_edit.pipeline import run_pipeline
from walkthrough_edit.pack import (
    assets_root,
    compute_sticker_windows,
    render_title_overlay,
    resolve_font,
    _add_price_sparkles,
    _encode_png_sequence_apng,
    _render_estate_overlay,
    _resolve_sticker_path,
)
from walkthrough_edit.music_catalog import resolve_bgm_path, DEFAULT_BGM
from walkthrough_edit.render import (
    _filter_complex_file_args,
    _replace_with_retry,
    ensure_encoder,
    probe_duration,
    probe_media,
    select_video_codec,
)
from walkthrough_edit.text_styles import get_style


# 456 保留段（源时间轴，秒）—— 删入户/过渡，裁掉出点尾段
KEEP_RANGES_456: list[tuple[float, float]] = [
    (9.80, 18.40),   # 主空间扫视
    (20.70, 30.00),  # 室内连续带看
    (33.20, 38.50),  # 后段房间（原 41.5s 出点裁掉约 3s）
]

OUT_W, OUT_H = 1080, 1920  # 竖屏成片，不升采样
FADE = 0.25

# 片头外景约 4.5s；副标题在此切换
# 默认白字副标题叠在明亮外景上几乎看不见，片头用高对比描边单独渲染
TITLE = "黄埔悦辰壹号"
INTRO_SUBTITLE = "小区绿意满满"  # 片头：顺口、好记
MAIN_HIGHLIGHTS = ["107㎡住得舒服", "黄埔刚需好房"]
PRICE = "总价160万"


def _run(cmd: list[str], *, quiet: bool = True) -> None:
    kwargs: dict = {}
    if quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    subprocess.run(cmd, check=True, **kwargs)


def build_trimmed_456(src: Path, out: Path, work: Path) -> Path:
    """Concat keep-ranges from 456 into a single intermediate (portrait-safe)."""
    parts_dir = work / "keep_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []

    for i, (t0, t1) in enumerate(KEEP_RANGES_456):
        part = parts_dir / f"part_{i:02d}.mp4"
        dur = max(0.05, t1 - t0)
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t0:.3f}",
            "-i", str(src),
            "-t", f"{dur:.3f}",
            "-vf", (
                f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
                f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,fps=30,format=yuv420p"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(part),
        ]
        print(f"  裁切 456 [{t0:.2f}-{t1:.2f}s] -> {part.name}")
        _run(cmd)
        part_paths.append(part)

    concat_list = work / "keep_concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in part_paths:
            f.write(f"file '{p.resolve().as_posix()}'\n")

    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(out),
    ])
    print(f"  保留段拼接完成: {out.name} ({probe_duration(out):.2f}s)")
    return out


def normalize_clip(
    src: Path,
    out: Path,
    *,
    fade_in: float,
    fade_out: float,
) -> None:
    dur = probe_duration(src)
    vfilters = [
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease",
        f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
        "fps=30",
        "format=yuv420p",
    ]
    if fade_in > 0:
        vfilters.append(f"fade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0 and dur > fade_out:
        vfilters.append(f"fade=t=out:st={dur - fade_out:.3f}:d={fade_out:.3f}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", ",".join(vfilters),
        "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    _run(cmd)


def _outdoor_highlight_style() -> dict[str, Any]:
    """
    片头外景副标题：柔和深灰蓝字 + 薄奶白描边（不刺眼）。
    再配合半透明浅底托，叠在绿化/天空上也能一眼看清。
    """
    return {
        "font_size_rel": 0.074,
        "fill": (36, 48, 68, 255),             # 柔和深灰蓝，不纯黑
        "fill_bottom": (52, 68, 92, 255),
        "stroke_outer": (255, 252, 245, 245), # 奶白外描
        "stroke_inner": (230, 236, 245, 160),
        "stroke_outer_rel": 0.095,
        "stroke_inner_rel": 0.028,
        "glow": (255, 255, 255, 70),
        "glow_rel": 0.018,
    }


def _add_soft_band_plate(img: "Image.Image", *, band_index: int = 1) -> "Image.Image":
    """在指定文字带下方加半透明圆角底托（默认第二行=副标题）。"""
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np

    rgba = img.convert("RGBA")
    alpha = np.array(rgba.split()[-1])
    row = (alpha > 18).mean(axis=1)
    active = row > 0.008
    bands: list[tuple[int, int]] = []
    in_b = False
    start = 0
    for i, on in enumerate(active):
        if on and not in_b:
            start = i
            in_b = True
        elif not on and in_b:
            bands.append((start, i - 1))
            in_b = False
    if in_b:
        bands.append((start, len(active) - 1))
    if len(bands) <= band_index:
        return rgba

    y0, y1 = bands[band_index]
    band = alpha[y0 : y1 + 1]
    cols = (band > 18).mean(axis=0)
    xs = np.where(cols > 0.02)[0]
    if len(xs) == 0:
        return rgba
    x0, x1 = int(xs[0]), int(xs[-1])
    pad_x = max(18, (x1 - x0) // 12)
    pad_y = max(10, (y1 - y0) // 5)
    plate = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    box = (x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y)
    # 浅雾白底托：护眼、不抢画面
    draw.rounded_rectangle(
        box,
        radius=max(12, (y1 - y0 + 2 * pad_y) // 2),
        fill=(248, 250, 252, 165),
    )
    plate = plate.filter(ImageFilter.GaussianBlur(radius=1.2))
    return Image.alpha_composite(plate, rgba)


def _render_phase_title(
    *,
    work_dir: Path,
    name: str,
    title: str,
    highlights: list[str],
    price: str,
    style_name: str,
    width: int,
    height: int,
    font_path: str,
    layout: dict[str, Any],
    animate: bool,
    anim_fps: int,
    anim_frames: int,
    outdoor_readable: bool = False,
) -> Path | None:
    content = {
        "title": title,
        "highlights": highlights,
        "price": price,
    }
    lines = [title, *highlights, price]
    lines = [x for x in lines if str(x).strip()]
    out = work_dir / f"pack_title_{name}.png"

    if not outdoor_readable:
        return render_title_overlay(
            lines,
            style_name,
            width,
            height,
            font_path,
            out,
            layout=layout,
            content=content,
            animate=animate,
            anim_fps=anim_fps,
            anim_frames=anim_frames,
        )

    # 片头专用：改 highlights 配色 + 浅底托，再走 estate 渲染
    style = deepcopy(get_style(style_name))
    roles = dict(style.get("roles") or {})
    roles["highlights"] = _outdoor_highlight_style()
    style["roles"] = roles

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = max(6, int(anim_frames))
    fps = max(6, int(anim_fps))
    if animate:
        base, targets = _render_estate_overlay(
            content,
            style,
            width,
            height,
            font_path,
            layout,
            draw_sparkles=False,
            return_sparkle_targets=True,
        )
        base = _add_soft_band_plate(base, band_index=1)
        frames_dir = out.parent / f".title_frames_{name}"
        frames_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        try:
            for i in range(n):
                frame = base.copy()
                for glyph, dest in targets:
                    _add_price_sparkles(frame, glyph, dest, phase=i / n)
                p = frames_dir / f"f_{i:03d}.png"
                frame.save(p, "PNG")
                paths.append(p)
            apng_path = out.with_suffix(".apng")
            _encode_png_sequence_apng(paths, apng_path, fps=fps)
            base.save(out, "PNG")
            return apng_path
        finally:
            for p in paths:
                p.unlink(missing_ok=True)
            try:
                frames_dir.rmdir()
            except OSError:
                pass

    img = _render_estate_overlay(
        content, style, width, height, font_path, layout
    )
    img = _add_soft_band_plate(img, band_index=1)
    img.save(out, "PNG")
    return out


def pack_dual_subtitle(
    input_path: Path,
    output_path: Path,
    cfg: dict[str, Any],
    work_dir: Path,
    *,
    intro_end: float,
    project_root: Path,
) -> float:
    """
    Full pack with two title phases:
      [0, intro_end)  title + 片头副标题（小区绿化）
      [intro_end, end] title + 室内卖点副标题 + 价格
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pack = cfg.get("pack") or {}
    assets = assets_root(cfg, project_root)
    media = probe_media(input_path)
    width = int(media["width"] or 0)
    height = int(media["height"] or 0)
    duration = float(media["duration"] or probe_duration(input_path))
    if width <= 0 or height <= 0 or duration <= 0:
        raise RuntimeError(f"Cannot probe pack input: {input_path}")

    intro_end = max(0.8, min(float(intro_end), duration - 0.5))
    style_name = str(pack.get("style") or "douyin_estate")
    font_path = resolve_font(cfg, assets)
    layout = pack.get("layout") if isinstance(pack.get("layout"), dict) else {}
    text_motion = pack.get("text_motion") if isinstance(pack.get("text_motion"), dict) else {}
    animate = bool(text_motion.get("sparkle_anim", True))
    anim_fps = int(float(text_motion.get("sparkle_fps", 10)))
    anim_frames = int(float(text_motion.get("sparkle_frames", 10)))
    enter_dur = max(0.05, float(text_motion.get("duration", 0.35)))
    cross = min(0.40, enter_dur)

    intro_path = _render_phase_title(
        work_dir=work_dir,
        name="intro",
        title=TITLE,
        highlights=[INTRO_SUBTITLE],
        price=PRICE,
        style_name=style_name,
        width=width,
        height=height,
        font_path=font_path,
        layout=layout,
        animate=animate,
        anim_fps=anim_fps,
        anim_frames=anim_frames,
        outdoor_readable=True,  # 外景白底看不见 → 黄字黑边
    )
    main_path = _render_phase_title(
        work_dir=work_dir,
        name="main",
        title=TITLE,
        highlights=list(MAIN_HIGHLIGHTS),
        price=PRICE,
        style_name=style_name,
        width=width,
        height=height,
        font_path=font_path,
        layout=layout,
        animate=animate,
        anim_fps=anim_fps,
        anim_frames=anim_frames,
    )
    if intro_path is None or main_path is None:
        raise RuntimeError("title overlay render failed")

    sticker_info = _resolve_sticker_path(cfg, assets, work_dir=work_dir)
    sticker_cfg = pack.get("sticker") or {}
    sticker_windows = compute_sticker_windows(duration, sticker_cfg)

    audio_cfg = pack.get("audio") or {}
    bgm = str(audio_cfg.get("bgm") or DEFAULT_BGM).strip() or DEFAULT_BGM
    bgm_path = resolve_bgm_path(bgm, assets)
    bgm_volume = float(audio_cfg.get("volume", 0.85))
    fade_in = float(audio_cfg.get("fade_in", 0.5))
    fade_out = float(audio_cfg.get("fade_out", 0.8))

    inputs: list[str] = ["-i", str(input_path)]
    next_idx = 1

    def _add_title_input(path: Path) -> int:
        nonlocal next_idx
        if path.suffix.lower() == ".apng":
            inputs.extend(["-stream_loop", "-1", "-i", str(path)])
        else:
            inputs.extend(["-loop", "1", "-i", str(path)])
        idx = next_idx
        next_idx += 1
        return idx

    intro_idx = _add_title_input(intro_path)
    main_idx = _add_title_input(main_path)

    sticker_idx = None
    sticker_meta: dict[str, Any] = {}
    if sticker_info is not None:
        sticker_path, sticker_meta = sticker_info
        inputs.extend(["-stream_loop", "-1", "-i", str(sticker_path)])
        sticker_idx = next_idx
        next_idx += 1

    inputs.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
    bgm_idx = next_idx

    filter_parts: list[str] = []
    current = "[0:v]"
    vlabel = 0

    # --- intro title: show until intro_end, fade out near switch ---
    intro_fade_out_st = max(0.0, intro_end - cross)
    intro_stream = f"[{intro_idx}:v]"
    intro_prep = ["format=rgba"]
    if intro_path.suffix.lower() == ".apng":
        intro_prep.insert(0, "fps=10")
    intro_prep.append(f"fade=t=in:st=0:d={enter_dur:.3f}:alpha=1")
    intro_prep.append(
        f"fade=t=out:st={intro_fade_out_st:.3f}:d={cross:.3f}:alpha=1"
    )
    filter_parts.append(f"{intro_stream}{','.join(intro_prep)}[title_intro]")
    out = f"[v{vlabel}]"
    filter_parts.append(
        f"{current}[title_intro]overlay=0:0:format=auto:"
        f"enable='lt(t\\,{intro_end:.3f})'{out}"
    )
    current = out
    vlabel += 1

    # --- main title: from intro_end to end ---
    main_stream = f"[{main_idx}:v]"
    main_prep = ["format=rgba"]
    if main_path.suffix.lower() == ".apng":
        main_prep.insert(0, "fps=10")
    # local fade-in after setpts shift
    main_prep.append(f"fade=t=in:st=0:d={enter_dur:.3f}:alpha=1")
    main_prep.append(f"setpts=PTS+{intro_end:.3f}/TB")
    filter_parts.append(f"{main_stream}{','.join(main_prep)}[title_main]")
    out = f"[v{vlabel}]"
    filter_parts.append(
        f"{current}[title_main]overlay=0:0:format=auto:"
        f"enable='gte(t\\,{intro_end:.3f})'{out}"
    )
    current = out
    vlabel += 1

    # --- sticker (same as pack_video) ---
    if sticker_idx is not None and sticker_windows:
        sw = max(32, int(width * float(sticker_meta["width_rel"])))
        if sw % 2:
            sw += 1
        sx = float(sticker_meta["x"])
        sy = float(sticker_meta["y"])
        sticker_fps = max(6, int(float(sticker_cfg.get("fps", 12))))
        enter = str(sticker_cfg.get("enter") or "slide_up").strip().lower()
        if enter not in {"none", "pop", "slide_up"}:
            enter = "slide_up"
        enter_ms = max(80, int(float(sticker_cfg.get("enter_ms", 280))))
        enter_s = enter_ms / 1000.0
        ox = f"(main_w*{sx:.4f})-(overlay_w/2)"
        base_labels = [f"[cta_base_{i}]" for i in range(len(sticker_windows))]
        split = "" if len(base_labels) == 1 else f",split={len(base_labels)}"
        outputs = base_labels[0] if len(base_labels) == 1 else "".join(base_labels)
        filter_parts.append(
            f"[{sticker_idx}:v]fps={sticker_fps},scale={sw}:-1:flags=lanczos,format=rgba"
            f"{split}{outputs}"
        )
        for i, (window_start, window_end) in enumerate(sticker_windows):
            window_duration = max(0.1, window_end - window_start)
            fade_out_start = max(0.18, window_duration - 0.30)
            timed = f"[cta_{i}]"
            chain = [
                f"trim=duration={window_duration:.3f}",
                "setpts=PTS-STARTPTS",
                "fade=t=in:st=0:d=0.180:alpha=1",
                f"fade=t=out:st={fade_out_start:.3f}:d=0.300:alpha=1",
                f"setpts=PTS+{window_start:.3f}/TB",
            ]
            filter_parts.append(f"{base_labels[i]}{','.join(chain)}{timed}")
            if enter == "slide_up":
                oy = (
                    f"(main_h*{sy:.4f})-(overlay_h/2)+"
                    f"if(lt(t-{window_start:.3f}\\,{enter_s:.3f})\\,"
                    f"28*(1-(t-{window_start:.3f})/{enter_s:.3f})"
                    f"*(1-(t-{window_start:.3f})/{enter_s:.3f})\\,0)"
                )
            else:
                oy = f"(main_h*{sy:.4f})-(overlay_h/2)"
            out = f"[v{vlabel}]"
            filter_parts.append(
                f"{current}{timed}overlay=x='{ox}':y='{oy}':"
                f"enable='between(t,{window_start:.3f},{window_end:.3f})':"
                f"eof_action=pass:repeatlast=0:format=auto{out}"
            )
            current = out
            vlabel += 1

    filter_parts.append(f"{current}format=yuv420p[outv]")

    afades = [f"volume={bgm_volume:.4f}"]
    if fade_in > 0:
        afades.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0 and duration > fade_out:
        afades.append(
            f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
        )
    filter_parts.append(
        f"[{bgm_idx}:a]atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,"
        f"{','.join(afades)}[outa]"
    )

    filter_script = work_dir / "pack_filter_complex.txt"
    filter_script.write_text(";\n".join(filter_parts), encoding="utf-8")

    enc = cfg.get("encode") or {}
    vcodec = select_video_codec(
        str(media.get("video_codec") or "h264"),
        str(enc.get("video_codec", "auto")),
    )
    ensure_encoder(vcodec)
    preset = str(enc.get("preset", "medium"))
    pix = str(enc.get("pixel_format", "yuv420p"))
    crf = int(float(enc.get("crf", 23)))

    temporary = output_path.with_name(
        f".{output_path.stem}.pack.part{output_path.suffix}"
    )
    temporary.unlink(missing_ok=True)

    cmd = [
        "ffmpeg", "-y", "-nostdin",
        *inputs,
        *_filter_complex_file_args(filter_script),
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", vcodec,
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", pix,
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", f"{duration:.6f}",
        "-movflags", str(enc.get("movflags", "+faststart")),
        str(temporary),
    ]
    print(
        f"  pack dual: intro[0-{intro_end:.1f}s]=「{INTRO_SUBTITLE}」 "
        f"main=「{' · '.join(MAIN_HIGHLIGHTS)}」 bgm={bgm_path.name}"
    )
    _run(cmd, quiet=False)
    _replace_with_retry(temporary, output_path)
    return probe_duration(output_path)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    pack_only = "--pack-only" in sys.argv

    src_ext = root / "234.mp4"
    src_in = root / "456.mp4"
    if not src_ext.is_file() or not src_in.is_file():
        raise SystemExit("需要根目录 234.mp4 与 456.mp4")

    work = root / "frames" / "yuechen_master"
    work.mkdir(parents=True, exist_ok=True)
    cfg_path = root / "config.yaml"
    concat_raw = work / "speed_raw_concat.mp4"
    intro_end = 4.5  # 外景变速后时长；pack-only 时若有归一片段则覆盖

    print("==========================================")
    print(" 黄埔悦辰壹号 · 精剪规划")
    print("==========================================")
    keep_src = sum(b - a for a, b in KEEP_RANGES_456)
    print(f"  外景 234: 全保留 6.0s")
    print(f"  室内 456: 保留 {keep_src:.1f}s / 原 41.5s（删入户过渡+出点）")
    for t0, t1 in KEEP_RANGES_456:
        print(f"    keep {t0:.2f}-{t1:.2f}s")
    print(f"  画布: {OUT_W}x{OUT_H} 竖屏")
    print(f"  片头副标题: {INTRO_SUBTITLE}")
    print(f"  室内副标题: {' · '.join(MAIN_HIGHLIGHTS)}")

    if pack_only and concat_raw.is_file():
        print("\n[pack-only] 复用已有 speed_raw_concat.mp4")
        ext_norm = work / "normalized" / "norm_0_ext.mp4"
        if ext_norm.is_file():
            intro_end = probe_duration(ext_norm)
            print(f"  片头切换点: {intro_end:.2f}s（外景段时长）")
    else:
        # --- Stage 0: trim 456 ---
        print("\n==========================================")
        print(" 阶段 0：裁切室内保留段")
        print("==========================================")
        trimmed_456 = work / "456_kept.mp4"
        build_trimmed_456(src_in, trimmed_456, work)

        # --- Stage 1: speed edit ---
        print("\n==========================================")
        print(" 阶段 1：智能变速（外景 + 裁切后室内）")
        print("==========================================")

        print("\n>>> 外景 234.mp4")
        res_ext = run_pipeline(
            input_path=src_ext,
            config_path=cfg_path,
            pack=False,
            dry_run=False,
        )
        speed_ext = res_ext.work_dir / "speed_raw.mp4"
        if not speed_ext.is_file():
            speed_ext = res_ext.output_path
        intro_end = float(res_ext.duration_out or probe_duration(speed_ext))
        print(f"  外景变速: {intro_end:.2f}s -> {speed_ext}")

        print("\n>>> 室内 456_kept.mp4")
        res_in = run_pipeline(
            input_path=trimmed_456,
            config_path=cfg_path,
            pack=False,
            dry_run=False,
            output_path=work / "456_kept_edited.mp4",
        )
        speed_in = res_in.work_dir / "speed_raw.mp4"
        if not speed_in.is_file():
            speed_in = res_in.output_path
        print(f"  室内变速: {res_in.duration_out:.2f}s -> {speed_in}")

        # --- Stage 2: normalize + fade + concat ---
        print("\n==========================================")
        print(" 阶段 2：竖屏归一 + 黑转拼接")
        print("==========================================")
        norm_dir = work / "normalized"
        norm_dir.mkdir(exist_ok=True)

        clips = [
            ("ext", speed_ext, 0.0, FADE),
            ("int", speed_in, FADE, 0.0),
        ]
        norm_paths: list[Path] = []
        for idx, (name, path, fin, fout) in enumerate(clips):
            out = norm_dir / f"norm_{idx}_{name}.mp4"
            print(f"  归一 [{name}] fade_in={fin} fade_out={fout}")
            normalize_clip(path, out, fade_in=fin, fade_out=fout)
            norm_paths.append(out)

        intro_end = probe_duration(norm_paths[0])
        concat_list = work / "concat_list.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in norm_paths:
                f.write(f"file '{p.resolve().as_posix()}'\n")

        _run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(concat_raw),
        ])
        print(f"  拼接完成: {concat_raw.name}  {probe_duration(concat_raw):.2f}s")
        print(f"  片头切换点: {intro_end:.2f}s")

    # --- Stage 3: pack with dual subtitles ---
    print("\n==========================================")
    print(" 阶段 3：包装（分时段花字 + 贴纸 + BGM）")
    print("==========================================")
    cfg = load_config(cfg_path)
    cfg.setdefault("encode", {})
    cfg["encode"]["match_source"] = False
    cfg["encode"]["crf"] = 23
    cfg["encode"]["preset"] = "medium"
    cfg["encode"]["pixel_format"] = "yuv420p"
    cfg["encode"]["video_codec"] = "libx264"

    cfg.setdefault("pack", {})
    cfg["pack"]["enabled"] = True
    cfg["pack"]["style"] = "douyin_estate"
    cfg["pack"]["layout"] = {
        "y_rel": 0.255,
        "max_width_rel": 0.88,
        "highlights_mode": "join",
    }
    cfg["pack"]["text_motion"] = {
        "enter": "fade",
        "duration": 0.35,
        "sparkle_anim": True,
    }
    cfg["pack"]["sticker"] = {
        "enabled": True,
        "style": "dm_estate_cta",
        "text": "私信了解",
        "enter": "slide_up",
        "enter_ms": 280,
        "start": 3.5,
        "duration": 2.5,
        "repeat_at_end": True,
        "end_lead": 2.5,
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
    }
    cfg["pack"]["audio"] = {
        "bgm": "a1",
        "volume": 0.80,
        "fade_in": 0.5,
        "fade_out": 0.8,
    }

    final_output = root / "黄埔悦辰壹号_edited.mp4"
    print(f">>> 成片: {final_output.name}")
    out_dur = pack_dual_subtitle(
        input_path=concat_raw,
        output_path=final_output,
        cfg=cfg,
        work_dir=work,
        intro_end=intro_end,
        project_root=root,
    )
    final_mb = final_output.stat().st_size / (1024 * 1024)

    light = root / "黄埔悦辰壹号_light.mp4"
    print(f">>> 轻量版: {light.name} (crf=26)")
    _run([
        "ffmpeg", "-y",
        "-i", str(final_output),
        "-c:v", "libx264", "-preset", "medium", "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        str(light),
    ], quiet=False)
    light_mb = light.stat().st_size / (1024 * 1024)

    print("\n==========================================")
    print(" SUCCESS")
    print(f"  画布: {OUT_W}x{OUT_H}")
    print(f"  成片: {final_output.resolve()}")
    print(f"       {out_dur:.2f}s  {final_mb:.1f} MB")
    print(f"  轻量: {light.resolve()}")
    print(f"       {light_mb:.1f} MB")
    print(f"  片头 0–{intro_end:.1f}s: {TITLE} / {INTRO_SUBTITLE} / {PRICE}")
    print(
        f"  之后: {TITLE} / {' · '.join(MAIN_HIGHLIGHTS)} / {PRICE}"
    )
    print("==========================================")


if __name__ == "__main__":
    main()
