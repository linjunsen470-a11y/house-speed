"""Voiceover script lines and output-timeline cue layout."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# Rough Mandarin short-video pacing: ~4.5 characters per second.
_CHARS_PER_SEC = 4.5
_MIN_LINE_SEC = 0.8
_MAX_LINE_SEC = 6.0


def estimate_speech_seconds(text: str) -> float:
    """Estimate spoken duration from character count (no TTS required)."""
    cleaned = re.sub(r"\s+", "", str(text or "").strip())
    if not cleaned:
        return _MIN_LINE_SEC
    # Punctuation is short; count CJK/alnum only for pacing.
    chars = len(re.findall(r"[\w\u4e00-\u9fff]", cleaned, flags=re.UNICODE))
    chars = max(1, chars)
    sec = chars / _CHARS_PER_SEC
    return float(min(_MAX_LINE_SEC, max(_MIN_LINE_SEC, sec)))


def for_speech(text: str, *, price_speak: str = "") -> str:
    """Light normalization so TTS reads price hooks more naturally."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if price_speak and raw == price_speak:
        return price_speak
    out = raw
    # 单价5XXX / 5XXX → 五字头 style hooks
    out = re.sub(r"(\d)\s*[xX×]{2,3}", r"\1字头", out)
    out = re.sub(r"(\d)\s*XXX", r"\1字头", out, flags=re.IGNORECASE)
    return out


