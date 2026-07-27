"""Classify frames into room / move / fast and build time segments."""
from __future__ import annotations

from typing import Any

import numpy as np

from .analyze import smooth


def _segment_frame_vals(
    rows: list[dict], t0: float, t1: float, key: str
) -> list[float]:
    vals = [
        float(r[key])
        for r in rows
        if t0 - 1e-9 <= float(r["t"]) < t1 - 1e-9
    ]
    if vals:
        return vals
    return [
        float(r[key])
        for r in rows
        if t0 - 1e-9 <= float(r["t"]) <= t1 + 1e-9
    ]


def _segment_mean(rows: list[dict], t0: float, t1: float, key: str) -> float:
    vals = _segment_frame_vals(rows, t0, t1, key)
    return float(np.mean(vals)) if vals else 0.0


def _segment_percentile(
    rows: list[dict], t0: float, t1: float, key: str, q: float
) -> float:
    vals = _segment_frame_vals(rows, t0, t1, key)
    if not vals:
        return 0.0
    return float(np.percentile(vals, q * 100.0))


def is_flat_surface(
    edge: float,
    std: float,
    *,
    wall_edge_max: float,
    wall_std_max: float,
    flat_edge_soft: float | None = None,
) -> bool:
    """
    Color-agnostic low-information / flat surface (painted wall any color, door).

    Edge density is the primary cue. Do **not** require low luminance or
    low global std: soft shadows and paint grain often raise std while the
    frame is still an uninformative wall.
    """
    e = float(edge)
    s = float(std)
    # Strict low-edge: always flat (fixes high-std colored walls)
    if e < wall_edge_max:
        return True
    # Soft band: slightly more edge only if contrast stays low
    soft = float(flat_edge_soft) if flat_edge_soft is not None else wall_edge_max * 1.4
    if e < soft and s < wall_std_max:
        return True
    return False


def classify_frames(rows: list[dict], cfg: dict[str, Any]) -> list[str]:
    """
    Per-frame label with dual-gate logic:

      room  – high-structure holds / content pans (keep ~1x)
      move  – corridor / mid-structure sustained walk
      fast  – flat walls / low-structure dashes / fast corridor

    High structure always blocks fast (protect living rooms / bedrooms).
    Mid structure + sustained walk promotes move/fast (corridors).
    """
    c = cfg["classify"]
    k = int(cfg["analysis"]["smooth_window"])

    motion = smooth(np.array([r["motion"] for r in rows], dtype=float), k)
    edge = smooth(np.array([r["edge"] for r in rows], dtype=float), k)
    std = smooth(np.array([r["std"] for r in rows], dtype=float), k)
    times = np.array([float(r["t"]) for r in rows], dtype=float)
    n = len(rows)
    if n == 0:
        return []

    wall_edge_max = float(c["wall_edge_max"])
    wall_std_max = float(c["wall_std_max"])
    flat_edge_soft = float(c.get("flat_edge_soft", wall_edge_max * 1.4))
    very_fast_motion = float(c["very_fast_motion"])
    transitional_motion = float(c["transitional_motion"])

    # Dual-gate thresholds (with backward-compatible fallbacks)
    content_struct_min = float(
        c.get("content_struct_min", c.get("scenic_edge_min", 0.115))
    )
    dash_struct_max = float(c.get("dash_struct_max", 0.055))
    walk_motion_lo = float(c.get("walk_motion_lo", 6.5))
    corridor_struct_lo = float(c.get("corridor_struct_lo", 0.040))
    corridor_struct_hi = float(c.get("corridor_struct_hi", 0.110))
    corridor_sustain_sec = float(c.get("corridor_sustain_sec", 0.8))
    corridor_sustain_ratio = float(c.get("corridor_sustain_ratio", 0.65))
    corridor_fast_motion = float(c.get("corridor_fast_motion", 12.0))
    move_struct_max = float(
        c.get("move_struct_max", c.get("transitional_edge_max", 0.090))
    )
    # High structure (indoor furniture / outdoor scenic) never goes to fast.
    protect_struct_min = content_struct_min

    def _flat(e: float, s: float) -> bool:
        return is_flat_surface(
            e,
            s,
            wall_edge_max=wall_edge_max,
            wall_std_max=wall_std_max,
            flat_edge_soft=flat_edge_soft,
        )

    # Pre-compute walk-like frames for sustain windows
    walk_like = np.zeros(n, dtype=bool)
    for i in range(n):
        m = float(motion[i])
        e = float(edge[i])
        s = float(std[i])
        flat = _flat(e, s)
        mid_struct = corridor_struct_lo <= e <= corridor_struct_hi
        # Sustained walk band, or faster walk still in mid-structure corridor
        if flat:
            walk_like[i] = False
        elif mid_struct and m >= walk_motion_lo:
            walk_like[i] = True
        elif (
            e < protect_struct_min
            and e >= corridor_struct_lo
            and m >= walk_motion_lo
        ):
            walk_like[i] = True

    labels: list[str] = []
    for i in range(n):
        m = float(motion[i])
        e = float(edge[i])
        s = float(std[i])

        # --- D1: flat / low-info surface (any wall color; edge-primary) ---
        if _flat(e, s):
            labels.append("fast")
            continue

        # --- B: high structure → never accelerate (room / content pan) ---
        if e >= protect_struct_min:
            labels.append("room")
            continue

        # --- D2: low-structure dash / whip turn ---
        if m > very_fast_motion and e < dash_struct_max:
            labels.append("fast")
            continue

        # --- C: corridor — mid structure + sustained walk-like motion ---
        t_now = float(times[i])
        t_lo = t_now - corridor_sustain_sec
        # frames in sustain window ending at i
        j0 = i
        while j0 > 0 and float(times[j0 - 1]) >= t_lo - 1e-12:
            j0 -= 1
        window = walk_like[j0 : i + 1]
        sustain = (
            len(window) > 0
            and float(np.mean(window)) >= corridor_sustain_ratio
        )
        mid_struct = corridor_struct_lo <= e <= max(
            corridor_struct_hi, protect_struct_min - 1e-9
        )
        corridor_hit = sustain and mid_struct and m >= walk_motion_lo
        # Also allow mid-struct high-motion even if slightly above walk_hi
        if not corridor_hit and sustain and e < protect_struct_min:
            if corridor_struct_lo <= e and m >= walk_motion_lo:
                corridor_hit = True

        if corridor_hit:
            if m >= corridor_fast_motion:
                labels.append("fast")
            else:
                labels.append("move")
            continue

        # --- fallback transitional walk (relaxed vs legacy edge-only gate) ---
        if m > transitional_motion and e < move_struct_max:
            labels.append("move")
            continue

        # --- A: default hold / room ---
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
            segs[target]["kind"] = segs[target]["kind"]
            segs[target]["speed"] = segs[target]["speed"]
        else:
            segs[target]["t0"] = segs[i]["t0"]
        segs.pop(i)
        segs = coalesce_adjacent(segs)
    return coalesce_adjacent(segs)


