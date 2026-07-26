"""
私信角标 stickers.

Primary path: transparent animated emoji GIFs (Noto Animated Emoji) + Chinese badge text.
Fallback: typography-first procedural badges (no crude cartoon mascots).
"""
from __future__ import annotations

import math
import subprocess
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageSequence


# Google Noto Animated Emoji (Apache-2.0) — transparent GIF loops
NOTO_EMOJI_BASE = "https://fonts.gstatic.com/s/e/notoemoji/latest"
EXTERNAL_SOURCES: dict[str, dict] = {
    "dm_emoji_bubble": {
        "label": "一体角标：气泡图标 + 私信我（推荐）",
        "emoji_id": "1f4ac",
        "text": "私信我",
        "width_rel": 0.34,
        "x": 0.18,
        "y": 0.91,
        "emoji_scale": 0.38,  # icon share of badge height
    },
    "dm_emoji_heart": {
        "label": "一体角标：爱心 + 私信我",
        "emoji_id": "2764_fe0f",
        "text": "私信我",
        "width_rel": 0.34,
        "x": 0.18,
        "y": 0.91,
        "emoji_scale": 0.36,
    },
    "dm_emoji_mail": {
        "label": "一体角标：信封 + 私信我",
        "emoji_id": "1f48c",
        "text": "私信我",
        "width_rel": 0.34,
        "x": 0.18,
        "y": 0.91,
        "emoji_scale": 0.36,
    },
    "dm_emoji_point": {
        "label": "一体角标：指向 + 戳我私信",
        "emoji_id": "1f449",
        "text": "戳我私信",
        "width_rel": 0.36,
        "x": 0.19,
        "y": 0.91,
        "emoji_scale": 0.34,
    },
}

