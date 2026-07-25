"""Classify frames into room / move / fast and build time segments."""
from __future__ import annotations

from typing import Any

import numpy as np

from .analyze import smooth


def classify_frames(rows: list[dict], cfg: dict[str, Any]) -> list[str]:
    """
    Per-frame label:
      room  – meaningful space / scenic pans (keep near original speed)
      move  – transitional walk
      fast  – corners / blank walls / low-structure dashes
    """
    c = cfg["classify"]
    k = int(cfg["analysis"]["smooth_window"])

    motion = smooth(np.array([r["motion"] for r in rows]), k)
    edge = smooth(np.array([r["edge"] for r in rows]), k)
    std = smooth(np.array([r["std"] for r in rows]), k)

    wall_edge_max = float(c["wall_edge_max"])
    wall_std_max = float(c["wall_std_max"])
    very_fast_motion = float(c["very_fast_motion"])
    transitional_motion = float(c["transitional_motion"])
    transitional_edge_max = float(c["transitional_edge_max"])
    # High edge + high motion = intentional balcony/cityscape pan, not a corner cut
    scenic_edge_min = float(c.get("scenic_edge_min", 0.16))

    labels: list[str] = []
    for i in range(len(rows)):
        m, e, s = float(motion[i]), float(edge[i]), float(std[i])
        wall = e < wall_edge_max and s < wall_std_max
        scenic = e >= scenic_edge_min
        # Only treat as dash/turn when motion is high AND scene is not information-rich
        very_fast = m > very_fast_motion and not scenic
        transitional = (
            m > transitional_motion
            and e < transitional_edge_max
            and not scenic
        )

        if wall:
            labels.append("fast")
        elif scenic and m > transitional_motion:
            # Dense outdoor/structure content while camera moves → protect as room
            labels.append("room")
        elif very_fast:
            labels.append("fast")
        elif transitional:
            labels.append("move")
        else:
            labels.append("room")
    return labels


def labels_to_segments(
    rows: list[dict],
    labels: list[str],
    duration: float,
    cfg: dict[str, Any],
) -> list[dict]:
    """
    Collapse consecutive labels into segments.

    Frame timestamps come from OpenCV (`idx / fps`) while ``duration`` is from
    ffprobe; clamp every cut into ``[0, duration]`` so VFR / rounded-FPS
    mismatch cannot produce ``t0 > t1`` and fail ``normalize_timeline``.
    """
    speeds = cfg["speeds"]
    duration = float(duration)
    if duration <= 0 or not labels:
        return []

    def _clamp_t(t: float) -> float:
        return min(duration, max(0.0, float(t)))

    segs: list[dict] = []
    cur = labels[0]
    t0 = _clamp_t(rows[0]["t"])
    for i in range(1, len(labels)):
        if labels[i] != cur:
            t1 = _clamp_t(rows[i]["t"])
            if t1 > t0 + 1e-9:
                segs.append(
                    {
                        "t0": t0,
                        "t1": t1,
                        "kind": cur,
                        "speed": float(speeds[cur]),
                    }
                )
                t0 = t1
            cur = labels[i]
    if duration > t0 + 1e-9:
        segs.append(
            {
                "t0": t0,
                "t1": duration,
                "kind": cur,
                "speed": float(speeds[cur]),
            }
        )
    elif not segs:
        # Degenerate analysis (all cuts collapsed): still cover full media.
        segs.append(
            {
                "t0": 0.0,
                "t1": duration,
                "kind": labels[0],
                "speed": float(speeds[labels[0]]),
            }
        )
    return segs


def _same_play(a: dict, b: dict, speed_eps: float = 1e-3) -> bool:
    """True if two segments can be merged (same kind and speed)."""
    return a["kind"] == b["kind"] and abs(
        float(a["speed"]) - float(b["speed"])
    ) < speed_eps


def coalesce_adjacent(segs: list[dict]) -> list[dict]:
    """Merge only adjacent segments with identical kind and speed."""
    out: list[dict] = []
    for seg in segs:
        item = dict(seg)
        if out and _same_play(out[-1], item):
            out[-1]["t1"] = item["t1"]
        else:
            out.append(item)
    return out