def merge_short(segs: list[dict], min_dur: float) -> list[dict]:
    """Backward-compatible alias for automatic short-island absorption."""
    return absorb_short_auto(segs, min_dur)


def demote_high_struct_fast(
    segs: list[dict],
    rows: list[dict],
    content_struct_min: float,
    speeds: dict[str, float],
) -> list[dict]:
    """Downgrade fast segments whose frames still look content-rich."""
    if not segs or not rows:
        return segs
    out: list[dict] = []
    for seg in segs:
        item = dict(seg)
        if item["kind"] == "fast":
            mean_e = _segment_mean(rows, float(item["t0"]), float(item["t1"]), "edge")
            if mean_e >= content_struct_min:
                item["kind"] = "room"
                item["speed"] = float(speeds["room"])
        out.append(item)
    return coalesce_adjacent(out)


def demote_sandwich_fast(
    segs: list[dict],
    rows: list[dict],
    *,
    content_struct_min: float,
    max_fast_dur: float,
    speeds: dict[str, float],
    require_high_struct_neighbors: bool = True,
) -> list[dict]:
    """
    room — short fast — room with high-structure neighbors → demote fast to room.

    Asymmetric: only demotes when neighbors look like real rooms, so true
    corridor dashes between mid-structure stretches are kept.
    """
    if len(segs) < 3 or max_fast_dur <= 0:
        return segs
    segs = [dict(s) for s in segs]
    changed = True
    while changed:
        changed = False
        i = 1
        while i < len(segs) - 1:
            cur = segs[i]
            left, right = segs[i - 1], segs[i + 1]
            dur = float(cur["t1"]) - float(cur["t0"])
            if (
                cur["kind"] == "fast"
                and left["kind"] == "room"
                and right["kind"] == "room"
                and dur < max_fast_dur
            ):
                ok = True
                if require_high_struct_neighbors and rows:
                    le = _segment_mean(
                        rows, float(left["t0"]), float(left["t1"]), "edge"
                    )
                    re = _segment_mean(
                        rows, float(right["t0"]), float(right["t1"]), "edge"
                    )
                    ok = le >= content_struct_min and re >= content_struct_min
                if ok:
                    cur["kind"] = "room"
                    cur["speed"] = float(speeds["room"])
                    segs = coalesce_adjacent(segs)
                    changed = True
                    break
            i += 1
    return coalesce_adjacent(segs)


