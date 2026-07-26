"""Stage-B packaging: full-video title, dynamic DM sticker, BGM-only audio."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .render import (
    _filter_complex_file_args,
    _replace_with_retry,
    ensure_encoder,
    probe_duration,
    probe_media,
    select_video_codec,
)
from .music_catalog import DEFAULT_BGM, resolve_bgm_path
from .stickers_gen import DEFAULT_STICKER, STICKER_SPECS, ensure_builtin_stickers
from .text_styles import DEFAULT_STYLE, get_style, list_styles


def assets_root(cfg: dict[str, Any], project_root: Path | None = None) -> Path:
    pack = cfg.get("pack") or {}
    raw = str(pack.get("assets_dir") or "assets")
    path = Path(raw)
    if path.is_absolute():
        return path
    base = project_root or Path.cwd()
    return (base / path).resolve()


def resolve_font(cfg: dict[str, Any], assets: Path) -> str:
    """Prefer pack font, then assets/fonts, then system Chinese fonts."""
    pack = cfg.get("pack") or {}
    configured = str(pack.get("fontfile") or "").strip()
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p.resolve())
        candidate = assets / "fonts" / configured
        if candidate.is_file():
            return str(candidate.resolve())
        raise FileNotFoundError(f"pack.fontfile not found: {configured}")

    # Prefer bold system faces first — closer to short-video 花字 than thin UI fonts.
    system = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    ]
    for p in system:
        if p.is_file():
            return str(p.resolve())

    font_dir = assets / "fonts"
    preferred_names = [
        "SmileySans-Oblique.ttf",
        "SmileySans.ttf",
        "NotoSansSC-Bold.otf",
        "SourceHanSansSC-Bold.otf",
        "LXGWWenKai-Regular.ttf",
    ]
    if font_dir.is_dir():
        for name in preferred_names:
            p = font_dir / name
            if p.is_file():
                return str(p.resolve())
        for p in sorted(font_dir.glob("*.*")):
            if p.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                return str(p.resolve())
    raise FileNotFoundError(
        "No Chinese font found. Put a .ttf/.otf under assets/fonts/ "
        "or set pack.fontfile"
    )


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        # Some TTC need index
        try:
            return ImageFont.truetype(path, size=size, index=0)
        except OSError:
            return ImageFont.load_default()


def _text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    *,
    stroke_width: int = 0,
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])


def _render_solid_line(
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    fill_bottom: tuple[int, int, int, int] | None,
    stroke_outer: tuple[int, int, int, int],
    stroke_inner: tuple[int, int, int, int],
    stroke_outer_r: int,
    stroke_inner_r: int,
    glow: tuple[int, int, int, int] | None,
    glow_r: int,
) -> Image.Image:
    """One continuous line (no tracking) via FreeType multi-stroke 花字."""
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    thick = max(stroke_outer_r, stroke_inner_r, glow_r)
    tw, th = _text_size(probe, text, font, stroke_width=thick)
    pad = thick + 8
    tile_w, tile_h = tw + pad * 2, th + pad * 2
    x, y = pad, pad
    canvas = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))

    if glow and glow_r > 0:
        glow_layer = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        gr, gg, gb, ga = glow
        col = (gr, gg, gb, min(255, max(ga, 80)))
        gd.text(
            (x, y),
            text,
            font=font,
            fill=col,
            stroke_width=stroke_outer_r + max(2, glow_r // 2),
            stroke_fill=col,
        )
        glow_layer = glow_layer.filter(
            ImageFilter.GaussianBlur(radius=max(1.5, glow_r * 0.4))
        )
        r, g, b, a = glow_layer.split()
        a = a.point(lambda p: int(p * 0.5))
        canvas = Image.alpha_composite(canvas, Image.merge("RGBA", (r, g, b, a)))

    draw = ImageDraw.Draw(canvas)
    if stroke_outer_r > 0:
        draw.text(
            (x, y),
            text,
            font=font,
            fill=stroke_outer,
            stroke_width=stroke_outer_r,
            stroke_fill=stroke_outer,
        )
    if stroke_inner_r > 0:
        draw.text(
            (x, y),
            text,
            font=font,
            fill=stroke_inner,
            stroke_width=stroke_inner_r,
            stroke_fill=stroke_inner,
        )

    if fill_bottom and fill_bottom[:3] != fill[:3]:
        mask = Image.new("L", (tile_w, tile_h), 0)
        ImageDraw.Draw(mask).text((x, y), text, font=font, fill=255)
        grad = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
        gp = grad.load()
        for row in range(tile_h):
            t = row / max(1, tile_h - 1)
            t = min(1.0, max(0.0, (t - 0.12) / 0.88))
            color = tuple(
                int(fill[i] * (1 - t) + fill_bottom[i] * t) for i in range(4)
            )
            for col in range(tile_w):
                gp[col, row] = color
        grad.putalpha(mask)
        canvas = Image.alpha_composite(canvas, grad)
    else:
        draw = ImageDraw.Draw(canvas)
        draw.text((x, y), text, font=font, fill=fill)
    return canvas


def _render_line_glyph(
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    fill_bottom: tuple[int, int, int, int] | None,
    stroke_outer: tuple[int, int, int, int],
    stroke_inner: tuple[int, int, int, int],
    stroke_outer_r: int,
    stroke_inner_r: int,
    glow: tuple[int, int, int, int] | None,
    glow_r: int,
    letter_spacing: int = 0,
) -> Image.Image:
    """
    One line via FreeType multi-stroke 花字.

    Slight tracking: render each complete multi-layer glyph, then join with
    a small gap (never stroke-per-layer with gaps — that caused blue mush).
    """
    if letter_spacing <= 0 or len(text) <= 1:
        return _render_solid_line(
            text,
            font,
            fill,
            fill_bottom,
            stroke_outer,
            stroke_inner,
            stroke_outer_r,
            stroke_inner_r,
            glow,
            glow_r,
        )

    chars = [ch for ch in text if ch.strip() or ch == " "]
    if not chars:
        return _render_solid_line(
            text,
            font,
            fill,
            fill_bottom,
            stroke_outer,
            stroke_inner,
            stroke_outer_r,
            stroke_inner_r,
            glow,
            glow_r,
        )

    tiles = [
        _render_solid_line(
            ch,
            font,
            fill,
            fill_bottom,
            stroke_outer,
            stroke_inner,
            stroke_outer_r,
            stroke_inner_r,
            glow,
            glow_r,
        )
        for ch in chars
    ]
    # Overlap strokes slightly so rings stay continuous; gap only adds a hair
    # of air between glyph boxes (negative pull-in + small spacing).
    pull = max(2, int(stroke_outer_r * 0.55))
    gap = max(0, letter_spacing - pull)
    total_w = sum(t.size[0] for t in tiles) + gap * (len(tiles) - 1) - pull * (len(tiles) - 1)
    # When pull > gap, advance is tile_w - pull + letter_spacing contribution
    advances: list[int] = []
    for i, t in enumerate(tiles):
        if i == 0:
            advances.append(0)
        else:
            prev = tiles[i - 1]
            # place next so stroke rings nearly touch, then add letter_spacing
            advances.append(advances[-1] + prev.size[0] - pull + letter_spacing)

    total_w = advances[-1] + tiles[-1].size[0]
    total_h = max(t.size[1] for t in tiles)
    canvas = Image.new("RGBA", (max(1, total_w), total_h), (0, 0, 0, 0))
    for t, ax in zip(tiles, advances):
        y = (total_h - t.size[1]) // 2
        canvas.alpha_composite(t, dest=(ax, y))
    return canvas


def collect_text_lines(pack: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    """Support lines[] or line1..line5 / meta fields."""
    text_cfg = pack.get("text") or {}
    raw_lines = text_cfg.get("lines")
    if isinstance(raw_lines, list) and any(str(x).strip() for x in raw_lines):
        return [str(x).strip() for x in raw_lines if str(x).strip()][:5]
    lines: list[str] = []
    for key in ("line1", "line2", "line3", "line4", "line5"):
        val = str(text_cfg.get(key) or "").strip()
        if val:
            lines.append(val)
    if lines:
        return lines[:5]
    for key in ("title", "subtitle", "price"):
        val = str(text_cfg.get(key) or "").strip()
        if val:
            lines.append(val)
    if lines:
        return lines[:5]
    meta = pack.get("meta") or cfg.get("meta") or {}
    for key in ("title", "subtitle", "price"):
        val = str(meta.get(key) or "").strip()
        if val:
            lines.append(val)
    return lines[:5]


def render_title_overlay(
    lines: list[str],
    style_name: str,
    width: int,
    height: int,
    font_path: str,
    out_path: Path,
    layout: dict[str, Any] | None = None,
) -> Path | None:
    """
    Full-frame transparent 花字 PNG.

    3× supersample + FreeType native multi-stroke, then LANCZOS downscale.
    """
    cleaned = [str(x).strip() for x in lines if str(x).strip()][:5]
    if not cleaned:
        return None
    style = get_style(style_name)
    layout = layout or {}

    ss = 3  # higher SS → sharper after shrink
    W, H = width * ss, height * ss
    short = min(W, H)

    max_width = int(W * float(layout.get("max_width_rel") or style.get("max_width_rel") or 0.92))
    size_rel = float(layout.get("font_size_rel") or style["font_size_rel"])
    font_size = max(30 * ss, int(short * size_rel))
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))

    # Extra tracking off by default — per-glyph spacing breaks continuous 花字 rings.
    # Prefer solid FreeType line; only use join path if letter_spacing_rel > 0.
    letter_spacing_rel = float(style.get("letter_spacing_rel", 0.0))

    # Fit including outer stroke budget
    while font_size > 22 * ss:
        font = _load_font(font_path, font_size)
        outer_try = max(3 * ss, int(font_size * float(style.get("stroke_outer_rel", 0.16))))
        gap = max(0, int(font_size * letter_spacing_rel))
        widest = 0
        for line in cleaned:
            base = _text_size(probe, line, font, stroke_width=outer_try)[0]
            extra = gap * max(0, len(line) - 1)
            widest = max(widest, base + extra)
        if widest <= max_width:
            break
        font_size -= ss

    font = _load_font(font_path, font_size)
    # layout.line_gap_rel overrides style; 0/negative = tighter (glyph tiles have pad)
    line_gap_rel = float(
        layout.get("line_gap_rel", style.get("line_gap_rel", -0.006))
    )
    letter_spacing = max(0, int(font_size * letter_spacing_rel))

    # Stroke widths: outer thick, inner clearly visible white ring
    stroke_outer_r = max(4 * ss, int(font_size * float(style.get("stroke_outer_rel", 0.16))))
    stroke_inner_r = max(2 * ss, int(font_size * float(style.get("stroke_inner_rel", 0.08))))
    if stroke_inner_r >= stroke_outer_r:
        stroke_inner_r = max(ss, stroke_outer_r - 2 * ss)
    glow_r = max(0, int(font_size * float(style.get("glow_rel", 0.04))))

    fill = tuple(style["fill"])
    fill_bottom = tuple(style["fill_bottom"]) if style.get("fill_bottom") else None
    outer = tuple(style["stroke_outer"])
    inner = tuple(style["stroke_inner"])
    glow = tuple(style["glow"]) if style.get("glow") else None

    glyphs = [
        _render_line_glyph(
            line,
            font,
            fill,
            fill_bottom,
            outer,
            inner,
            stroke_outer_r,
            stroke_inner_r,
            glow,
            glow_r,
            letter_spacing=letter_spacing,
        )
        for line in cleaned
    ]

    line_gap = int(short * line_gap_rel)
    if glyphs:
        # Don't pull more than ~28% of shortest glyph (avoids heavy overlap)
        min_gap = -int(min(g.size[1] for g in glyphs) * 0.28)
        line_gap = max(min_gap, line_gap)

    block_w = max(g.size[0] for g in glyphs)
    block_h = sum(g.size[1] for g in glyphs) + line_gap * (len(glyphs) - 1)
    y_center_rel = float(layout.get("y_rel") or style.get("y_center_rel") or 0.30)
    cy = int(H * y_center_rel)
    top = cy - block_h // 2

    hi = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = top
    for g in glyphs:
        x = (W - g.size[0]) // 2
        hi.alpha_composite(g, dest=(x, max(0, y)))
        y += g.size[1] + line_gap

    # Very light drop shadow under whole block for wall readability
    alpha = hi.split()[-1]
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.putalpha(alpha.point(lambda p: int(p * 0.28)))
    sh = sh.filter(ImageFilter.GaussianBlur(radius=max(2, ss * 2)))
    shadow.alpha_composite(sh, dest=(ss, ss * 2))
    composed = Image.alpha_composite(shadow, hi)

    img = composed.resize((width, height), Image.Resampling.LANCZOS)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def _resolve_sticker_path(cfg: dict[str, Any], assets: Path) -> tuple[Path, dict[str, Any]] | None:
    pack = cfg.get("pack") or {}
    st = pack.get("sticker") or {}
    if st.get("enabled", True) is False:
        return None
    # Legacy sticker ids → new cute set
    legacy_stickers = {
        "dm_tap_01": "dm_emoji_point",
        "dm_chat_01": "dm_emoji_bubble",
        "dm_hand_01": "dm_hand_cute",
        "dm_bell_01": "dm_bell_cute",
        "dm_heart_01": "dm_emoji_heart",
        "dm_wave_01": "dm_pink_wave",
    }
    style_id = str(st.get("style") or pack.get("default_sticker") or DEFAULT_STICKER)
    style_id = legacy_stickers.get(style_id, style_id)
    custom = str(st.get("file") or "").strip()
    if custom:
        path = Path(custom)
        if not path.is_file():
            path = assets / "stickers" / custom
        if not path.is_file():
            raise FileNotFoundError(f"sticker file not found: {custom}")
        defaults = STICKER_SPECS.get(DEFAULT_STICKER, {})
        meta = {
            "width_rel": float(st.get("width_rel") or defaults.get("width_rel") or 0.28),
            "x": float(st.get("x") if st.get("x") is not None else defaults.get("x", 0.16)),
            "y": float(st.get("y") if st.get("y") is not None else defaults.get("y", 0.90)),
        }
        return path.resolve(), meta

    ensure_builtin_stickers(assets / "stickers", force=False)
    path = assets / "stickers" / f"{style_id}.apng"
    if not path.is_file():
        for ext in (".webm", ".mov", ".gif", ".webp", ".png"):
            alt = assets / "stickers" / f"{style_id}{ext}"
            if alt.is_file():
                path = alt
                break
    if not path.is_file():
        raise FileNotFoundError(
            f"Sticker style {style_id!r} not found under {assets / 'stickers'}"
        )
    defaults = STICKER_SPECS.get(style_id, STICKER_SPECS[DEFAULT_STICKER])
    meta = {
        "width_rel": float(st.get("width_rel") or defaults.get("width_rel") or 0.28),
        "x": float(st.get("x") if st.get("x") is not None else defaults.get("x", 0.16)),
        "y": float(st.get("y") if st.get("y") is not None else defaults.get("y", 0.90)),
    }
    return path.resolve(), meta


def _escape_filter_path(path: Path) -> str:
    # FFmpeg filter paths: forward slashes, escape : and \
    text = path.resolve().as_posix()
    return text.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def pack_video(
    input_path: Path,
    output_path: Path,
    cfg: dict[str, Any],
    work_dir: Path,
    project_root: Path | None = None,
) -> float:
    """
    Overlay full-video title + timed sticker; replace audio with BGM only.

    ``input_path`` should be the stage-A speed-edited video.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    pack = cfg.get("pack") or {}
    assets = assets_root(cfg, project_root)

    media = probe_media(input_path)
    width = int(media["width"] or 0)
    height = int(media["height"] or 0)
    duration = float(media["duration"] or probe_duration(input_path))
    if width <= 0 or height <= 0 or duration <= 0:
        raise RuntimeError(f"Cannot probe pack input: {input_path}")

    lines = collect_text_lines(pack, cfg)
    style_name = str(pack.get("style") or pack.get("default_style") or DEFAULT_STYLE)
    font_path = resolve_font(cfg, assets)
    title_png = work_dir / "pack_title.png"
    layout = pack.get("layout") or {}
    if not isinstance(layout, dict):
        layout = {}
    title_path = render_title_overlay(
        lines, style_name, width, height, font_path, title_png, layout=layout
    )

    sticker_info = _resolve_sticker_path(cfg, assets)
    sticker_start = float((pack.get("sticker") or {}).get("start", pack.get("sticker_start", 8.0)))
    sticker_start = max(0.0, min(sticker_start, max(0.0, duration - 0.05)))

    audio_cfg = pack.get("audio") or {}
    bgm = str(audio_cfg.get("bgm") or DEFAULT_BGM).strip() or DEFAULT_BGM
    bgm_path = resolve_bgm_path(bgm, assets)
    bgm_volume = float(audio_cfg.get("volume", 0.85))
    fade_in = float(audio_cfg.get("fade_in", 0.5))
    fade_out = float(audio_cfg.get("fade_out", 0.8))

    # Build multi-input ffmpeg graph
    # inputs: 0=video, [1=title png], [2=sticker], last=bgm
    inputs: list[str] = ["-i", str(input_path)]
    next_idx = 1
    title_idx = None
    sticker_idx = None

    if title_path is not None:
        inputs += ["-loop", "1", "-i", str(title_path)]
        title_idx = next_idx
        next_idx += 1

    if sticker_info is not None:
        sticker_path, sticker_meta = sticker_info
        # loop sticker stream
        inputs += ["-stream_loop", "-1", "-i", str(sticker_path)]
        sticker_idx = next_idx
        next_idx += 1
    else:
        sticker_meta = {}

    inputs += ["-stream_loop", "-1", "-i", str(bgm_path)]
    bgm_idx = next_idx

    filter_parts: list[str] = []
    current = "[0:v]"
    vlabel = 0

    if title_idx is not None:
        out = f"[v{vlabel}]"
        filter_parts.append(
            f"{current}[{title_idx}:v]overlay=0:0:format=auto{out}"
        )
        current = out
        vlabel += 1

    if sticker_idx is not None:
        sw = max(32, int(width * float(sticker_meta["width_rel"])))
        # keep even dims for yuv
        if sw % 2:
            sw += 1
        sx = float(sticker_meta["x"])
        sy = float(sticker_meta["y"])
        # position: x/y are anchor centers in normalized coords
        # overlay x = center_x - overlay_w/2
        ox = f"(main_w*{sx:.4f})-(overlay_w/2)"
        oy = f"(main_h*{sy:.4f})-(overlay_h/2)"
        scaled = f"[s{sticker_idx}]"
        filter_parts.append(
            f"[{sticker_idx}:v]fps=12,scale={sw}:-1:flags=lanczos,format=rgba{scaled}"
        )
        out = f"[v{vlabel}]"
        filter_parts.append(
            f"{current}{scaled}overlay=x='{ox}':y='{oy}':"
            f"enable='gte(t,{sticker_start:.3f})':format=auto{out}"
        )
        current = out
        vlabel += 1

    if current != "[0:v]":
        filter_parts.append(f"{current}format=yuv420p[outv]")
    else:
        filter_parts.append("[0:v]format=yuv420p[outv]")

    # Audio: BGM only, trim to duration, volume, optional fades
    afades: list[str] = [f"volume={bgm_volume:.4f}"]
    if fade_in > 0:
        afades.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0 and duration > fade_out:
        afades.append(f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}")
    afilter = ",".join(afades)
    filter_parts.append(
        f"[{bgm_idx}:a]atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,{afilter}[outa]"
    )

    filter_script = work_dir / "pack_filter_complex.txt"
    filter_script.write_text(";\n".join(filter_parts), encoding="utf-8")

    enc = cfg.get("encode") or {}
    # Lightweight re-encode for overlay stage — match source family still
    src_codec = str(media.get("video_codec") or "h264")
    vcodec = select_video_codec(src_codec, str(enc.get("video_codec", "auto")))
    ensure_encoder(vcodec)
    preset = str(enc.get("preset", "medium"))
    pix = str(enc.get("pixel_format", "yuv420p"))

    # Prefer bitrate near intermediate if available
    v_br = int(media.get("video_bitrate") or 0)
    if v_br < 80_000:
        v_br = 400_000

    temporary = output_path.with_name(f".{output_path.stem}.pack.part{output_path.suffix}")
    temporary.unlink(missing_ok=True)
    log_path = work_dir / "pack_ffmpeg.log"

    cmd = [
        "ffmpeg", "-y", "-nostdin",
        *inputs,
        *_filter_complex_file_args(filter_script),
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", vcodec,
        "-preset", preset,
        "-b:v", f"{max(32, int(round(v_br / 1000)))}k",
        "-pix_fmt", pix,
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", f"{duration:.6f}",
        "-movflags", str(enc.get("movflags", "+faststart")),
        "-progress", "pipe:1", "-nostats",
        str(temporary),
    ]
    if vcodec == "libx265":
        # insert before -c:a
        idx = cmd.index("-c:a")
        cmd[idx:idx] = ["-tag:v", "hvc1", "-x265-params", "log-level=error"]

    print(f"  pack: style={style_name} sticker_start={sticker_start:.2f}s bgm={bgm_path.name}")
    last_percent = -10
    with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=log_file,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_ms" and duration > 0:
                try:
                    seconds = float(value) / 1_000_000.0
                except ValueError:
                    continue
                percent = min(100, int(seconds / duration * 100))
                bucket = percent // 10 * 10
                if bucket >= last_percent + 10:
                    print(f"  pack progress: {bucket}%")
                    last_percent = bucket
        code = process.wait()
    if code != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"pack ffmpeg failed:\n{tail}")

    out_dur = probe_duration(temporary)
    if out_dur <= 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("pack produced empty output")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _replace_with_retry(temporary, output_path)

    # Write pack summary for debugging
    summary = {
        "style": style_name,
        "lines": [str(x) for x in lines if str(x).strip()],
        "styles_available": list_styles(),
        "sticker": None
        if sticker_info is None
        else {
            "path": str(sticker_info[0]),
            "start": sticker_start,
            **sticker_meta,
        },
        "bgm": str(bgm_path.resolve()),
        "duration": out_dur,
        "font": font_path,
    }
    (work_dir / "pack_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out_dur