def absorb_short_auto(segs: list[dict], min_dur: float) -> list[dict]:
    """Absorb auto-classified short islands into the longer neighbor."""
    if not segs:
        return []
    segs = [dict(s) for s in segs]
    while len(segs) > 1:
        short_index = None
        for i, s in enumerate(segs):
            if float(s["t1"]) - float(s["t0"]) < min_dur:
                short_index = i
                break
        if short_index is None:
            break
        i = short_index
        if i == 0:
            target = 1
        elif i == len(segs) - 1:
            target = i - 1
        else:
            left = float(segs[i - 1]["t1"]) - float(segs[i - 1]["t0"])
            right = float(segs[i + 1]["t1"]) - float(segs[i + 1]["t0"])
            target = i - 1 if left >= right else i + 1
        if target < i:
            segs[target]["t1"] = segs[i]["t1"]
        else:
            segs[target]["t0"] = segs[i]["t0"]
        segs.pop(i)
        segs = coalesce_adjacent(segs)
    return coalesce_adjacent(segs)


def merge_short(segs: list[dict], min_dur: float) -> list[dict]:
    """Backward-compatible alias for automatic short-island absorption."""
    return absorb_short_auto(segs, min_dur)


def _segment_mean_edge(rows: list[dict], t0: float, t1: float) -> float:
    """Average edge density for frames overlapping [t0, t1)."""
    vals = [
        float(r["edge"])
        for r in rows
        if t0 - 1e-9 <= float(r["t"]) < t1 - 1e-9
    ]
    if not vals:
        # include last frame if segment ends at video end
        vals = [
            float(r["edge"])
            for r in rows
            if t0 - 1e-9 <= float(r["t"]) <= t1 + 1e-9
        ]
    return float(np.mean(vals)) if vals else 0.0


def apply_room_hold_ramp(
    segs: list[dict],
    cfg: dict[str, Any],
    rows: list[dict] | None = None,
) -> list[dict]:
    """
    Scheme A: within each continuous room segment, ramp up speed by dwell time
    so long static holds on the same space feel less draggy. No frames dropped.

    Scenic segments (high mean edge, e.g. balcony cityscape) can skip the ramp
    so outdoor views are not gradually sped up.
    """
    pacing = cfg.get("pacing") or {}
    if not pacing.get("enabled", True):
        return segs
    ramp = list(pacing.get("room_hold_ramp") or [])
    if not ramp:
        return segs

    steps = sorted(
        (
            {"after": float(s["after"]), "speed": float(s["speed"])}
            for s in ramp
        ),
        key=lambda s: s["after"],
    )
    # ensure a step at 0
    if steps[0]["after"] > 1e-9:
        steps.insert(0, {"after": 0.0, "speed": float(cfg["speeds"]["room"])})

    scenic_skip = bool(pacing.get("scenic_skip_hold_ramp", True))
    scenic_skip_edge = float(
        pacing.get(
            "scenic_skip_edge_min",
            cfg.get("classify", {}).get("scenic_edge_min", 0.18),
        )
    )
    room_speed = float(cfg["speeds"]["room"])

    out: list[dict] = []
    for seg in segs:
        if seg["kind"] != "room":
            out.append(dict(seg))
            continue

        t0 = float(seg["t0"])
        t1 = float(seg["t1"])
        dur = t1 - t0
        if dur <= 1e-9:
            continue

        # Build cut points inside [t0, t1] from ramp thresholds
        cuts = [t0]
        for step in steps[1:]:
            abs_t = t0 + step["after"]
            if t0 < abs_t < t1:
                cuts.append(abs_t)
        cuts.append(t1)
        cuts = sorted(set(cuts))

        for i in range(len(cuts) - 1):
            a, b = cuts[i], cuts[i + 1]
            if b <= a + 1e-9:
                continue
            # dwell time at midpoint of this sub-range (relative to room start)
            rel = ((a + b) / 2.0) - t0
            speed = steps[0]["speed"]
            for step in steps:
                if rel + 1e-9 >= step["after"]:
                    speed = step["speed"]
                else:
                    break
            # Per-slice scenic protect: dense outdoor structure stays 1x even late in hold
            if scenic_skip and rows is not None:
                avg_e = _segment_mean_edge(rows, a, b)
                if avg_e >= scenic_skip_edge:
                    speed = room_speed
            piece = {
                "t0": a,
                "t1": b,
                "kind": "room",
                "speed": float(speed),
            }
            if out and _same_play(out[-1], piece):
                out[-1]["t1"] = b
            else:
                out.append(piece)
    return out



