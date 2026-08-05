"""Place voiceover cues on the *output* timeline (roles, room bands, spread)."""
from __future__ import annotations

import re
from typing import Any

from .script import estimate_speech_seconds, for_speech
from .timeline_map import cut_points, room_bands, snap_time


_PRICE_HINT = re.compile(r"(总价|单价|万|元|价格|字头|\d)", re.I)
_CTA_HINT = re.compile(r"(私信|了解|看房|咨询|滴滴|联系)")


def assign_roles(lines: list[str]) -> list[str]:
    """Tag each line: hook | highlight | price | cta."""
    n = len(lines)
    if n == 0:
        return []
    roles = ["highlight"] * n
    roles[0] = "hook"
    if n == 1:
        return roles
    roles[-1] = "cta"
    # Prefer explicit price line
    price_idx = None
    for i, line in enumerate(lines):
        if _PRICE_HINT.search(line) and i not in (0, n - 1):
            price_idx = i
            break
    if price_idx is None and n >= 3:
        price_idx = n - 2
    if price_idx is not None:
        roles[price_idx] = "price"
    for i, line in enumerate(lines):
        if i == 0 or i == n - 1:
            continue
        if roles[i] == "price":
            continue
        if _CTA_HINT.search(line) and i == n - 1:
            roles[i] = "cta"
        else:
            if roles[i] != "price":
                roles[i] = "highlight"
    return roles


def resolve_chapter_time(
    chapter: dict[str, Any],
    *,
    duration: float,
    bands: list[dict[str, Any]],
    default_start: float,
) -> float | None:
    """Resolve one chapter anchor to an output-timeline second."""
    if "at" in chapter and chapter.get("at") is not None:
        raw = chapter["at"]
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s.startswith("end-") or s.startswith("end+"):
                try:
                    delta = float(s[4:])
                except ValueError:
                    return None
                return max(0.0, duration - abs(delta))
            if s.endswith("%"):
                try:
                    pct = float(s[:-1]) / 100.0
                except ValueError:
                    return None
                return max(0.0, min(duration, duration * pct))
            try:
                return max(0.0, float(s))
            except ValueError:
                return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        if 0.0 < v <= 1.0 and duration > 1.5:
            # treat as fraction of duration when clearly fractional
            return max(0.0, min(duration, duration * v))
        return max(0.0, min(duration, v))

    if chapter.get("at_room") is not None:
        rooms = room_bands(bands, min_out_dur=1.0)
        try:
            idx = int(chapter["at_room"])
        except (TypeError, ValueError):
            return None
        # 1-based for humans
        if idx >= 1:
            idx -= 1
        if 0 <= idx < len(rooms):
            return float(rooms[idx]["t0"]) + float(chapter.get("offset", 0.3))
        return None

    if chapter.get("after") == "intro":
        return default_start
    return None


def lines_from_chapters(
    chapters: list[Any],
    *,
    duration: float,
    bands: list[dict[str, Any]],
    default_start: float,
) -> list[dict[str, Any]]:
    """Return list of {text, role, preferred_start} from chapter entries."""
    out: list[dict[str, Any]] = []
    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            continue
        text = str(ch.get("text") or "").strip()
        if not text:
            continue
        t = resolve_chapter_time(
            ch, duration=duration, bands=bands, default_start=default_start
        )
        if t is None:
            t = default_start + i * 2.5
        role = str(ch.get("role") or "").strip().lower()
        if role not in {"hook", "highlight", "price", "cta"}:
            if i == 0:
                role = "hook"
            elif i == len(chapters) - 1:
                role = "cta"
            elif _PRICE_HINT.search(text):
                role = "price"
            else:
                role = "highlight"
        out.append({"text": text, "role": role, "preferred_start": float(t)})
    return out


def preferred_starts_for_roles(
    roles: list[str],
    *,
    duration: float,
    start: float,
    end_pad: float,
    bands: list[dict[str, Any]],
) -> list[float]:
    """Heuristic anchors per role on the output timeline."""
    t_end = max(start, duration - max(0.0, end_pad))
    rooms = room_bands(bands, min_out_dur=1.2)
    rooms_by_len = sorted(rooms, key=lambda b: -float(b["out_dur"]))

    hook_t = start
    if rooms:
        hl_t = float(rooms[0]["t0"]) + 0.25
    else:
        hl_t = start + 2.0
    if len(rooms) >= 2:
        price_t = float(rooms[1]["t0"]) + 0.2
    elif rooms_by_len:
        # mid of longest room
        b = rooms_by_len[0]
        price_t = float(b["t0"]) + min(2.0, float(b["out_dur"]) * 0.45)
    else:
        price_t = duration * 0.55
    cta_t = max(start, t_end - 2.8)

    # Ensure chronological soft order
    hl_t = max(hl_t, hook_t + 0.5)
    price_t = max(price_t, hl_t + 0.8)
    cta_t = max(cta_t, price_t + 0.8)
    cta_t = min(cta_t, max(start, t_end - 0.5))

    role_default = {
        "hook": hook_t,
        "highlight": hl_t,
        "price": price_t,
        "cta": cta_t,
    }
    # Multiple highlights: stagger within room bands
    hl_count = 0
    prefs: list[float] = []
    for role in roles:
        if role == "highlight":
            if hl_count < len(rooms):
                prefs.append(float(rooms[hl_count]["t0"]) + 0.25)
            else:
                prefs.append(hl_t + hl_count * 1.8)
            hl_count += 1
        else:
            prefs.append(role_default.get(role, hl_t))
    return prefs


