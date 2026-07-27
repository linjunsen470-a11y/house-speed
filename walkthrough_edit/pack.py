"""Stage-B packaging: full-video title, dynamic DM sticker, BGM-only audio."""
from __future__ import annotations

import hashlib
import json
import math
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
from .stickers_gen import (
    DEFAULT_STICKER,
    STICKER_SPECS,
    ensure_builtin_stickers,
    generate_sticker_apng,
)
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
    """Prefer pack font, then chunky project fonts, then system Chinese bold."""
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

    # Short-video 花字: prefer bundled chunky faces before system UI fonts.
    font_dir = assets / "fonts"
    preferred_names = [
        "SmileySans-Oblique.ttf",
        "SmileySans-Oblique.otf",
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


def resolve_upright_bold_font(primary: str, assets: Path | None = None) -> str:
    """Prefer an upright bold CJK face for dense 花字 (price line).

    Display italics/obliques look stylish on titles but smear Chinese glyphs
    after multi-stroke + downscale — bad for the price hook.
    """
    name = Path(primary).name.lower()
    if "oblique" not in name and "italic" not in name:
        # Already upright; keep it if it loads
        return primary

    # Prefer true bold / heavy faces first (not decorative regulars).
    candidates: list[Path] = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    ]
    if assets is not None:
        font_dir = assets / "fonts"
        for n in (
            "NotoSansSC-Bold.otf",
            "SourceHanSansSC-Bold.otf",
            "SmileySans.ttf",
        ):
            candidates.append(font_dir / n)
    candidates.extend(
        [
            Path(r"C:\Windows\Fonts\msyh.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
        ]
    )
    for p in candidates:
        if p.is_file():
            return str(p.resolve())
    return primary


def _text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    *,
    stroke_width: int = 0,
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])