STICKER_SPECS: dict[str, dict] = {
    **{
        sid: {
            "label": meta["label"],
            "kind": "emoji_badge",
            "width_rel": meta["width_rel"],
            "x": meta["x"],
            "y": meta["y"],
            "emoji_id": meta["emoji_id"],
            "text": meta["text"],
            "emoji_scale": meta["emoji_scale"],
        }
        for sid, meta in EXTERNAL_SOURCES.items()
    },
    # --- Locked family: 花字一体式 CTA（用户确认 v7 观感后锁定）---------------
    # 结构统一：多层花字 + 小 Noto 点缀 GIF，不叠大号气泡/独立胶囊。
    "dm_estate_cta": {
        "label": "【锁定默认】暖橙花字 + 星光",
        "kind": "estate_gif_cta",
        "theme": "warm",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
        "locked": True,
    },
    "dm_estate_cta_gold": {
        "label": "金奢花字 + 星光",
        "kind": "estate_gif_cta",
        "theme": "gold",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_sky": {
        "label": "晴空蓝花字 + 星光",
        "kind": "estate_gif_cta",
        "theme": "sky",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_mint": {
        "label": "薄荷花字 + 星光",
        "kind": "estate_gif_cta",
        "theme": "mint",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_violet": {
        "label": "紫霓花字 + 星光",
        "kind": "estate_gif_cta",
        "theme": "violet",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_snow": {
        "label": "冰雪白花字 + 金环 + 星光（加对比）",
        "kind": "estate_gif_cta",
        "theme": "snow",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_heart": {
        "label": "暖粉花字 + 爱心点缀",
        "kind": "estate_gif_cta",
        "theme": "rose",
        "sparkle_id": "2764_fe0f",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_fire": {
        "label": "炽红花字 + 星光（强钩子）",
        "kind": "estate_gif_cta",
        "theme": "fire",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_ink": {
        "label": "墨黑花字 + 金环（高级感）",
        "kind": "estate_gif_cta",
        "theme": "ink",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_lemon": {
        "label": "柠檬黄花字 + 红描边",
        "kind": "estate_gif_cta",
        "theme": "lemon",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_neon": {
        "label": "霓虹青花字 + 星光",
        "kind": "estate_gif_cta",
        "theme": "neon",
        "sparkle_id": "2728",
        "width_rel": 0.30,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    "dm_estate_cta_bubble": {
        "label": "气泡主视觉（备选）",
        "kind": "estate_gif_cta",
        "emoji_id": "1f4ac",
        "sparkle_id": "2728",
        "layout": "bubble",
        "theme": "warm",
        "width_rel": 0.34,
        "x": 0.17,
        "y": 0.88,
        "text": "私信了解",
    },
    # 原「磨砂暗胶囊」改为更亮的软胶囊，仍无网络依赖
    "dm_estate_cta_plain": {
        "label": "软珊瑚胶囊（无网络，重做）",
        "kind": "estate_cta",
        "width_rel": 0.28,
        "x": 0.16,
        "y": 0.90,
        "text": "私信了解",
    },
    # 以下原「稀疏/程序弱款」全部升为花字一体式（保留旧 id 兼容）
    "dm_follow_me": {
        "label": "青字私信我 + 星光（原青气泡重做）",
        "kind": "estate_gif_cta",
        "theme": "sky",
        "sparkle_id": "2728",
        "width_rel": 0.28,
        "x": 0.16,
        "y": 0.90,
        "text": "私信我",
    },
    "dm_pink_wave": {
        "label": "粉字私信我 + 星光（原粉描边重做）",
        "kind": "estate_gif_cta",
        "theme": "rose",
        "sparkle_id": "2728",
        "width_rel": 0.28,
        "x": 0.16,
        "y": 0.90,
        "text": "私信我",
    },
    "dm_chat_pop": {
        "label": "粉字私信我 + 爱心（原粉胶囊重做）",
        "kind": "estate_gif_cta",
        "theme": "rose",
        "sparkle_id": "2764_fe0f",
        "width_rel": 0.28,
        "x": 0.16,
        "y": 0.90,
        "text": "私信我",
    },
    "dm_heart_tap": {
        "label": "暖粉短字「私信」+ 爱心",
        "kind": "estate_gif_cta",
        "theme": "rose",
        "sparkle_id": "2764_fe0f",
        "width_rel": 0.24,
        "x": 0.14,
        "y": 0.90,
        "text": "私信",
    },
    "dm_bell_cute": {
        "label": "金字私信我 + 星光（原铃铛重做）",
        "kind": "estate_gif_cta",
        "theme": "gold",
        "sparkle_id": "2728",
        "width_rel": 0.28,
        "x": 0.16,
        "y": 0.90,
        "text": "私信我",
    },
    "dm_hand_cute": {
        "label": "炽红「戳我私信」+ 星光（原点击重做）",
        "kind": "estate_gif_cta",
        "theme": "fire",
        "sparkle_id": "2728",
        "width_rel": 0.32,
        "x": 0.17,
        "y": 0.90,
        "text": "戳我私信",
    },
}

DEFAULT_STICKER = "dm_estate_cta"

# 花字 CTA 配色主题（fill / outer / mid / glow）
HUAZI_THEMES: dict[str, dict[str, tuple[int, int, int, int]]] = {
    # Locked default — do not change casually
    "warm": {
        "fill": (255, 75, 55, 255),
        "outer": (22, 48, 110, 255),
        "mid": (255, 255, 255, 255),
        "glow": (255, 120, 40, 120),
        "glow_stroke": (255, 100, 30, 100),
    },
    "gold": {
        "fill": (255, 210, 70, 255),
        "outer": (90, 50, 10, 255),
        "mid": (255, 250, 230, 255),
        "glow": (255, 190, 60, 110),
        "glow_stroke": (220, 150, 30, 90),
    },
    "sky": {
        "fill": (70, 170, 255, 255),
        "outer": (18, 55, 130, 255),
        "mid": (255, 255, 255, 255),
        "glow": (80, 160, 255, 100),
        "glow_stroke": (40, 120, 220, 90),
    },
    "mint": {
        "fill": (70, 230, 190, 255),
        "outer": (15, 90, 80, 255),
        "mid": (255, 255, 255, 255),
        "glow": (60, 220, 180, 100),
        "glow_stroke": (30, 180, 150, 90),
    },
    "violet": {
        "fill": (190, 110, 255, 255),
        "outer": (70, 30, 130, 255),
        "mid": (255, 255, 255, 255),
        "glow": (180, 100, 255, 110),
        "glow_stroke": (140, 60, 220, 90),
    },
    # snow: ice cream white — thicker dark outer so it doesn't wash out on light walls
    "snow": {
        "fill": (255, 252, 248, 255),
        "outer": (18, 28, 55, 255),
        "mid": (255, 200, 90, 255),  # gold mid ring for punch
        "glow": (255, 220, 140, 100),
        "glow_stroke": (40, 60, 100, 90),
    },
    "rose": {
        "fill": (255, 95, 150, 255),
        "outer": (130, 25, 70, 255),
        "mid": (255, 255, 255, 255),
        "glow": (255, 120, 170, 110),
        "glow_stroke": (230, 70, 120, 90),
    },
    "fire": {
        "fill": (255, 45, 40, 255),
        "outer": (20, 70, 160, 255),
        "mid": (255, 255, 255, 255),
        "glow": (255, 80, 30, 120),
        "glow_stroke": (255, 50, 20, 100),
    },
    "ink": {
        "fill": (30, 34, 48, 255),
        "outer": (255, 255, 255, 255),
        "mid": (255, 210, 80, 255),
        "glow": (20, 25, 40, 100),
        "glow_stroke": (0, 0, 0, 80),
    },
    "lemon": {
        "fill": (255, 235, 70, 255),
        "outer": (180, 40, 50, 255),
        "mid": (255, 255, 255, 255),
        "glow": (255, 220, 60, 110),
        "glow_stroke": (220, 80, 40, 90),
    },
    "neon": {
        "fill": (60, 255, 220, 255),
        "outer": (20, 30, 80, 255),
        "mid": (255, 255, 255, 255),
        "glow": (40, 240, 200, 120),
        "glow_stroke": (0, 200, 180, 100),
    },
}


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path("assets/fonts/SmileySans-Oblique.ttf"),
    ):
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                try:
                    return ImageFont.truetype(str(path), size=size, index=0)
                except OSError:
                    continue
    return ImageFont.load_default()