def demote_long_structured_fast(
    segs: list[dict],
    rows: list[dict],
    cfg: dict[str, Any],
) -> list[dict]:
    """
    Long fast segments that still have mid/high structure are usually corridors
    walked too quickly — demote to move (2.2x) instead of 3.0x whip.
    """
    if not segs or not rows:
        return segs
    c = cfg["classify"]
    speeds = cfg["speeds"]
    segs_cfg = cfg.get("segments") or {}
    min_sec = float(segs_cfg.get("structured_fast_demote_min_sec", 2.0))
    edge_min = float(
        segs_cfg.get(
            "structured_fast_demote_edge_min",
            max(0.08, float(c.get("corridor_struct_hi", 0.11)) * 0.75),
        )
    )
    out: list[dict] = []
    for seg in segs:
        item = dict(seg)
        dur = float(item["t1"]) - float(item["t0"])
        if item["kind"] == "fast" and dur + 1e-9 >= min_sec:
            mean_e = _segment_mean(rows, float(item["t0"]), float(item["t1"]), "edge")
            if mean_e >= edge_min:
                item["kind"] = "move"
                item["speed"] = float(speeds["move"])
        out.append(item)
    return coalesce_adjacent(out)


def promote_flat_rooms(
    segs: list[dict],
    rows: list[dict],
    cfg: dict[str, Any],
) -> list[dict]:
    """
    Promote room segments that are still low-edge flats (any wall color) to fast.

    Catches islands the frame smoother / neighbor absorb may leave as room
    when std is high from shadows but edge stays wall-like.
    """
    if not segs or not rows:
        return segs
    c = cfg["classify"]
    speeds = cfg["speeds"]
    wall_edge_max = float(c["wall_edge_max"])
    # Allow slightly above frame threshold after temporal averaging
    edge_cap = float(c.get("flat_promote_edge_max", wall_edge_max * 1.15))
    out: list[dict] = []
    for seg in segs:
        item = dict(seg)
        if item["kind"] == "room":
            mean_e = _segment_mean(rows, float(item["t0"]), float(item["t1"]), "edge")
            # p80 edge still low → whole segment is flat, not a brief wall flash
            p80_e = _segment_percentile(
                rows, float(item["t0"]), float(item["t1"]), "edge", 0.8
            )
            if mean_e < edge_cap and p80_e < edge_cap * 1.25:
                item["kind"] = "fast"
                item["speed"] = float(speeds["fast"])
        out.append(item)
    return coalesce_adjacent(out)


def promote_corridor_rooms(
    segs: list[dict],
    rows: list[dict],
    cfg: dict[str, Any],
) -> list[dict]:
    """
    Promote long mid-structure room runs that look like corridor walking
    into move/fast so hallways are not left at 1x.
    """
    if not segs or not rows:
        return segs
    c = cfg["classify"]
    speeds = cfg["speeds"]
    segs_cfg = cfg.get("segments") or {}
    min_sec = float(segs_cfg.get("corridor_promote_min_sec", 1.2))
    content_struct_min = float(
        c.get("content_struct_min", c.get("scenic_edge_min", 0.115))
    )
    corridor_struct_lo = float(c.get("corridor_struct_lo", 0.040))
    corridor_struct_hi = float(c.get("corridor_struct_hi", 0.110))
    walk_motion_lo = float(c.get("walk_motion_lo", 6.5))
    corridor_fast_motion = float(c.get("corridor_fast_motion", 12.0))

    out: list[dict] = []
    for seg in segs:
        item = dict(seg)
        dur = float(item["t1"]) - float(item["t0"])
        if item["kind"] == "room" and dur + 1e-9 >= min_sec:
            mean_e = _segment_mean(rows, float(item["t0"]), float(item["t1"]), "edge")
            mean_m = _segment_mean(
                rows, float(item["t0"]), float(item["t1"]), "motion"
            )
            p20_e = _segment_percentile(
                rows, float(item["t0"]), float(item["t1"]), "edge", 0.2
            )
            # Mid-structure sustained walk; not a dense living-room hold
            mid = (
                corridor_struct_lo <= mean_e <= corridor_struct_hi
                or (
                    mean_e < content_struct_min
                    and p20_e <= corridor_struct_hi
                    and mean_e >= corridor_struct_lo
                )
            )
            if mid and mean_m >= walk_motion_lo and mean_e < content_struct_min:
                if mean_m >= corridor_fast_motion:
                    item["kind"] = "fast"
                    item["speed"] = float(speeds["fast"])
                else:
                    item["kind"] = "move"
                    item["speed"] = float(speeds["move"])
        out.append(item)
    return coalesce_adjacent(out)