def _draw_sparkle(
    img: Image.Image,
    cx: int,
    cy: int,
    arm: int,
    *,
    fill: tuple[int, int, int, int] = (255, 250, 210, 240),
) -> None:
    """Small 4-point star (diamond + cross) for 花字 accent."""
    draw = ImageDraw.Draw(img)
    arm = max(3, arm)
    # Diamond
    draw.polygon(
        [(cx, cy - arm), (cx + arm // 2, cy), (cx, cy + arm), (cx - arm // 2, cy)],
        fill=fill,
    )
    # Thin cross for sparkle tips
    tip = max(1, arm // 3)
    draw.polygon(
        [
            (cx, cy - arm - tip),
            (cx + tip, cy - arm // 2),
            (cx, cy - arm // 4),
            (cx - tip, cy - arm // 2),
        ],
        fill=fill,
    )
    draw.polygon(
        [
            (cx, cy + arm // 4),
            (cx + tip, cy + arm // 2),
            (cx, cy + arm + tip),
            (cx - tip, cy + arm // 2),
        ],
        fill=fill,
    )


def _add_price_sparkles(
    hi: Image.Image,
    glyph: Image.Image,
    dest: tuple[int, int],
    *,
    phase: float = 0.0,
) -> None:
    """Twinkling stars around the price line; ``phase`` in [0, 1) drives blink."""
    dx, dy = dest
    gw, gh = glyph.width, glyph.height
    base = max(5, min(gw, gh) // 15)
    # (rel_x, rel_y, size_scale, phase_offset, warm?)
    stars = (
        (0.92, 0.18, 1.00, 0.00, True),
        (0.78, 0.08, 0.65, 0.33, True),
        (0.08, 0.35, 0.55, 0.55, False),
        (0.95, 0.55, 0.45, 0.72, False),
        (0.18, 0.12, 0.40, 0.18, True),
        (0.88, 0.78, 0.50, 0.88, False),
    )
    for rx, ry, sm, po, warm in stars:
        # Staggered blink: bright → dim without fully vanishing
        wave = 0.5 + 0.5 * math.sin(2 * math.pi * (phase + po))
        alpha = int(110 + 145 * wave)
        arm = max(3, int(base * sm * (0.75 + 0.35 * wave)))
        fill = (255, 248, 200, alpha) if warm else (255, 255, 255, alpha)
        _draw_sparkle(
            hi,
            dx + int(gw * rx),
            dy + int(gh * ry),
            arm,
            fill=fill,
        )


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
    stroke_under: tuple[int, int, int, int] | None = None,
    stroke_under_r: int = 0,
) -> Image.Image:
    """One continuous line (no tracking) via FreeType multi-stroke 花字."""
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    thick = max(stroke_outer_r, stroke_inner_r, glow_r, stroke_under_r)
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
            stroke_width=max(stroke_outer_r, stroke_under_r) + max(2, glow_r // 2),
            stroke_fill=col,
        )
        glow_layer = glow_layer.filter(
            ImageFilter.GaussianBlur(radius=max(1.5, glow_r * 0.4))
        )
        r, g, b, a = glow_layer.split()
        a = a.point(lambda p: int(p * 0.5))
        canvas = Image.alpha_composite(canvas, Image.merge("RGBA", (r, g, b, a)))

    draw = ImageDraw.Draw(canvas)
    # Dark under-ring first → outer color → white ring → fill (max contrast on light walls)
    if stroke_under is not None and stroke_under_r > 0:
        draw.text(
            (x, y),
            text,
            font=font,
            fill=stroke_under,
            stroke_width=stroke_under_r,
            stroke_fill=stroke_under,
        )
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
    stroke_under: tuple[int, int, int, int] | None = None,
    stroke_under_r: int = 0,
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
            stroke_under=stroke_under,
            stroke_under_r=stroke_under_r,
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
            stroke_under=stroke_under,
            stroke_under_r=stroke_under_r,
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
            stroke_under=stroke_under,
            stroke_under_r=stroke_under_r,
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


def collect_text_content(pack: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Return semantic title/highlights/price while preserving legacy inputs."""
    text_cfg = pack.get("text") or {}
    title = str(text_cfg.get("title") or "").strip()
    raw_highlights = text_cfg.get("highlights")
    if isinstance(raw_highlights, str):
        highlights = [raw_highlights.strip()] if raw_highlights.strip() else []
    elif isinstance(raw_highlights, list):
        highlights = [str(x).strip() for x in raw_highlights if str(x).strip()]
    else:
        highlights = []
    price = str(text_cfg.get("price") or "").strip()
    if title or highlights or price:
        return {"title": title, "highlights": highlights[:3], "price": price}

    raw_lines = text_cfg.get("lines")
    if isinstance(raw_lines, list) and any(str(x).strip() for x in raw_lines):
        lines = [str(x).strip() for x in raw_lines if str(x).strip()][:5]
    else:
        lines = []
        for key in ("line1", "line2", "line3", "line4", "line5"):
            value = str(text_cfg.get(key) or "").strip()
            if value:
                lines.append(value)
    if lines:
        return {
            "title": lines[0],
            "highlights": lines[1:-1] if len(lines) > 2 else [],
            "price": lines[-1] if len(lines) > 1 else "",
        }

    meta = pack.get("meta") or cfg.get("meta") or {}
    subtitle = str(meta.get("subtitle") or "").strip()
    return {
        "title": str(meta.get("title") or "").strip(),
        "highlights": [subtitle] if subtitle else [],
        "price": str(meta.get("price") or "").strip(),
    }

def collect_text_lines(pack: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    """Flatten semantic text for legacy callers and summaries."""
    content = collect_text_content(pack, cfg)
    return [
        str(value).strip()
        for value in (
            content.get("title"),
            *(content.get("highlights") or []),
            content.get("price"),
        )
        if str(value or "").strip()
    ][:5]


def _render_role_line(
    text: str,
    role: str,
    roles: dict[str, Any],
    *,
    short: int,
    max_width: int,
    font_path: str,
    probe: ImageDraw.ImageDraw,
    ss: int,
    size_scale: float = 1.0,
    assets: Path | None = None,
) -> Image.Image:
    """Fit and rasterize one estate role line."""
    spec = roles[role]
    use_font = font_path
    if spec.get("prefer_upright_bold"):
        use_font = resolve_upright_bold_font(font_path, assets)
    base_rel = float(spec["font_size_rel"]) * size_scale
    under_rel = float(spec.get("stroke_under_rel") or 0)
    inner_rel = float(spec.get("stroke_inner_rel") or 0)
    font_size = max(20 * ss, int(short * base_rel))
    while font_size > 8 * ss:
        font = _load_font(use_font, font_size)
        outer_r = max(2 * ss, int(font_size * float(spec["stroke_outer_rel"])))
        under_r = max(0, int(font_size * under_rel)) if under_rel else 0
        fit_r = max(outer_r, under_r)
        if _text_size(probe, text, font, stroke_width=fit_r)[0] <= max_width:
            break
        font_size -= ss
    font = _load_font(use_font, font_size)
    outer_r = max(2 * ss, int(font_size * float(spec["stroke_outer_rel"])))
    under_r = max(0, int(font_size * under_rel)) if under_rel else 0
    if under_r > 0:
        under_r = max(under_r, outer_r + ss)
    if inner_rel > 0:
        inner_r = max(ss, int(font_size * inner_rel))
        inner_r = min(inner_r, max(ss, outer_r - ss))
    else:
        inner_r = 0
    glow_r = max(0, int(font_size * float(spec.get("glow_rel", 0))))
    under_col = tuple(spec["stroke_under"]) if spec.get("stroke_under") else None
    glow = tuple(spec["glow"]) if spec.get("glow") else None
    fill_bottom = tuple(spec["fill_bottom"]) if spec.get("fill_bottom") else None
    return _render_line_glyph(
        text, font, tuple(spec["fill"]),
        fill_bottom,
        tuple(spec["stroke_outer"]), tuple(spec["stroke_inner"]),
        outer_r, inner_r,
        glow, glow_r,
        stroke_under=under_col,
        stroke_under_r=under_r,
    )


def _draw_price_plate(
    hi: Image.Image,
    dest: tuple[int, int],
    glyph: Image.Image,
    ss: int,
) -> None:
    """Soft dark bar under price text — lifts yellow glyphs off white walls."""
    dx, dy = dest
    gw, gh = glyph.width, glyph.height
    # Tight to the ink bbox so the plate doesn't look like a heavy subtitle bar
    alpha = glyph.split()[-1]
    bbox = alpha.getbbox()
    if not bbox:
        return
    pad_x = max(ss * 4, (bbox[2] - bbox[0]) // 18)
    pad_y = max(ss * 2, (bbox[3] - bbox[1]) // 10)
    x0 = dx + bbox[0] - pad_x
    y0 = dy + bbox[1] - pad_y
    x1 = dx + bbox[2] + pad_x
    y1 = dy + bbox[3] + pad_y
    plate = Image.new("RGBA", hi.size, (0, 0, 0, 0))
    radius = max(ss * 3, (y1 - y0) // 3)
    ImageDraw.Draw(plate).rounded_rectangle(
        (x0, y0, x1, y1),
        radius=radius,
        fill=(12, 14, 22, 120),
    )
    plate = plate.filter(ImageFilter.GaussianBlur(radius=max(1.5, ss * 1.2)))
    # Composite plate under existing content by rebuilding: plate then hi content
    # Caller should draw plate before glyphs; here we alpha_composite onto hi under...
    # Actually hi already empty at dest region when called before glyph — draw plate on hi.
    hi.alpha_composite(plate)


def _render_estate_overlay(
    content: dict[str, Any], style: dict[str, Any], width: int, height: int,
    font_path: str, layout: dict[str, Any],
    *,
    sparkle_phase: float = 0.0,
    draw_sparkles: bool = True,
    return_sparkle_targets: bool = False,
) -> Image.Image | tuple[Image.Image, list[tuple[Image.Image, tuple[int, int]]]]:
    """Render independently fitted title, highlight and price rows."""
    ss = 3
    W, H = width * ss, height * ss
    short = min(W, H)
    max_width = int(W * float(layout.get("max_width_rel", style.get("max_width_rel", 0.88))))
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    roles = style.get("roles") or {}
    highlights = [str(x).strip() for x in content.get("highlights", []) if str(x).strip()]
    mode = str(
        layout.get("highlights_mode")
        or style.get("highlights_mode")
        or "join"
    ).strip().lower()
    if mode not in {"join", "stack"}:
        mode = "join"

    # (role, text, want_sparkle, want_plate)
    rows: list[tuple[str, str, bool, bool]] = []
    title = str(content.get("title") or "").strip()
    if title:
        rows.append(("title", title, False, False))
    if highlights:
        if mode == "stack":
            for h in highlights[:3]:
                rows.append(("highlights", h, False, False))
        else:
            rows.append(("highlights", " \u00b7 ".join(highlights), False, False))
    price = str(content.get("price") or "").strip()
    style_sparkle = bool(style.get("sparkle", False))
    price_spec = roles.get("price") or {}
    price_sparkle = bool(price_spec.get("sparkle", style_sparkle))
    if layout.get("sparkle") is not None:
        price_sparkle = bool(layout.get("sparkle"))
    price_plate = bool(price_spec.get("plate", False))
    if layout.get("price_plate") is not None:
        price_plate = bool(layout.get("price_plate"))
    if price:
        rows.append(("price", price, price_sparkle, price_plate))

    # Stacked highlights: slightly smaller so 2–3 lines still fit upper band
    hl_scale = 0.90 if mode == "stack" and len(highlights) > 1 else 1.0
    font_p = Path(font_path)
    assets_guess = font_p.parent.parent if font_p.parent.name.lower() == "fonts" else None
    glyphs: list[tuple[str, Image.Image, bool, bool]] = []
    for role, text, want_sparkle, want_plate in rows:
        scale = hl_scale if role == "highlights" else 1.0
        glyph = _render_role_line(
            text, role, roles,
            short=short, max_width=max_width, font_path=font_path,
            probe=probe, ss=ss, size_scale=scale, assets=assets_guess,
        )
        glyphs.append((role, glyph, want_sparkle, want_plate))

    gap = int(short * float(layout.get("line_gap_rel", style.get("line_gap_rel", 0.006))))
    block_h = sum(g.height for _, g, _, _ in glyphs) + gap * max(0, len(glyphs) - 1)
    cy = int(H * float(layout.get("y_rel", style.get("y_center_rel", 0.255))))
    safe_top = int(H * float(style.get("safe_margin_rel", 0.06)))
    upper_bottom = int(H * 0.47)
    top = max(safe_top, min(cy - block_h // 2, max(safe_top, upper_bottom - block_h)))
    hi = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = top
    placed: list[tuple[str, Image.Image, tuple[int, int], bool, bool]] = []
    for role, glyph, want_sparkle, want_plate in glyphs:
        dest = ((W - glyph.width) // 2, y)
        placed.append((role, glyph, dest, want_sparkle, want_plate))
        y += glyph.height + gap

    for role, glyph, dest, _, want_plate in placed:
        if want_plate:
            _draw_price_plate(hi, dest, glyph, ss)

    sparkle_targets: list[tuple[Image.Image, tuple[int, int]]] = []
    for role, glyph, dest, want_sparkle, _ in placed:
        hi.alpha_composite(glyph, dest=dest)
        if want_sparkle:
            sparkle_targets.append((glyph, dest))
            if draw_sparkles:
                _add_price_sparkles(hi, glyph, dest, phase=sparkle_phase)

    alpha = hi.split()[-1]
    # Soft lift only — avoid heavy black halo around 花字
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda p: int(p * 0.18)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=ss * 2))
    offset = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    offset.alpha_composite(shadow, dest=(0, ss))
    composed = Image.alpha_composite(offset, hi)
    final = composed.resize((width, height), Image.Resampling.LANCZOS)
    if return_sparkle_targets:
        # Scale dest coords from supersampled canvas to output size
        scale = 1.0 / ss
        scaled = [
            (g.resize((max(1, int(g.width * scale)), max(1, int(g.height * scale))),
                      Image.Resampling.LANCZOS),
             (int(d[0] * scale), int(d[1] * scale)))
            for g, d in sparkle_targets
        ]
        return final, scaled
    return final


def _encode_png_sequence_apng(
    frame_paths: list[Path],
    out_path: Path,
    *,
    fps: int = 10,
) -> Path:
    """Encode ordered PNG frames into a looping APNG."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not frame_paths:
        raise ValueError("no frames for APNG")
    # Prefer contiguous %03d pattern when names are sequential
    first = frame_paths[0]
    pattern = first.parent / "f_%03d.png"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(max(6, fps)),
        "-i", str(pattern),
        "-plays", "0",
        "-f", "apng",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


def render_title_overlay(
    lines: list[str],
    style_name: str,
    width: int,
    height: int,
    font_path: str,
    out_path: Path,
    layout: dict[str, Any] | None = None,
    content: dict[str, Any] | None = None,
    *,
    animate: bool = False,
    anim_fps: int = 10,
    anim_frames: int = 10,
) -> Path | None:
    """
    Full-frame transparent 花字 PNG (or looping APNG when ``animate``).

    3× supersample + FreeType native multi-stroke, then LANCZOS downscale.
    """
    cleaned = [str(x).strip() for x in lines if str(x).strip()][:5]
    style = get_style(style_name)
    layout = layout or {}

    if style.get("kind") == "estate":
        if content is None:
            if not cleaned:
                return None
            content = {
                "title": cleaned[0] if cleaned else "",
                "highlights": cleaned[1:-1] if len(cleaned) > 2 else [],
                "price": cleaned[-1] if len(cleaned) > 1 else "",
            }
        else:
            has_text = bool(
                str(content.get("title") or "").strip()
                or str(content.get("price") or "").strip()
                or any(str(x).strip() for x in (content.get("highlights") or []))
            )
            if not has_text and not cleaned:
                return None
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        price_spec = (style.get("roles") or {}).get("price") or {}
        want_anim = bool(animate) and bool(
            price_spec.get("sparkle", style.get("sparkle", False))
        )
        if layout.get("sparkle") is not None:
            want_anim = bool(animate) and bool(layout.get("sparkle"))

        if want_anim:
            n = max(6, int(anim_frames))
            fps = max(6, int(anim_fps))
            frames_dir = out_path.parent / ".title_frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            # One expensive glyph pass; only re-draw stars per frame
            base, targets = _render_estate_overlay(
                content, style, width, height, font_path, layout,
                draw_sparkles=False,
                return_sparkle_targets=True,
            )
            assert isinstance(base, Image.Image)
            paths: list[Path] = []
            for i in range(n):
                phase = i / n
                frame = base.copy()
                for glyph, dest in targets:
                    _add_price_sparkles(frame, glyph, dest, phase=phase)
                p = frames_dir / f"f_{i:03d}.png"
                frame.save(p, "PNG")
                paths.append(p)
            apng_path = out_path.with_suffix(".apng")
            try:
                _encode_png_sequence_apng(paths, apng_path, fps=fps)
            finally:
                for p in paths:
                    p.unlink(missing_ok=True)
                try:
                    frames_dir.rmdir()
                except OSError:
                    pass
            # Static preview PNG (mid twinkle)
            preview = base.copy()
            for glyph, dest in targets:
                _add_price_sparkles(preview, glyph, dest, phase=0.25)
            preview.save(out_path, "PNG")
            return apng_path

        img = _render_estate_overlay(
            content, style, width, height, font_path, layout, sparkle_phase=0.2
        )
        assert isinstance(img, Image.Image)
        img.save(out_path, "PNG")
        return out_path

    if not cleaned:
        return None

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


def _resolve_sticker_path(
    cfg: dict[str, Any],
    assets: Path,
    *,
    work_dir: Path | None = None,
) -> tuple[Path, dict[str, Any]] | None:
    pack = cfg.get("pack") or {}
    st = pack.get("sticker") or {}
    if st.get("enabled", True) is False:
        return None
    # Legacy ids → current defaults (estate CTA or kept procedural styles)
    legacy_stickers = {
        "dm_tap_01": "dm_estate_cta",
        "dm_chat_01": "dm_estate_cta",
        "dm_hand_01": "dm_hand_cute",
        "dm_bell_01": "dm_bell_cute",
        "dm_heart_01": "dm_estate_cta",
        "dm_wave_01": "dm_pink_wave",
        "dm_emoji_bubble": "dm_estate_cta",
        "dm_emoji_heart": "dm_estate_cta",
        "dm_emoji_mail": "dm_estate_cta",
        "dm_emoji_point": "dm_estate_cta",
        "dm_follow_me": "dm_estate_cta",
    }
    style_id = str(st.get("style") or pack.get("default_sticker") or DEFAULT_STICKER)
    style_id = legacy_stickers.get(style_id, style_id)
    defaults = STICKER_SPECS.get(style_id, STICKER_SPECS[DEFAULT_STICKER])
    cta_text = str(
        st.get("text") if st.get("text") is not None else defaults.get("text") or "私信了解"
    ).strip()
    if not cta_text:
        cta_text = "私信了解"

    custom = str(st.get("file") or "").strip()
    if custom:
        path = Path(custom)
        if not path.is_file():
            path = assets / "stickers" / custom
        if not path.is_file():
            raise FileNotFoundError(f"sticker file not found: {custom}")
        meta = {
            "width_rel": float(st.get("width_rel") or defaults.get("width_rel") or 0.22),
            "x": float(st.get("x") if st.get("x") is not None else defaults.get("x", 0.14)),
            "y": float(st.get("y") if st.get("y") is not None else defaults.get("y", 0.91)),
            "text": cta_text,
        }
        return path.resolve(), meta

    # Estate CTA (GIF art or plain pill): always bake current text (config-driven)
    kind = STICKER_SPECS.get(style_id, {}).get("kind")
    if kind in {"estate_cta", "estate_gif_cta"}:
        out_dir = Path(work_dir) if work_dir is not None else (assets / "stickers")
        out_dir.mkdir(parents=True, exist_ok=True)
        # Stable filename so repeated packs with same text reuse the APNG.
        # Bump render_ver when CTA drawing changes so caches refresh.
        render_ver = "v9huazi"
        theme = str(defaults.get("theme") or "warm")
        sparkle = str(defaults.get("sparkle_id") or "")
        digest = hashlib.md5(
            f"{render_ver}|{kind}|{style_id}|{theme}|{sparkle}|{cta_text}".encode("utf-8")
        ).hexdigest()[:10]
        path = out_dir / f"{style_id}_{digest}.apng"
        if not path.is_file() or path.stat().st_size < 5_000:
            generate_sticker_apng(
                style_id, path, stickers_dir=assets / "stickers", text=cta_text
            )
        meta = {
            "width_rel": float(st.get("width_rel") or defaults.get("width_rel") or 0.30),
            "x": float(st.get("x") if st.get("x") is not None else defaults.get("x", 0.16)),
            "y": float(st.get("y") if st.get("y") is not None else defaults.get("y", 0.90)),
            "text": cta_text,
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
    meta = {
        "width_rel": float(st.get("width_rel") or defaults.get("width_rel") or 0.28),
        "x": float(st.get("x") if st.get("x") is not None else defaults.get("x", 0.16)),
        "y": float(st.get("y") if st.get("y") is not None else defaults.get("y", 0.90)),
        "text": cta_text,
    }
    return path.resolve(), meta


def _escape_filter_path(path: Path) -> str:
    # FFmpeg filter paths: forward slashes, escape : and \
    text = path.resolve().as_posix()
    return text.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def compute_sticker_windows(duration: float, sticker: dict[str, Any]) -> list[tuple[float, float]]:
    """Build non-overlapping CTA windows; short clips keep only the ending CTA."""
    if duration <= 0:
        return []
    start = max(0.0, float(sticker.get("start", 4.5)))
    window_duration = max(0.1, float(sticker.get("duration", 2.5)))
    repeat_at_end = bool(sticker.get("repeat_at_end", True))
    end_lead = max(0.1, float(sticker.get("end_lead", 2.8)))

    if not repeat_at_end:
        if start >= duration:
            return []
        return [(start, min(duration, start + window_duration))]

    end_start = max(0.0, duration - end_lead)
    end_window = (end_start, duration)
    first_end = min(duration, start + window_duration)
    # Keep both only when there is breathing room between them. This also
    # makes short videos degrade to a single, complete ending CTA.
    if start < duration and first_end + 0.5 < end_start:
        return [(start, first_end), end_window]
    return [end_window]


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
    # FFmpeg creates the temporary output beside the final file, so the
    # destination directory must exist before the process starts.
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pack = cfg.get("pack") or {}
    assets = assets_root(cfg, project_root)

    media = probe_media(input_path)
    width = int(media["width"] or 0)
    height = int(media["height"] or 0)
    duration = float(media["duration"] or probe_duration(input_path))
    if width <= 0 or height <= 0 or duration <= 0:
        raise RuntimeError(f"Cannot probe pack input: {input_path}")

    content = collect_text_content(pack, cfg)
    lines = collect_text_lines(pack, cfg)
    style_name = str(pack.get("style") or pack.get("default_style") or DEFAULT_STYLE)
    font_path = resolve_font(cfg, assets)
    title_png = work_dir / "pack_title.png"
    layout = pack.get("layout") or {}
    if not isinstance(layout, dict):
        layout = {}
    text_motion_early = pack.get("text_motion") or {}
    if not isinstance(text_motion_early, dict):
        text_motion_early = {}
    # Animated title when price has sparkles (twinkling stars APNG)
    title_animate = bool(text_motion_early.get("sparkle_anim", True))
    title_path = render_title_overlay(
        lines, style_name, width, height, font_path, title_png,
        layout=layout, content=content,
        animate=title_animate,
        anim_fps=int(float(text_motion_early.get("sparkle_fps", 10))),
        anim_frames=int(float(text_motion_early.get("sparkle_frames", 10))),
    )

    sticker_info = _resolve_sticker_path(cfg, assets, work_dir=work_dir)
    sticker_cfg = pack.get("sticker") or {}
    sticker_windows = compute_sticker_windows(duration, sticker_cfg)

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
        # APNG loops via stream_loop; still PNG uses -loop 1
        if title_path.suffix.lower() == ".apng":
            inputs += ["-stream_loop", "-1", "-i", str(title_path)]
        else:
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

    text_motion = pack.get("text_motion") or {}
    if not isinstance(text_motion, dict):
        text_motion = {}
    title_enter = str(text_motion.get("enter") or "fade").strip().lower()
    title_enter_dur = max(0.05, float(text_motion.get("duration", 0.35)))
    # Bounce off by default (user prefers no jitter)
    title_bounce = float(text_motion.get("bounce_px", text_motion.get("float_px", 0.0)))
    title_bounce_period = max(
        0.8, float(text_motion.get("bounce_period", text_motion.get("float_period", 1.4)))
    )
    # Soft opacity pulse (0–0.15). 0 = off.
    title_pulse = max(0.0, min(0.15, float(text_motion.get("pulse", 0.0))))
    title_pulse_period = max(1.2, float(text_motion.get("pulse_period", 2.0)))

    if title_idx is not None:
        title_stream = f"[{title_idx}:v]"
        prep: list[str] = []
        if title_path is not None and title_path.suffix.lower() == ".apng":
            # Normalize animated title fps a bit for smoother star twinkle
            prep.append("fps=10,format=rgba")
        if title_enter in {"fade", "pop"}:
            prep.append(f"fade=t=in:st=0:d={title_enter_dur:.3f}:alpha=1")
        if title_pulse > 0.001:
            lo = 1.0 - title_pulse
            prep.append(
                "format=rgba,"
                f"geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':"
                f"a='alpha(X\\,Y)*({lo:.3f}+{title_pulse:.3f}"
                f"*(0.5+0.5*sin(2*PI*T/{title_pulse_period:.3f})))'"
            )
        if prep:
            prepped = "[title_fx]"
            filter_parts.append(f"{title_stream}{','.join(prep)}{prepped}")
            title_stream = prepped
        out = f"[v{vlabel}]"
        if title_bounce > 0.05:
            # Jump up then settle: y grows downward, so negative = bounce up.
            # pow(abs(sin), 1.8) → snappier 跳动 than smooth float.
            oy = (
                f"-{title_bounce:.2f}*pow(abs(sin(2*PI*t/{title_bounce_period:.3f}))\\,1.8)"
            )
            filter_parts.append(
                f"{current}{title_stream}overlay=x=0:y='{oy}':format=auto{out}"
            )
        else:
            filter_parts.append(
                f"{current}{title_stream}overlay=0:0:format=auto{out}"
            )
        current = out
        vlabel += 1

    if sticker_idx is not None and sticker_windows:
        sw = max(32, int(width * float(sticker_meta["width_rel"])))
        # keep even dims for yuv
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
        # position: x/y are anchor centers in normalized coords
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
            # Build per-window motion chain (local t starts at 0 after setpts)
            chain = [
                f"trim=duration={window_duration:.3f}",
                "setpts=PTS-STARTPTS",
            ]
            if enter == "pop":
                # Soft pop: 0.88→1.03→1.0 (less aggressive)
                settle = enter_s * 1.35
                scale_expr = (
                    f"if(lt(t\\,{enter_s:.3f})\\,"
                    f"0.88+0.15*t/{enter_s:.3f}\\,"
                    f"if(lt(t\\,{settle:.3f})\\,"
                    f"1.03-0.03*(t-{enter_s:.3f})/{max(0.05, settle - enter_s):.3f}\\,"
                    f"1.0))"
                )
                chain.append(
                    f"scale=w='iw*({scale_expr})':h=-1:eval=frame:flags=lanczos"
                )
            chain.append("fade=t=in:st=0:d=0.180:alpha=1")
            chain.append(f"fade=t=out:st={fade_out_start:.3f}:d=0.300:alpha=1")
            chain.append(f"setpts=PTS+{window_start:.3f}/TB")
            filter_parts.append(f"{base_labels[i]}{','.join(chain)}{timed}")

            if enter == "slide_up":
                # Soft ease-up from ~28px below
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
    # Overlay stage re-encode: prefer CRF when match_source is false (lean packs).
    src_codec = str(media.get("video_codec") or "h264")
    vcodec = select_video_codec(src_codec, str(enc.get("video_codec", "auto")))
    ensure_encoder(vcodec)
    preset = str(enc.get("preset", "medium"))
    pix = str(enc.get("pixel_format", "yuv420p"))
    use_crf = not bool(enc.get("match_source", True))
    crf = int(float(enc.get("crf", 23)))

    # Prefer bitrate near intermediate when matching source family
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
    ]
    if use_crf:
        cmd += ["-crf", str(crf)]
    else:
        cmd += ["-b:v", f"{max(32, int(round(v_br / 1000)))}k"]
    cmd += [
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

    print(f"  pack: style={style_name} sticker_windows={sticker_windows} bgm={bgm_path.name}")
    last_percent = -10
    process: subprocess.Popen[str] | None = None
    succeeded = False
    out_dur = 0.0
    try:
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
            process.stdout.close()
            code = process.wait()
        if code != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(f"pack ffmpeg failed:\n{tail}")

        out_dur = probe_duration(temporary)
        if out_dur <= 0:
            raise RuntimeError("pack produced empty output")
        _replace_with_retry(temporary, output_path)
        succeeded = True
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if not succeeded:
            try:
                temporary.unlink(missing_ok=True)
            except PermissionError:
                pass
    if not succeeded:
        # Should not reach: failures raise inside try.
        raise RuntimeError("pack failed")

    # Write pack summary for debugging
    summary = {
        "style": style_name,
        "lines": [str(x) for x in lines if str(x).strip()],
        "text": content,
        "styles_available": list_styles(),
        "sticker": None
        if sticker_info is None
        else {
            "path": str(sticker_info[0]),
            "windows": sticker_windows,
            "config": sticker_cfg,
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