def _outlined_text(
    img: Image.Image,
    text: str,
    center: tuple[int, int],
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    stroke: tuple[int, int, int, int],
    stroke_w: int,
    *,
    outer: tuple[int, int, int, int] | None = None,
    outer_w: int = 0,
) -> None:
    """Premium badge text: optional outer color ring + white ring + fill."""
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=max(stroke_w, outer_w))
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = center[0] - tw // 2
    y = center[1] - th // 2

    # soft under-glow
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gc = (fill[0], fill[1], fill[2], 90)
    gd.text((x, y), text, font=font, fill=gc, stroke_width=stroke_w + 4, stroke_fill=gc)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(radius=max(2, stroke_w))))

    draw = ImageDraw.Draw(img)
    if outer and outer_w > 0:
        draw.text(
            (x, y),
            text,
            font=font,
            fill=outer,
            stroke_width=outer_w,
            stroke_fill=outer,
        )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=stroke,
        stroke_width=stroke_w,
        stroke_fill=stroke,
    )
    draw.text((x, y), text, font=font, fill=fill)


def _speech_bubble(
    img: Image.Image,
    box: tuple[int, int, int, int],
    t: float,
    *,
    fill: tuple[int, int, int, int] = (120, 210, 255, 250),
) -> None:
    """Soft chat bubble with highlight + animated dots."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    # shadow
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle((x0 + 3, y0 + 6, x1 + 3, y1 + 6), radius=h // 2, fill=(20, 40, 80, 55))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(3)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=h // 2, fill=fill, outline=(255, 255, 255, 255), width=4)
    # top gloss
    gloss = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    gd.rounded_rectangle(
        (x0 + 8, y0 + 6, x1 - 8, y0 + h // 2),
        radius=h // 3,
        fill=(255, 255, 255, 75),
    )
    img.alpha_composite(gloss.filter(ImageFilter.GaussianBlur(1.2)))

    # three dots
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    d = ImageDraw.Draw(img)
    for i, dx in enumerate((-20, 0, 20)):
        phase = 0.55 + 0.45 * math.sin(t * 2 * math.pi + i * 0.9)
        rr = int(5 + 3 * phase)
        d.ellipse((cx + dx - rr, cy - rr, cx + dx + rr, cy + rr), fill=(255, 255, 255, 255))


def _render_bubble_pink(size: int, t: float) -> Image.Image:
    """Default: cyan bubble + pink 私信我 — closest to commercial refs, no mascot."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bob = int(6 * math.sin(t * 2 * math.pi))
    scale = 1.0 + 0.03 * math.sin(t * 2 * math.pi)

    bw, bh = int(size * 0.52 * scale), int(size * 0.24 * scale)
    cx = size // 2
    box = (cx - bw // 2, int(size * 0.22) + bob, cx + bw // 2, int(size * 0.22) + bob + bh)
    _speech_bubble(img, box, t)

    font = _font(max(42, int(size * 0.155)))
    _outlined_text(
        img,
        "私信我",
        (cx, int(size * 0.68) + bob // 2),
        font,
        fill=(255, 75, 160, 255),
        stroke=(255, 255, 255, 255),
        stroke_w=max(5, size // 48),
        outer=(255, 170, 210, 255),
        outer_w=max(2, size // 90),
    )
    # tiny sparkles
    d = ImageDraw.Draw(img)
    for i, (sx, sy) in enumerate(((0.16, 0.58), (0.84, 0.58), (0.50, 0.52))):
        phase = 0.45 + 0.55 * abs(math.sin(t * 2 * math.pi + i))
        r = int(3 + 4 * phase)
        x, y = int(size * sx), int(size * sy)
        d.line((x - r * 2, y, x + r * 2, y), fill=(255, 255, 255, int(230 * phase)), width=2)
        d.line((x, y - r * 2, x, y + r * 2), fill=(255, 255, 255, int(230 * phase)), width=2)
    return img


def _render_text_pink(size: int, t: float) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bob = int(8 * math.sin(t * 2 * math.pi))
    font = _font(max(48, int(size * 0.18)))
    _outlined_text(
        img,
        "私信我",
        (size // 2, size // 2 + bob),
        font,
        fill=(255, 70, 155, 255),
        stroke=(255, 255, 255, 255),
        stroke_w=max(6, size // 42),
    )
    # underline wave
    d = ImageDraw.Draw(img)
    pts = [
        (int(size * (0.18 + 0.64 * i / 14)), int(size * 0.72 + 6 * math.sin(t * 2 * math.pi + i * 0.5)))
        for i in range(15)
    ]
    d.line(pts, fill=(255, 140, 200, 230), width=max(4, size // 55))
    return img


def _render_pill_pop(size: int, t: float) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scale = 1.0 + 0.06 * abs(math.sin(t * 2 * math.pi))
    font = _font(max(40, int(size * 0.14)))
    text = "私信我"
    d0 = ImageDraw.Draw(img)
    bbox = d0.textbbox((0, 0), text, font=font, stroke_width=8)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, cy = size // 2, int(size * 0.48)
    pad_x, pad_y = int(28 * scale), int(18 * scale)
    box = (
        cx - tw // 2 - pad_x,
        cy - th // 2 - pad_y,
        cx + tw // 2 + pad_x,
        cy + th // 2 + pad_y,
    )
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        (box[0] + 4, box[1] + 7, box[2] + 4, box[3] + 7),
        radius=(box[3] - box[1]) // 2,
        fill=(100, 20, 50, 60),
    )
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(4)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=(255, 85, 150, 255), outline=(255, 255, 255, 255), width=5)
    # gloss
    gloss = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(gloss).rounded_rectangle(
        (box[0] + 10, box[1] + 6, box[2] - 10, box[1] + (box[3] - box[1]) // 2),
        radius=20,
        fill=(255, 255, 255, 70),
    )
    img.alpha_composite(gloss.filter(ImageFilter.GaussianBlur(1)))
    _outlined_text(
        img, text, (cx, cy), font,
        fill=(255, 255, 255, 255),
        stroke=(220, 40, 110, 255),
        stroke_w=3,
    )
    return img


def _render_heart_text(size: int, t: float) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s = 1.0 + 0.07 * abs(math.sin(t * 2 * math.pi))
    cx, cy = size // 2, int(size * 0.34)
    r = int(40 * s)
    d = ImageDraw.Draw(img)
    d.ellipse((cx - r - 12, cy - r, cx + 8, cy + r - 8), fill=(255, 80, 130, 255))
    d.ellipse((cx - 8, cy - r, cx + r + 12, cy + r - 8), fill=(255, 80, 130, 255))
    d.polygon([(cx - r - 10, cy + 6), (cx + r + 10, cy + 6), (cx, cy + r + 28)], fill=(255, 80, 130, 255))
    # white edge via slightly larger heart under? skip — keep clean
    font = _font(max(36, int(size * 0.13)))
    _outlined_text(
        img, "私信", (size // 2, int(size * 0.76)), font,
        fill=(255, 255, 255, 255), stroke=(255, 80, 140, 255), stroke_w=4,
    )
    return img


def _render_bell_text(size: int, t: float) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = size // 2, int(size * 0.34)
    d.ellipse((cx - 50, cy - 20, cx + 50, cy + 54), fill=(255, 210, 55, 255), outline=(255, 255, 255, 255), width=5)
    d.rectangle((cx - 11, cy - 42, cx + 11, cy - 16), fill=(255, 210, 55, 255))
    d.ellipse((cx - 15, cy - 54, cx + 15, cy - 30), fill=(255, 230, 120, 255), outline=(255, 255, 255, 255), width=3)
    d.ellipse((cx - 15, cy + 48, cx + 15, cy + 68), fill=(255, 175, 30, 255), outline=(255, 255, 255, 255), width=3)
    img = layer.rotate(10 * math.sin(t * 2 * math.pi), resample=Image.Resampling.BICUBIC, center=(cx, cy))
    font = _font(max(34, int(size * 0.12)))
    _outlined_text(
        img, "私信我", (size // 2, int(size * 0.78)), font,
        fill=(255, 80, 160, 255), stroke=(255, 255, 255, 255), stroke_w=4,
    )
    return img


def _render_tap_text(size: int, t: float) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bob = int(10 * math.sin(t * 2 * math.pi))
    font = _font(max(36, int(size * 0.12)))
    text = "戳我私信"
    d0 = ImageDraw.Draw(img)
    bbox = d0.textbbox((0, 0), text, font=font, stroke_width=6)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, cy = size // 2, int(size * 0.42) + bob
    box = (cx - tw // 2 - 24, cy - th // 2 - 14, cx + tw // 2 + 24, cy + th // 2 + 14)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=th, fill=(255, 120, 60, 250), outline=(255, 255, 255, 255), width=4)
    _outlined_text(
        img, text, (cx, cy), font,
        fill=(255, 255, 255, 255), stroke=(200, 70, 25, 255), stroke_w=2,
    )
    # ring
    fx, fy = size // 2, int(size * 0.78)
    ring = int(12 + 10 * abs(math.sin(t * 2 * math.pi)))
    d.ellipse((fx - ring, fy - ring, fx + ring, fy + ring), outline=(255, 200, 80, 200), width=3)
    return img


def _render_estate_cta(size: int, t: float, text: str = "私信了解") -> Image.Image:
    """
    Soft coral capsule CTA (offline, no GIF).

    Bright short-video language: coral→orange fill, thick white ring,
    clean white type, gentle bob + sheen — not dark frosted glass.
    """
    label = (text or "私信了解").strip() or "私信了解"
    n = max(1, len(label))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    bob = int(round(size * 0.01 * math.sin(t * 2 * math.pi)))
    width_rel = 0.56 if n <= 1 else (0.68 if n <= 2 else (0.80 if n <= 3 else 0.90))
    pill_w = int(size * width_rel)
    pill_h = int(size * 0.22)
    cx, cy = size // 2, size // 2 + bob
    x0, y0 = cx - pill_w // 2, cy - pill_h // 2
    x1, y1 = cx + pill_w // 2, cy + pill_h // 2
    radius = pill_h // 2

    # Soft warm shadow
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x0 + 2, y0 + 5, x1 + 2, y1 + 8),
        radius=radius,
        fill=(120, 30, 40, 90),
    )
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=max(5, size // 50))))

    # Coral body
    body = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(body)
    bd.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(255, 72, 88, 250))
    # Soft vertical depth (lighter top)
    hi = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(hi).rounded_rectangle(
        (x0 + 2, y0 + 2, x1 - 2, y0 + pill_h // 2),
        radius=max(2, radius - 2),
        fill=(255, 255, 255, 55),
    )
    body.alpha_composite(hi.filter(ImageFilter.GaussianBlur(radius=max(2, pill_h // 8))))
    # Thick white ring
    ImageDraw.Draw(body).rounded_rectangle(
        (x0, y0, x1, y1),
        radius=radius,
        outline=(255, 255, 255, 255),
        width=max(4, size // 70),
    )
    # Sheen sweep
    sheen_x = x0 + int((pill_w + pill_h) * ((t + 0.2) % 1.0)) - pill_h // 2
    sheen = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    band = max(8, pill_w // 6)
    for i, a in enumerate((0, 35, 70, 35, 0)):
        xx = sheen_x + i * (band // 4)
        sd.rectangle((xx, y0 + 3, xx + band // 4, y1 - 3), fill=(255, 255, 255, a))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=255)
    sheen.putalpha(ImageChops.multiply(sheen.split()[-1], mask))
    body.alpha_composite(sheen.filter(ImageFilter.GaussianBlur(radius=max(2, size // 80))))
    img.alpha_composite(body)

    font_rel = 0.125 if n <= 1 else (0.112 if n <= 2 else (0.100 if n <= 3 else 0.090))
    font = _font(max(34, int(size * font_rel)))
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = cx - tw // 2
    ty = cy - th // 2 - max(1, size // 220)
    draw.text(
        (tx, ty), label, font=font,
        fill=(255, 255, 255, 255),
        stroke_width=max(2, size // 140),
        stroke_fill=(160, 30, 45, 220),
    )
    return img


def _render_frame(kind: str, size: int, t: float, text: str | None = None) -> Image.Image:
    if kind == "estate_cta":
        return _render_estate_cta(size, t, text=text or "私信了解")
    if kind == "bubble_pink":
        return _render_bubble_pink(size, t)
    if kind == "text_pink":
        return _render_text_pink(size, t)
    if kind == "pill_pop":
        return _render_pill_pop(size, t)
    if kind == "heart_text":
        return _render_heart_text(size, t)
    if kind == "bell_text":
        return _render_bell_text(size, t)
    if kind == "tap_text":
        return _render_tap_text(size, t)
    return _render_bubble_pink(size, t)


def _emoji_cache_dir(stickers_dir: Path) -> Path:
    d = Path(stickers_dir) / "external" / "noto"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_emoji_gif(emoji_id: str, stickers_dir: Path) -> Path:
    """Download Noto Animated Emoji GIF if missing (transparent loop)."""
    dest = _emoji_cache_dir(stickers_dir) / f"{emoji_id}_512.gif"
    if dest.is_file() and dest.stat().st_size > 10_000:
        return dest
    url = f"{NOTO_EMOJI_BASE}/{emoji_id}/512.gif"
    print(f"  download emoji {emoji_id}: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "clip-stickers/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        dest.write_bytes(resp.read())
    if dest.stat().st_size < 5_000:
        raise RuntimeError(f"emoji download too small: {dest}")
    return dest


def _load_gif_rgba_frames(path: Path, max_frames: int = 48) -> list[Image.Image]:
    """Decode GIF frames to RGBA (Noto emoji frames are self-contained)."""
    im = Image.open(path)
    frames: list[Image.Image] = []
    for i, frame in enumerate(ImageSequence.Iterator(im)):
        if i >= max_frames:
            break
        # convert() handles palette transparency → alpha
        frames.append(frame.convert("RGBA"))
    if not frames:
        frames = [Image.new("RGBA", (512, 512), (0, 0, 0, 0))]
    return frames


def _composite_emoji_badge(
    emoji_frames: list[Image.Image],
    text: str,
    size: int,
    emoji_scale: float,
    frame_i: int,
) -> Image.Image:
    """
    Horizontal CTA chip: [small animated icon] + [大号 私信我] inside one pill.

    Avoids “huge emoji + tiny label” layout that looked sparse on video.
    Canvas is square for pack scaling; content is a centered horizontal badge.
    """
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    em = emoji_frames[frame_i % len(emoji_frames)]

    # Horizontal chip: text is the hero; icon is a small accent on the left
    font_size = max(40, int(size * 0.17))
    font = _font(font_size)
    stroke_w = max(3, font_size // 9)
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    bbox = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_w + 2)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    icon_h = max(44, int(size * min(0.28, max(0.18, float(emoji_scale) * 0.65))))
    icon_h = min(icon_h, int(th * 1.15) + 8)
    em_r = em.resize((icon_h, icon_h), Image.Resampling.LANCZOS)

    pad_l, pad_r, pad_y = 16, 26, 16
    gap = 12
    badge_h = max(icon_h + pad_y * 2, th + pad_y * 2)
    badge_w = pad_l + icon_h + gap + tw + pad_r

    t = frame_i / max(1, len(emoji_frames))
    bob = int(5 * math.sin(t * 2 * math.pi))
    scale_pop = 1.0 + 0.028 * math.sin(t * 2 * math.pi * 2)
    badge_w = int(badge_w * scale_pop)
    badge_h = int(badge_h * scale_pop)

    bx = (size - badge_w) // 2
    by = (size - badge_h) // 2 + bob

    # Soft drop shadow under whole chip
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        (bx + 3, by + 5, bx + badge_w + 3, by + badge_h + 5),
        radius=badge_h // 2,
        fill=(0, 0, 0, 100),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=5))
    canvas.alpha_composite(shadow)

    # Pill body: pink fill + thick white ring (短视频角标语言)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(
        (bx, by, bx + badge_w, by + badge_h),
        radius=badge_h // 2,
        fill=(255, 72, 145, 250),
        outline=(255, 255, 255, 255),
        width=max(4, badge_h // 18),
    )
    # Inner soft highlight
    inset = max(3, badge_h // 20)
    d.rounded_rectangle(
        (bx + inset, by + inset, bx + badge_w - inset, by + badge_h // 2),
        radius=badge_h // 3,
        fill=(255, 160, 200, 55),
    )

    # Icon on the left inside the pill
    icon_x = bx + pad_l
    icon_y = by + (badge_h - icon_h) // 2
    # white circular seat under emoji for contrast
    seat_pad = 4
    seat = (
        icon_x - seat_pad,
        icon_y - seat_pad,
        icon_x + icon_h + seat_pad,
        icon_y + icon_h + seat_pad,
    )
    d.ellipse(seat, fill=(255, 255, 255, 235))
    canvas.alpha_composite(em_r, dest=(icon_x, icon_y))

    # Text to the right of icon — dominant
    text_cx = icon_x + icon_h + gap + tw // 2
    text_cy = by + badge_h // 2
    _outlined_text(
        canvas,
        text,
        (text_cx, text_cy),
        font,
        fill=(255, 255, 255, 255),
        stroke=(190, 30, 95, 255),
        stroke_w=max(2, stroke_w // 2),
        outer=(255, 255, 255, 255),
        outer_w=stroke_w,
    )
    return canvas


def _subsample_frames(frames: list[Image.Image], target_n: int) -> list[Image.Image]:
    if len(frames) <= target_n:
        return frames
    step = len(frames) / target_n
    return [frames[int(i * step)] for i in range(target_n)]


def _draw_huazi_cta_line(
    canvas: Image.Image,
    label: str,
    center: tuple[int, int],
    font: ImageFont.ImageFont,
    *,
    size: int,
    theme: str = "warm",
) -> tuple[int, int, int, int]:
    """
    One-line short-video 花字 for CTA — outer → mid → fill.
    Returns ink bbox (x0,y0,x1,y1) for sparkle placement.
    """
    pal = HUAZI_THEMES.get(theme) or HUAZI_THEMES["warm"]
    draw = ImageDraw.Draw(canvas)
    outer_w = max(5, size // 28)
    mid_w = max(3, size // 48)
    bbox = draw.textbbox((0, 0), label, font=font, stroke_width=outer_w)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = center[0] - tw // 2
    y = center[1] - th // 2

    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.text(
        (x, y),
        label,
        font=font,
        fill=pal["glow"],
        stroke_width=outer_w + 4,
        stroke_fill=pal["glow_stroke"],
    )
    canvas.alpha_composite(
        glow.filter(ImageFilter.GaussianBlur(radius=max(3, size // 60)))
    )

    draw = ImageDraw.Draw(canvas)
    draw.text(
        (x, y),
        label,
        font=font,
        fill=pal["outer"],
        stroke_width=outer_w,
        stroke_fill=pal["outer"],
    )
    draw.text(
        (x, y),
        label,
        font=font,
        fill=pal["mid"],
        stroke_width=mid_w,
        stroke_fill=pal["mid"],
    )
    draw.text((x, y), label, font=font, fill=pal["fill"])
    return (x - outer_w, y - outer_w, x + tw + outer_w, y + th + outer_w)


def _composite_estate_gif_cta(
    emoji_frames: list[Image.Image] | None,
    sparkle_frames: list[Image.Image] | None,
    text: str,
    size: int,
    frame_i: int,
    *,
    layout: str = "huazi",
    theme: str = "warm",
) -> Image.Image:
    """
    Default ``huazi`` layout: bold 花字 CTA + small Noto sparkles only.

    Optional ``bubble`` layout keeps large emoji + label for alternate style.
    """
    label = (text or "私信了解").strip() or "私信了解"
    n = max(1, len(label))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    n_frames = max(
        12,
        len(sparkle_frames or []) or 0,
        len(emoji_frames or []) or 0,
    )
    t = frame_i / max(1, n_frames)
    bob = int(size * 0.012 * math.sin(t * 2 * math.pi))

    if layout == "bubble" and emoji_frames:
        # Alternate: compact emoji above 花字
        em = emoji_frames[frame_i % len(emoji_frames)]
        emoji_h = int(size * 0.36)
        em_r = em.resize((emoji_h, emoji_h), Image.Resampling.LANCZOS)
        ex = (size - emoji_h) // 2
        ey = int(size * 0.06) + bob
        glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse(
            (ex, ey + emoji_h // 4, ex + emoji_h, ey + emoji_h),
            fill=(255, 100, 80, 50),
        )
        canvas.alpha_composite(
            glow.filter(ImageFilter.GaussianBlur(radius=max(6, size // 35)))
        )
        canvas.alpha_composite(em_r, dest=(ex, ey))
        text_cy = ey + emoji_h + int(size * 0.06)
    else:
        text_cy = size // 2 + bob

    font_rel = 0.168 if n <= 2 else (0.148 if n <= 3 else 0.128)
    font = _font(max(40, int(size * font_rel)))
    ink = _draw_huazi_cta_line(
        canvas, label, (size // 2, text_cy), font, size=size, theme=theme
    )

    # Noto sparkles as accents only — hug the text bbox, not a second hero
    if sparkle_frames:
        sp = sparkle_frames[frame_i % len(sparkle_frames)]
        ix0, iy0, ix1, iy1 = ink
        iw, ih = max(1, ix1 - ix0), max(1, iy1 - iy0)
        anchors = (
            (ix1 - iw * 0.05, iy0 - ih * 0.15, 0.20, 0.0),
            (ix0 - iw * 0.08, iy0 + ih * 0.1, 0.15, 0.35),
            (ix1 - iw * 0.02, iy1 - ih * 0.25, 0.14, 0.7),
        )
        for ax, ay, sc, phase in anchors:
            twinkle = 0.45 + 0.55 * (
                0.5 + 0.5 * math.sin(t * 2 * math.pi + phase * 6.28)
            )
            sh_sz = max(16, int(size * sc * (0.8 + 0.25 * twinkle)))
            sp_r = sp.resize((sh_sz, sh_sz), Image.Resampling.LANCZOS)
            r, g, b, a = sp_r.split()
            a = a.point(lambda p, tw=twinkle: int(p * tw))
            sp_r = Image.merge("RGBA", (r, g, b, a))
            dx = int(ax - sh_sz / 2)
            dy = int(ay - sh_sz / 2) + bob // 2
            canvas.alpha_composite(sp_r, dest=(dx, dy))

    return canvas


def generate_emoji_sticker_apng(
    style_id: str,
    out_path: Path,
    stickers_dir: Path,
    *,
    size: int = 480,
    fps: int = 12,
) -> Path:
    spec = STICKER_SPECS[style_id]
    emoji_id = str(spec["emoji_id"])
    text = str(spec.get("text") or "私信我")
    emoji_scale = float(spec.get("emoji_scale") or 0.6)
    gif_path = ensure_emoji_gif(emoji_id, stickers_dir)
    emoji_frames = _load_gif_rgba_frames(gif_path, max_frames=36)
    # subsample to ~1.2s loop at fps
    target_n = max(12, int(fps * 1.2))
    emoji_frames = _subsample_frames(emoji_frames, target_n)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = out_path.parent / f".frames_{style_id}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(len(emoji_frames)):
        frame = _composite_emoji_badge(emoji_frames, text, size, emoji_scale, i)
        p = frames_dir / f"f_{i:03d}.png"
        frame.save(p, "PNG")
        paths.append(p)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", str(frames_dir / "f_%03d.png"),
        "-plays", "0",
        "-f", "apng",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    for p in paths:
        p.unlink(missing_ok=True)
    try:
        frames_dir.rmdir()
    except OSError:
        pass
    return out_path


def generate_estate_gif_cta_apng(
    style_id: str,
    out_path: Path,
    stickers_dir: Path,
    *,
    size: int = 480,
    fps: int = 12,
    text: str | None = None,
) -> Path:
    """Bake 花字 CTA + optional Noto sparkle/emoji GIFs into looping APNG."""
    spec = STICKER_SPECS.get(style_id) or STICKER_SPECS[DEFAULT_STICKER]
    emoji_id = str(spec.get("emoji_id") or "").strip()
    sparkle_id = str(spec.get("sparkle_id") or "2728")
    layout = str(spec.get("layout") or "huazi").strip().lower()
    theme = str(spec.get("theme") or "warm").strip().lower()
    if theme not in HUAZI_THEMES:
        theme = "warm"
    label = (text if text is not None else str(spec.get("text") or "私信了解")).strip()
    if not label:
        label = "私信了解"

    emoji_frames: list[Image.Image] | None = None
    if emoji_id and layout == "bubble":
        try:
            emoji_frames = _load_gif_rgba_frames(
                ensure_emoji_gif(emoji_id, stickers_dir), max_frames=40
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: emoji gif unavailable ({exc})")

    sparkle_frames: list[Image.Image] | None = None
    try:
        sparkle_frames = _load_gif_rgba_frames(
            ensure_emoji_gif(sparkle_id, stickers_dir), max_frames=40
        )
    except Exception as exc:  # noqa: BLE001 — offline fallback
        print(f"  warn: sparkle gif unavailable ({exc})")

    target_n = max(12, int(fps * 1.25))
    if emoji_frames:
        emoji_frames = _subsample_frames(emoji_frames, target_n)
    if sparkle_frames:
        sparkle_frames = _subsample_frames(sparkle_frames, target_n)
    n = max(
        target_n,
        len(emoji_frames or []),
        len(sparkle_frames or []),
        12,
    )
    # Pad frame count when only sparkles / pure procedural
    if not emoji_frames and not sparkle_frames:
        n = target_n

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = out_path.parent / f".frames_{style_id}_gif"
    frames_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(n):
        frame = _composite_estate_gif_cta(
            emoji_frames,
            sparkle_frames,
            label,
            size,
            i,
            layout=layout if emoji_frames else "huazi",
            theme=theme,
        )
        p = frames_dir / f"f_{i:03d}.png"
        frame.save(p, "PNG")
        paths.append(p)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", str(frames_dir / "f_%03d.png"),
        "-plays", "0",
        "-f", "apng",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    for p in paths:
        p.unlink(missing_ok=True)
    try:
        frames_dir.rmdir()
    except OSError:
        pass
    return out_path


def generate_sticker_apng(
    style_id: str,
    out_path: Path,
    *,
    size: int = 480,
    fps: int = 12,
    seconds: float = 1.1,
    stickers_dir: Path | None = None,
    text: str | None = None,
) -> Path:
    if style_id not in STICKER_SPECS:
        raise ValueError(f"Unknown sticker style: {style_id}")
    kind = STICKER_SPECS[style_id]["kind"]
    out_path = Path(out_path)
    base = stickers_dir or out_path.parent
    if kind == "emoji_badge":
        return generate_emoji_sticker_apng(
            style_id, out_path, base, size=size, fps=fps
        )
    if kind == "estate_gif_cta":
        try:
            return generate_estate_gif_cta_apng(
                style_id, out_path, base, size=size, fps=fps, text=text
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: gif CTA failed ({exc}), fallback to plain pill")
            kind = "estate_cta"

    label = (text if text is not None else str(STICKER_SPECS[style_id].get("text") or "私信")).strip()
    if not label:
        label = "私信"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = out_path.parent / f".frames_{style_id}"
    frames_dir.mkdir(parents=True, exist_ok=True)

    hi = int(size * 1.5)
    n = max(10, int(round(fps * seconds)))
    paths: list[Path] = []
    for i in range(n):
        t = i / n
        frame = _render_frame(kind, hi, t, text=label)
        if hi != size:
            frame = frame.resize((size, size), Image.Resampling.LANCZOS)
        p = frames_dir / f"f_{i:03d}.png"
        frame.save(p, "PNG")
        paths.append(p)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", str(frames_dir / "f_%03d.png"),
        "-plays", "0",
        "-f", "apng",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    for p in paths:
        p.unlink(missing_ok=True)
    try:
        frames_dir.rmdir()
    except OSError:
        pass
    return out_path


def ensure_builtin_stickers(stickers_dir: Path, *, force: bool = False) -> dict[str, Path]:
    stickers_dir = Path(stickers_dir)
    stickers_dir.mkdir(parents=True, exist_ok=True)
    # Prefer already-downloaded Noto GIFs from candidate cache
    cand = stickers_dir / "_candidates"
    noto = _emoji_cache_dir(stickers_dir)
    if cand.is_dir():
        for emoji_id in ("1f4ac", "1f48c", "1f449", "2764_fe0f"):
            src = cand / f"{emoji_id}_512.gif"
            dst = noto / f"{emoji_id}_512.gif"
            if src.is_file() and (not dst.is_file() or dst.stat().st_size < src.stat().st_size):
                dst.write_bytes(src.read_bytes())

    result: dict[str, Path] = {}
    # Generate emoji badges first (preferred), then procedural fallbacks
    for style_id in STICKER_SPECS:
        path = stickers_dir / f"{style_id}.apng"
        min_size = 30_000 if STICKER_SPECS[style_id]["kind"] == "emoji_badge" else 20_000
        if force or not path.is_file() or path.stat().st_size < min_size:
            print(f"  generate sticker: {style_id}")
            generate_sticker_apng(style_id, path, stickers_dir=stickers_dir)
        result[style_id] = path
    return result