def absorb_short_fast(
    segs: list[dict],
    rows: list[dict] | None,
    min_fast_dur: float,
    wall_edge_max: float,
    speeds: dict[str, float],
) -> list[dict]:
    """
    Absorb short fast islands into neighbors unless they look like blank walls.

    True wall flashes may stay short; noisy mid-structure flickers should not.
    """
    if not segs or min_fast_dur <= 0:
        return segs
    segs = [dict(s) for s in segs]
    while len(segs) > 1:
        target_i = None
        for i, s in enumerate(segs):
            if s["kind"] != "fast":
                continue
            dur = float(s["t1"]) - float(s["t0"])
            if dur >= min_fast_dur:
                continue
            # Keep short wall-like fast
            if rows is not None:
                mean_e = _segment_mean(rows, float(s["t0"]), float(s["t1"]), "edge")
                if mean_e < wall_edge_max:
                    continue
            target_i = i
            break
        if target_i is None:
            break
        i = target_i
        if i == 0:
            neighbor = 1
        elif i == len(segs) - 1:
            neighbor = i - 1
        else:
            left = float(segs[i - 1]["t1"]) - float(segs[i - 1]["t0"])
            right = float(segs[i + 1]["t1"]) - float(segs[i + 1]["t0"])
            neighbor = i - 1 if left >= right else i + 1
        # Expand neighbor over the short fast; keep neighbor kind
        if neighbor < i:
            segs[neighbor]["t1"] = segs[i]["t1"]
        else:
            segs[neighbor]["t0"] = segs[i]["t0"]
        segs.pop(i)
        # re-apply speeds for kind consistency after coalesce
        segs = coalesce_adjacent(segs)
        for s in segs:
            s["speed"] = float(speeds[s["kind"]])
    return coalesce_adjacent(segs)


def _segment_mean_edge(rows: list[dict], t0: float, t1: float) -> float:
    """Average edge density for frames overlapping [t0, t1)."""
    return _segment_mean(rows, t0, t1, "edge")


def apply_room_hold_ramp(
    segs: list[dict],
    cfg: dict[str, Any],
    rows: list[dict] | None = None,
) -> list[dict]:
    """
    Scheme A: within each continuous room segment, ramp up speed by dwell time
    so long static holds on the same space feel less draggy. No frames dropped.

    Scenic / high-structure and actively moving pans skip the ramp so indoor
    look-arounds are not gradually sped up into a whip-pan feel.
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
            cfg.get("classify", {}).get(
                "content_struct_min",
                cfg.get("classify", {}).get("scenic_edge_min", 0.18),
            ),
        )
    )
    # Active camera motion: not a static hold → keep 1x
    static_hold_motion_max = float(pacing.get("static_hold_motion_max", 7.0))
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
            if rows is not None:
                avg_e = _segment_mean_edge(rows, a, b)
                avg_m = _segment_mean(rows, a, b, "motion")
                # Dense structure stays 1x
                if scenic_skip and avg_e >= scenic_skip_edge:
                    speed = room_speed
                # Active pan / walk mislabeled as room: do not ramp
                elif avg_m > static_hold_motion_max:
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
    """Full classify → merge → dual-gate post → override → room-hold ramp."""
    labels = classify_frames(rows, cfg)
    segs = labels_to_segments(rows, labels, duration, cfg)
    segs_cfg = cfg.get("segments") or {}
    segs = absorb_short_auto(segs, float(segs_cfg.get("min_duration", 0.4)))
    speeds = cfg["speeds"]
    for s in segs:
        s["speed"] = float(speeds[s["kind"]])

    c = cfg["classify"]
    content_struct_min = float(
        c.get("content_struct_min", c.get("scenic_edge_min", 0.115))
    )
    wall_edge_max = float(c["wall_edge_max"])

    # Drop false fast on content-rich pans
    segs = demote_high_struct_fast(segs, rows, content_struct_min, speeds)
    segs = demote_sandwich_fast(
        segs,
        rows,
        content_struct_min=content_struct_min,
        max_fast_dur=float(segs_cfg.get("sandwich_demote_fast_max", 2.5)),
        speeds=speeds,
        require_high_struct_neighbors=bool(
            segs_cfg.get("sandwich_demote_need_high_struct", True)
        ),
    )
    # Low-edge flats left as room (high-std colored walls) → fast
    segs = promote_flat_rooms(segs, rows, cfg)
    # Lift mid-structure sustained rooms into corridor speeds
    segs = promote_corridor_rooms(segs, rows, cfg)
    # After promote: long high-structure "fast" → move (corridor walking ≠ blank wall)
    # Must run *after* promote_corridor so it cannot re-upgrade walking to 3x.
    segs = demote_long_structured_fast(segs, rows, cfg)
    # Remove short non-wall fast flickers
    segs = absorb_short_fast(
        segs,
        rows,
        min_fast_dur=float(segs_cfg.get("min_fast_duration", 0.9)),
        wall_edge_max=wall_edge_max,
        speeds=speeds,
    )
    segs = coalesce_adjacent(segs)

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