def place_cues(
    items: list[dict[str, Any]],
    *,
    duration: float,
    start: float = 1.0,
    end_pad: float = 2.2,
    gap: float = 0.30,
    fit: str = "spread",
    coverage: float = 0.55,
    bands: list[dict[str, Any]] | None = None,
    snap: bool = True,
    snap_window: float = 0.15,
    line_durations: list[float] | None = None,
    max_gap: float = 0.55,
    max_pref_wait: float = 0.9,
) -> list[dict[str, Any]]:
    """
    Place voice lines on the output timeline.

    ``items`` entries: {text, role?, preferred_start?}
    Returns cue dicts with text/speech/start/end/role.

    max_gap: hard cap on silence between consecutive spoken lines.
    max_pref_wait: how long we may delay past the previous line to hit a
    room-band preference (avoids multi-second dead air waiting for a room).
    """
    bands = bands or []
    clean_items = [x for x in items if str(x.get("text") or "").strip()]
    if not clean_items or duration <= 0:
        return []

    t0 = max(0.0, float(start))
    t1 = max(t0, float(duration) - max(0.0, float(end_pad)))
    available = t1 - t0
    if available < 0.5:
        return []

    n = len(clean_items)
    texts = [str(x["text"]).strip() for x in clean_items]
    roles = [str(x.get("role") or "highlight") for x in clean_items]
    if line_durations is not None and len(line_durations) == n:
        durs = [max(0.35, float(d)) for d in line_durations]
    else:
        durs = [estimate_speech_seconds(t) for t in texts]

    # Preferred starts
    auto_prefs = preferred_starts_for_roles(
        roles, duration=duration, start=t0, end_pad=end_pad, bands=bands
    )
    prefs: list[float] = []
    for i, item in enumerate(clean_items):
        if item.get("preferred_start") is not None:
            prefs.append(float(item["preferred_start"]))
        else:
            prefs.append(auto_prefs[i])

    cuts = cut_points(bands) if snap else []
    if snap and cuts:
        prefs = [snap_time(p, cuts, window=snap_window) for p in prefs]

    fit_mode = str(fit or "pack").strip().lower()
    if fit_mode == "pad":
        fit_mode = "pack"  # alias: tight natural pacing
    use_gap = max(0.05, float(gap))
    gap_cap = max(use_gap, float(max_gap))
    pref_wait = max(0.0, float(max_pref_wait))
    # Keep *script order*. Prefer room bands only within max_pref_wait of cursor.
    placed_start = [0.0] * n
    cursor = t0
    keep: list[int] = []
    for i in range(n):
        earliest = max(cursor, t0)
        want = float(prefs[i])
        if want <= earliest:
            s = earliest
        else:
            # Soft attract to preferred time, but never open a huge hole.
            s = min(want, earliest + pref_wait)
            s = max(s, earliest)
        # Also enforce gap_cap relative to previous speech end
        if keep:
            prev = keep[-1]
            s = min(s, placed_start[prev] + durs[prev] + gap_cap)
            s = max(s, placed_start[prev] + durs[prev] + use_gap)
        if s + durs[i] > t1:
            s = max(t0, cursor)
            if s + durs[i] > t1 + 0.05:
                break
        placed_start[i] = s
        keep.append(i)
        cursor = s + durs[i] + use_gap
    if not keep:
        return []

    # Mild spread only: never push inter-cue gaps above gap_cap
    cov = max(0.35, min(0.85, float(coverage)))
    if fit_mode == "spread" and len(keep) >= 2:
        speech_k = sum(durs[i] for i in keep)
        first = placed_start[keep[0]]
        # Ideal span capped by max gaps between lines
        max_span = speech_k + gap_cap * (len(keep) - 1)
        target_span = min(available, max(speech_k + use_gap * (len(keep) - 1), cov * available))
        target_span = min(target_span, max_span)
        ideal_end = min(t1, first + target_span)
        current_end = placed_start[keep[-1]] + durs[keep[-1]]
        extra = ideal_end - current_end
        if extra > 0.12:
            # Only add into gaps that are still under gap_cap
            room_left = []
            for j in range(len(keep) - 1):
                a, b = keep[j], keep[j + 1]
                cur_gap = placed_start[b] - (placed_start[a] + durs[a])
                room_left.append(max(0.0, gap_cap - cur_gap))
            total_room = sum(room_left)
            if total_room > 1e-3:
                scale = min(1.0, extra / total_room)
                shift = 0.0
                for j, i in enumerate(keep):
                    placed_start[i] += shift
                    if j < len(keep) - 1:
                        shift += room_left[j] * scale
            overflow = placed_start[keep[-1]] + durs[keep[-1]] - t1
            if overflow > 0:
                for i in keep:
                    placed_start[i] = max(t0, placed_start[i] - overflow)

    cues: list[dict[str, Any]] = []
    for rank, i in enumerate(keep):
        s = round(max(t0, placed_start[i]), 3)
        e = round(min(t1, s + durs[i]), 3)
        if e <= s + 0.05:
            continue
        line = texts[i]
        cues.append(
            {
                "id": f"c{rank + 1}",
                "text": line,
                "speech": for_speech(line),
                "display": "",
                "role": roles[i],
                "start": s,
                "end": e,
            }
        )
    return cues
