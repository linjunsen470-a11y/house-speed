"""Map Stage-A source segments onto the Stage-B *output* timeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_segments(path: Path | str | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("segments", "items", "segs"):
            raw = data.get(key)
            if isinstance(raw, list):
                return [x for x in raw if isinstance(x, dict)]
    return []


def map_segments_to_output(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert source-timeline segments (t0/t1 + speed) to output bands.

    Output t0/t1 are continuous from 0; out_dur = (t1-t0)/speed.
    """
    bands: list[dict[str, Any]] = []
    out_t = 0.0
    for seg in segs:
        try:
            src0 = float(seg.get("t0", seg.get("start", 0.0)))
            src1 = float(seg.get("t1", seg.get("end", src0)))
            speed = float(seg.get("speed", 1.0))
        except (TypeError, ValueError):
            continue
        if src1 <= src0 or speed <= 1e-9:
            continue
        out_dur = (src1 - src0) / speed
        kind = str(seg.get("kind") or "room").strip().lower() or "room"
        bands.append(
            {
                "kind": kind,
                "speed": speed,
                "src_t0": src0,
                "src_t1": src1,
                "t0": round(out_t, 6),
                "t1": round(out_t + out_dur, 6),
                "out_dur": out_dur,
            }
        )
        out_t += out_dur
    return bands


def room_bands(
    bands: list[dict[str, Any]],
    *,
    min_out_dur: float = 1.2,
) -> list[dict[str, Any]]:
    """Chronological room bands long enough for a highlight line."""
    rooms = [
        b
        for b in bands
        if b.get("kind") == "room" and float(b.get("out_dur") or 0) >= min_out_dur
    ]
    return sorted(rooms, key=lambda b: float(b["t0"]))


def cut_points(bands: list[dict[str, Any]]) -> list[float]:
    """Segment boundaries on the output timeline (for snap)."""
    pts: list[float] = []
    for b in bands:
        pts.append(float(b["t0"]))
        pts.append(float(b["t1"]))
    # unique sorted
    out: list[float] = []
    for p in sorted(pts):
        if not out or abs(p - out[-1]) > 1e-4:
            out.append(p)
    return out


def snap_time(t: float, cuts: list[float], *, window: float = 0.15) -> float:
    """Snap t to nearest cut within ±window seconds."""
    if not cuts or window <= 0:
        return t
    best = t
    best_d = window + 1.0
    for c in cuts:
        d = abs(c - t)
        if d <= window and d < best_d:
            best = c
            best_d = d
    return best