def apply_overrides(
    segs: list[dict],
    overrides: list[dict],
    speeds: dict[str, float],
    duration: float,
) -> list[dict]:
    """
    Force kind on time ranges. Rebuild by cutting timeline at all boundaries.
    """
    if not overrides:
        return segs

    # Build base kind timeline from segs
    cuts = {0.0, float(duration)}
    for s in segs:
        cuts.add(float(s["t0"]))
        cuts.add(float(s["t1"]))
    for ov in overrides:
        start = min(float(duration), max(0.0, float(ov["start"])))
        end = min(float(duration), max(0.0, float(ov["end"])))
        if end <= start:
            continue
        cuts.add(start)
        cuts.add(end)
    points = sorted(cuts)

    def kind_at(t: float) -> str:
        # base from auto segs
        kind = segs[0]["kind"]
        for s in segs:
            if s["t0"] <= t < s["t1"] or (t == duration and s["t1"] == duration):
                kind = s["kind"]
                break
            if s["t0"] <= t <= s["t1"]:
                kind = s["kind"]
        # overrides (later wins)
        for ov in overrides:
            a = min(float(duration), max(0.0, float(ov["start"])))
            b = min(float(duration), max(0.0, float(ov["end"])))
            if b <= a:
                continue
            if a <= t < b or (t == duration and b >= duration and a < duration):
                kind = ov["kind"]
        return kind

    out: list[dict] = []
    for i in range(len(points) - 1):
        t0, t1 = points[i], points[i + 1]
        if t1 <= t0 + 1e-9:
            continue
        mid = (t0 + t1) / 2
        kind = kind_at(mid)
        if out and out[-1]["kind"] == kind:
            out[-1]["t1"] = t1
        else:
            out.append(
                {
                    "t0": t0,
                    "t1": t1,
                    "kind": kind,
                    "speed": float(speeds[kind]),
                }
            )
    return out


def validate_timeline(segs: list[dict], duration: float, eps: float = 1e-6) -> None:
    """Ensure segments cover the input exactly once with positive playback speed."""
    if duration <= 0:
        raise ValueError("duration must be > 0")
    if not segs:
        raise ValueError("segment timeline is empty")
    if abs(float(segs[0]["t0"])) > eps:
        raise ValueError("segment timeline must start at 0")
    previous = 0.0
    for i, seg in enumerate(segs):
        t0, t1 = float(seg["t0"]), float(seg["t1"])
        speed = float(seg["speed"])
        if abs(t0 - previous) > eps:
            raise ValueError(f"segment {i} has a gap or overlap at {t0:.6f}s")
        if t1 <= t0:
            raise ValueError(f"segment {i} has non-positive duration")
        if speed <= 0 or not np.isfinite(speed):
            raise ValueError(f"segment {i} has invalid speed")
        previous = t1
    if abs(previous - float(duration)) > eps:
        raise ValueError("segment timeline must end at media duration")


def normalize_timeline(
    segs: list[dict], duration: float, precision: int = 6
) -> list[dict]:
    """Quantize shared boundaries once so adjacent trims cannot overlap or gap."""
    if not segs:
        raise ValueError("segment timeline is empty")
    boundaries = [0.0]
    boundaries.extend(round(float(seg["t1"]), precision) for seg in segs[:-1])
    boundaries.append(float(duration))
    out: list[dict] = []
    for i, seg in enumerate(segs):
        item = dict(seg)
        item["t0"], item["t1"] = boundaries[i], boundaries[i + 1]
        out.append(item)
    validate_timeline(out, duration)
    return out


def build_segments(
    rows: list[dict],
    duration: float,
    cfg: dict[str, Any],
) -> list[dict]:
    """Full classify → merge → override → room-hold ramp pipeline."""
    labels = classify_frames(rows, cfg)
    segs = labels_to_segments(rows, labels, duration, cfg)
    segs = absorb_short_auto(segs, float(cfg["segments"]["min_duration"]))
    speeds = cfg["speeds"]
    for s in segs:
        s["speed"] = float(speeds[s["kind"]])
    segs = apply_overrides(
        segs,
        list(cfg.get("overrides") or []),
        speeds,
        duration,
    )
    segs = coalesce_adjacent(segs)
    for s in segs:
        s["speed"] = float(speeds[s["kind"]])
    segs = apply_room_hold_ramp(segs, cfg, rows=rows)
    segs = coalesce_adjacent(segs)
    return normalize_timeline(segs, duration)