def _ensure_sentence(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return ""
    if t[-1] in "。！？!?…":
        return t
    return t + "。"


def lines_from_highlights(
    content: dict[str, Any],
    *,
    sticker_text: str = "",
    include_cta: bool = True,
    price_speak: str = "",
) -> list[str]:
    """Build short estate voiceover lines from pack.text content."""
    lines: list[str] = []
    title = str(content.get("title") or "").strip()
    if title:
        lines.append(_ensure_sentence(title))

    highlights = content.get("highlights") or []
    if isinstance(highlights, str):
        hl = [highlights.strip()] if highlights.strip() else []
    else:
        hl = [str(x).strip() for x in highlights if str(x).strip()]
    if hl:
        joined = "，".join(h.rstrip("。！？!?") for h in hl[:3])
        lines.append(_ensure_sentence(joined))

    price = str(content.get("price") or "").strip()
    if price:
        spoken_price = price_speak.strip() if price_speak.strip() else price
        lines.append(_ensure_sentence(spoken_price))

    if include_cta:
        cta = str(sticker_text or "").strip() or "私信了解"
        # Avoid "感兴趣的感兴趣的私信"
        if cta.startswith("感兴趣"):
            lines.append(_ensure_sentence(cta))
        else:
            lines.append(_ensure_sentence(f"感兴趣的{cta}"))
    return lines


def lines_from_script(script: Any) -> list[str]:
    """Normalize pack.voiceover.script into non-empty lines."""
    if script is None:
        return []
    if isinstance(script, str):
        parts = [p.strip() for p in re.split(r"[\n;]+", script) if p.strip()]
        return [_ensure_sentence(p) for p in parts]
    if isinstance(script, list):
        return [_ensure_sentence(str(x)) for x in script if str(x).strip()]
    raise ValueError("pack.voiceover.script must be a string or list of strings")


def lines_from_file(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"voiceover script file not found: {p}")
    text = p.read_text(encoding="utf-8")
    parts = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return [_ensure_sentence(p) for p in parts]


def collect_voice_lines(
    pack: dict[str, Any],
    content: dict[str, Any],
) -> list[str]:
    """Resolve voiceover lines from mode=highlights|script|file."""
    vo = pack.get("voiceover") or {}
    if not isinstance(vo, dict):
        vo = {}
    mode = str(vo.get("mode") or "highlights").strip().lower()
    if mode in {"script", "lines"}:
        lines = lines_from_script(vo.get("script") or vo.get("lines"))
    elif mode == "file":
        lines = lines_from_file(str(vo.get("file") or "").strip())
    else:
        sticker = pack.get("sticker") or {}
        sticker_text = str(sticker.get("text") or "") if isinstance(sticker, dict) else ""
        lines = lines_from_highlights(
            content,
            sticker_text=sticker_text,
            include_cta=bool(vo.get("include_cta", True)),
            price_speak=str(vo.get("price_speak") or ""),
        )
    # Cap to keep short-video VO within ~40s of speech
    max_lines = int(vo.get("max_lines") or 5)
    max_lines = max(1, min(8, max_lines))
    return lines[:max_lines]


def layout_cues(
    lines: list[str],
    *,
    duration: float,
    start: float = 1.2,
    end_pad: float = 2.5,
    gap: float = 0.35,
    fit: str = "pad",
    line_durations: list[float] | None = None,
) -> list[dict[str, Any]]:
    """
    Place lines on the *output* timeline.

    Returns cue dicts: id, text, start, end, speech (TTS text).
    Does not stretch short VO to fill the whole video (natural pacing).
    """
    clean = [str(x).strip() for x in lines if str(x).strip()]
    if not clean or duration <= 0:
        return []

    fit_mode = str(fit or "pad").strip().lower()
    if fit_mode not in {"pad", "speed", "trim"}:
        fit_mode = "pad"

    t0 = max(0.0, float(start))
    t1 = max(t0, float(duration) - max(0.0, float(end_pad)))
    available = t1 - t0
    if available < _MIN_LINE_SEC:
        return []

    if line_durations is not None and len(line_durations) == len(clean):
        durs = [max(0.05, float(d)) for d in line_durations]
    else:
        durs = [estimate_speech_seconds(ln) for ln in clean]

    n = len(clean)
    use_gap = max(0.0, float(gap))
    speech_total = sum(durs)
    total = speech_total + use_gap * max(0, n - 1)

    if total > available:
        if fit_mode == "speed":
            # Shrink timeline up to 1.25× speech speed (durations /= speed).
            speed = min(1.25, total / available)
            durs = [d / speed for d in durs]
            use_gap = use_gap / speed
            total = sum(durs) + use_gap * max(0, n - 1)
        if total > available:
            # Drop trailing lines (trim) or if still over after speed.
            while n > 1 and (sum(durs[:n]) + use_gap * max(0, n - 2)) > available:
                n -= 1
            clean = clean[:n]
            durs = durs[:n]
            speech_total = sum(durs)
            if speech_total > available:
                scale = available / speech_total
                durs = [d * scale for d in durs]
                use_gap = 0.0
            else:
                use_gap = min(use_gap, max(0.0, (available - speech_total) / max(1, n - 1)))

    cues: list[dict[str, Any]] = []
    t = t0
    for i, (line, d) in enumerate(zip(clean, durs)):
        end = min(t1, t + d)
        if end <= t + 0.05:
            break
        speech = for_speech(line)
        cues.append(
            {
                "id": f"c{i + 1}",
                "text": line,
                "speech": speech,
                # Display filled by captions.expand / write_ass (cleaned, no weak punct).
                "display": "",
                "start": round(t, 3),
                "end": round(end, 3),
            }
        )
        t = end + use_gap
        if t >= t1:
            break
    return cues


def validate_cues(
    cues: list[dict[str, Any]],
    duration: float,
    *,
    eps: float = 1e-3,
) -> None:
    """Ensure cues sit on [0, duration] without overlaps."""
    prev_end = 0.0
    for i, cue in enumerate(cues):
        start = float(cue["start"])
        end = float(cue["end"])
        if not (end > start + eps):
            raise ValueError(f"cue {i} has non-positive duration: {start}–{end}")
        if start < -eps or end > duration + eps:
            raise ValueError(
                f"cue {i} outside timeline [0, {duration}]: {start}–{end}"
            )
        if start + eps < prev_end:
            raise ValueError(
                f"cue {i} overlaps previous (prev_end={prev_end}, start={start})"
            )
        prev_end = end
