"""Stable bottom ASS captions (fixed anchor, cleaned display text)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# Weak punctuation removed on-screen (speech/TTS may keep full punctuation).
_WEAK_PUNCT = set("，,。；;：:、·“”\"'‘’（）()《》【】[]{}—－-～~…·!?")
# On-screen punctuation allowlist (strong tone only).
_KEEP_PUNCT = set("！？")

PROTECTED_TOKEN_RE = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}|"
    r"\d+(?:\.\d+)?%?|"
    r"\d+(?:/\d+)+|"
    r"\d+\s*㎡|"
    r"[A-Za-z]+(?:-[A-Za-z]+)*"
)

# Prefer not to break these estate phrases mid-line.
PROTECTED_PHRASES = (
    "南北通透",
    "私家花园",
    "私信了解",
    "私信看房",
    "黄埔悦辰壹号",
    "悦辰壹号",
    "刚需优选",
    "全新未入住",
    "舒适户型",
)


def _ass_time(seconds: float) -> str:
    """ASS timestamp H:MM:SS.cc (centiseconds)."""
    centiseconds = int(round(max(0.0, float(seconds)) * 100))
    hours, rem = divmod(centiseconds, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def clean_subtitle_text(text: str) -> str:
    """
    Short-video display cleanup (maoshan-style):

    - strip whitespace
    - drop weak punctuation (commas, periods, …)
    - keep ！？ and decimal points between digits
    """
    raw = "".join(str(text or "").split())
    if not raw:
        return ""
    chars: list[str] = []
    for index, char in enumerate(raw):
        if char in _KEEP_PUNCT:
            chars.append(char)
            continue
        if char == ".":
            prev_digit = index > 0 and raw[index - 1].isdigit()
            next_digit = index + 1 < len(raw) and raw[index + 1].isdigit()
            if prev_digit and next_digit:
                chars.append(char)
            continue
        if char in _WEAK_PUNCT:
            continue
        if char.isalnum() or "\u4e00" <= char <= "\u9fff" or char in "%㎡‰°+":
            chars.append(char)
            continue
        # Drop other decorative marks
    return "".join(chars)


def _display_width(text: str) -> int:
    return len(text)


def _tokenize_for_wrap(text: str) -> list[str]:
    tokens: list[str] = []
    cursor = 0
    while cursor < len(text):
        phrase = next((p for p in PROTECTED_PHRASES if text.startswith(p, cursor)), None)
        if phrase:
            tokens.append(phrase)
            cursor += len(phrase)
            continue
        match = PROTECTED_TOKEN_RE.match(text, cursor)
        if match:
            tokens.append(match.group(0))
            cursor = match.end()
            continue
        tokens.append(text[cursor])
        cursor += 1
    return [t for t in tokens if t]


def _join_tokens(tokens: list[str]) -> str:
    return "".join(tokens)


def _token_width(tokens: list[str]) -> int:
    return _display_width(_join_tokens(tokens))


def _best_line_break(tokens: list[str], max_one: int) -> int:
    candidates = [
        index
        for index in range(1, len(tokens))
        if _token_width(tokens[:index]) <= max_one
        and _token_width(tokens[index:]) <= max_one
    ]
    if not candidates:
        return max(1, len(tokens) // 2)
    target = max_one * 0.8
    return min(
        candidates,
        key=lambda i: (
            abs(_token_width(tokens[:i]) - _token_width(tokens[i:])),
            abs(_token_width(tokens[:i]) - target),
        ),
    )


def wrap_subtitle_text(
    text: str,
    *,
    max_one: int = 12,
    max_two: int = 21,
) -> str:
    """Clean + optional single \\N break for 2-line captions."""
    cleaned = clean_subtitle_text(text)
    if not cleaned:
        return ""
    if _display_width(cleaned) <= max_one:
        return cleaned
    tokens = _tokenize_for_wrap(cleaned)
    if _token_width(tokens) <= max_two:
        split_at = _best_line_break(tokens, max_one)
        return _join_tokens(tokens[:split_at]) + r"\N" + _join_tokens(tokens[split_at:])
    # Too long for one dialogue; caller should split cues — still return cleaned.
    return cleaned


def split_display_chunks(
    text: str,
    *,
    max_one: int = 12,
    max_two: int = 21,
) -> list[str]:
    """
    Split long cleaned text into display-safe chunks (each <= max_two chars).
    """
    cleaned = clean_subtitle_text(text)
    if not cleaned:
        return []
    tokens = _tokenize_for_wrap(cleaned)
    total = _token_width(tokens)
    if total <= max_two:
        return [wrap_subtitle_text(cleaned, max_one=max_one, max_two=max_two)]

    chunk_count = max(1, (total + max_two - 1) // max_two)
    target_width = max(8, (total + chunk_count - 1) // chunk_count)
    chunks: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        next_width = _token_width(current + [token])
        if current and _token_width(current) >= target_width and next_width <= max_two:
            chunks.append(current)
            current = [token]
        elif current and next_width > max_two:
            chunks.append(current)
            current = [token]
        else:
            current.append(token)
    if current:
        chunks.append(current)

    # Rebalance tiny tails
    while len(chunks) > 1 and _token_width(chunks[-1]) < 6:
        tail = chunks.pop()
        prev = chunks.pop()
        merged = prev + tail
        mid = max(1, len(merged) // 2)
        candidates = [
            i
            for i in range(1, len(merged))
            if _token_width(merged[:i]) <= max_two and _token_width(merged[i:]) <= max_two
        ]
        split_at = (
            min(candidates, key=lambda i: abs(_token_width(merged[:i]) - _token_width(merged[i:])))
            if candidates
            else mid
        )
        chunks.extend([merged[:split_at], merged[split_at:]])

    out: list[str] = []
    for chunk in chunks:
        joined = _join_tokens(chunk)
        out.append(wrap_subtitle_text(joined, max_one=max_one, max_two=max_two))
    return out


def expand_cues_for_display(
    cues: list[dict[str, Any]],
    *,
    max_one: int = 12,
    max_two: int = 21,
    split_long: bool = False,
) -> list[dict[str, Any]]:
    """
    Attach `display` text.

    Default: only wrap with \\N (same time window as speech) so captions
    stay locked to the spoken line. Optional split_long re-enables multi-cue.
    """
    expanded: list[dict[str, Any]] = []
    for cue in cues:
        raw = str(cue.get("text") or cue.get("speech") or "")
        if not split_long:
            display = wrap_subtitle_text(raw, max_one=max_one, max_two=max_two)
            if not display:
                continue
            item = dict(cue)
            item["display"] = display
            expanded.append(item)
            continue
        chunks = split_display_chunks(raw, max_one=max_one, max_two=max_two)
        if not chunks:
            continue
        start = float(cue["start"])
        end = float(cue["end"])
        if end <= start:
            continue
        if len(chunks) == 1:
            item = dict(cue)
            item["display"] = chunks[0]
            expanded.append(item)
            continue
        weights = [max(1, _display_width(c.replace(r"\N", ""))) for c in chunks]
        total_w = sum(weights)
        cursor = start
        for i, (chunk, w) in enumerate(zip(chunks, weights)):
            if i == len(chunks) - 1:
                piece_end = end
            else:
                piece_end = cursor + (end - start) * w / total_w
            item = dict(cue)
            item["id"] = f"{cue.get('id', 'c')}_{i + 1}"
            item["display"] = chunk
            item["start"] = round(cursor, 3)
            item["end"] = round(piece_end, 3)
            if i > 0:
                item.pop("audio", None)
                item.pop("audio_duration", None)
            expanded.append(item)
            cursor = piece_end
    return expanded


def fixed_margin_v(height: int, *, margin_v: int | None = None, margin_v_rel: float | None = None) -> int:
    """Single bottom margin for the whole video (pixels from bottom)."""
    h = max(16, int(height))
    if margin_v is not None and int(margin_v) > 0:
        mv = int(margin_v)
    elif margin_v_rel is not None and float(margin_v_rel) > 0:
        mv = int(round(h * float(margin_v_rel)))
    else:
        mv = int(round(h * 0.09))  # ~160px on 1920, ~86 on 960
    # Keep caption band inside frame: not flush to edge, not mid-screen.
    lo = max(48, int(h * 0.04))
    hi = max(lo + 8, int(h * 0.16))
    return max(lo, min(hi, mv))


def fixed_font_size(height: int, *, font_size: int | None = None, font_size_rel: float = 0.045) -> int:
    h = max(16, int(height))
    if font_size is not None and int(font_size) > 0:
        fs = int(font_size)
    else:
        fs = int(round(h * float(font_size_rel)))
    return max(22, min(int(h * 0.08), fs))


def write_ass_captions(
    cues: list[dict[str, Any]],
    out_path: Path,
    *,
    width: int,
    height: int,
    font_path: str = "",
    font_name: str = "Microsoft YaHei",
    font_size: int | None = None,
    font_size_rel: float = 0.045,
    margin_v: int | None = None,
    margin_v_rel: float | None = 0.09,
    margin_lr: int | None = None,
    stroke: bool = True,
    max_one: int = 12,
    max_two: int = 21,
    layout: str = "bottom_center",
    pos_x_rel: float = 0.50,
    **_ignored: Any,
) -> Path:
    """
    Write ASS with a *single* fixed bottom anchor for all cues.

    Uses {\\an2\\pos(cx,cy)} so line length cannot shift vertical placement.
    Default bottom_center; bottom_right uses pos_x_rel for a mild right bias.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w = max(16, int(width))
    h = max(16, int(height))
    fs = fixed_font_size(h, font_size=font_size, font_size_rel=font_size_rel)
    mv = fixed_margin_v(h, margin_v=margin_v, margin_v_rel=margin_v_rel)
    ml = int(margin_lr) if margin_lr is not None else max(36, int(round(w * 0.05)))
    outline = 3 if stroke else 0
    family = (font_name or "Microsoft YaHei").strip() or "Microsoft YaHei"
    _ = font_path

    layout_s = str(layout or "bottom_center").strip().lower()
    if layout_s in {"bottom_right", "right"}:
        xr = min(0.65, max(0.48, float(pos_x_rel)))
        cx = int(round(w * xr))
    else:
        cx = w // 2
    cy = h - mv
    pos_tag = rf"{{\an2\pos({cx},{cy})}}"

    display_cues = expand_cues_for_display(
        cues, max_one=max_one, max_two=max_two, split_long=False
    )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{family},{fs},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{outline},0,2,{ml},{ml},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header.rstrip(), ""]
    for cue in display_cues:
        start = float(cue["start"])
        end = float(cue["end"])
        if end <= start:
            continue
        display = str(cue.get("display") or "").strip()
        if not display:
            continue
        # Escape braces only; \\N already inserted by wrap.
        payload = display.replace("{", "").replace("}", "")
        # Event margins 0,0,0 → style defaults; position forced by \\pos
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{pos_tag}{payload}"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return out_path


def _escape_subtitles_path(path: Path) -> str:
    """Escape a filesystem path for ffmpeg subtitles=/fontsdir= values."""
    text = path.resolve().as_posix()
    text = text.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")
    return f"'{text}'"


def subtitles_filter(
    ass_path: Path,
    *,
    fonts_dir: Path | None = None,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """
    Build an ffmpeg `subtitles=` filter fragment (no stream labels).
    """
    main = _escape_subtitles_path(ass_path)
    parts = [f"subtitles=filename={main}"]
    if fonts_dir is not None and Path(fonts_dir).is_dir():
        parts.append(f"fontsdir={_escape_subtitles_path(Path(fonts_dir))}")
    if width and height and int(width) > 0 and int(height) > 0:
        parts.append(f"original_size={int(width)}x{int(height)}")
    return ":".join(parts)
