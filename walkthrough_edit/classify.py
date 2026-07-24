"""Classify frames into room / move / fast and build time segments."""
from __future__ import annotations

from typing import Any

import numpy as np

from .analyze import smooth


def classify_frames(rows: list[dict], cfg: dict[str, Any]) -> list[str]:
    """
    Per-frame label:
      room  – meaningful space (keep near original speed)
      move  – transitional walk
      fast  – corners / blank walls / sharp turns
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

    labels: list[str] = []
    for i in range(len(rows)):
        m, e, s = float(motion[i]), float(edge[i]), float(std[i])
        wall = e < wall_edge_max and s < wall_std_max
        very_fast = m > very_fast_motion
        transitional = m > transitional_motion and e < transitional_edge_max

        if wall or very_fast:
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
    speeds = cfg["speeds"]
    segs: list[dict] = []
    cur = labels[0]
    t0 = float(rows[0]["t"])
    for i in range(1, len(labels)):
        if labels[i] != cur:
            segs.append(
                {
                    "t0": t0,
                    "t1": float(rows[i]["t"]),
                    "kind": cur,
                    "speed": float(speeds[cur]),
                }
            )
            cur = labels[i]
            t0 = float(rows[i]["t"])
    segs.append(
        {
            "t0": t0,
            "t1": float(duration),
            "kind": cur,
            "speed": float(speeds[cur]),
        }
    )
    return segs


def _same_play(a: dict, b: dict, speed_eps: float = 1e-3) -> bool:
    """True if two segments can be merged (same kind and speed)."""
    return a["kind"] == b["kind"] and abs(
        float(a["speed"]) - float(b["speed"])
    ) < speed_eps


def merge_short(segs: list[dict], min_dur: float) -> list[dict]:
    """Absorb short islands into the longer neighbor; re-merge identical play."""
    if not segs:
        return segs
    segs = [dict(s) for s in segs]
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, s in enumerate(segs):
            dur = s["t1"] - s["t0"]
            if dur >= min_dur:
                continue
            if i == 0:
                segs[1]["t0"] = s["t0"]
                segs.pop(0)
            else:
                segs[i - 1]["t1"] = s["t1"]
                segs.pop(i)
            changed = True
            break
        j = 0
        while j < len(segs) - 1:
            # Do not merge room-hold steps that only share kind but differ in speed
            if _same_play(segs[j], segs[j + 1]):
                segs[j]["t1"] = segs[j + 1]["t1"]
                segs.pop(j + 1)
            else:
                j += 1
    return segs


def apply_room_hold_ramp(segs: list[dict], cfg: dict[str, Any]) -> list[dict]:
    """
    Scheme A: within each continuous room segment, ramp up speed by dwell time
    so long static holds on the same space feel less draggy. No frames dropped.
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
        cuts = sorted(set(round(c, 6) for c in cuts))

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
        cuts.add(max(0.0, float(ov["start"])))
        cuts.add(min(float(duration), float(ov["end"])))
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
            a, b = float(ov["start"]), float(ov["end"])
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


def build_segments(
    rows: list[dict],
    duration: float,
    cfg: dict[str, Any],
) -> list[dict]:
    """Full classify → merge → override → room-hold ramp pipeline."""
    labels = classify_frames(rows, cfg)
    segs = labels_to_segments(rows, labels, duration, cfg)
    segs = merge_short(segs, float(cfg["segments"]["min_duration"]))
    speeds = cfg["speeds"]
    for s in segs:
        s["speed"] = float(speeds[s["kind"]])
    segs = apply_overrides(
        segs,
        list(cfg.get("overrides") or []),
        speeds,
        duration,
    )
    segs = merge_short(segs, float(cfg["segments"]["min_duration"]))
    for s in segs:
        # base speed by kind; room may be rewritten by hold ramp next
        s["speed"] = float(speeds[s["kind"]])
    # Scheme A: long room holds ramp up (must run after kind is final)
    segs = apply_room_hold_ramp(segs, cfg)
    # Only merge identical kind+speed so ramp steps stay distinct
    segs = merge_short(segs, float(cfg["segments"]["min_duration"]))
    if segs:
        segs[0]["t0"] = 0.0
        segs[-1]["t1"] = float(duration)
    return segs
