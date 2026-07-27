"""Detect scene digressions A → B → A (content flash, not speed ABA).

A *scene ABA* is a short look-away: endpoints look like the same place A,
while the middle looks like a different place B. Playback speed may be
constant across the whole event.

Wall color is irrelevant — only appearance similarity and duration matter.
"""
from __future__ import annotations

from typing import Any

import numpy as np

# Optional OpenCV — only needed when fingerprinting real frames.
try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore


DEFAULT_SCENE_ABA: dict[str, Any] = {
    "enabled": True,
    # Middle digression length (source seconds)
    "min_b_sec": 0.30,
    "max_b_sec": 2.00,
    # Anchor pads before/after B used as scene A samples (source seconds)
    "anchor_sec": 0.25,
    # Sliding step when searching B windows
    "step_sec": 0.10,
    # Similarity thresholds in [0, 1]
    "end_sim_min": 0.82,  # FA ~ FC (same place)
    "mid_sim_max": 0.62,  # FA/FC vs FB (different place)
    # Optional motion gate (0 = disabled)
    "min_mid_motion": 0.0,
    # Score gate after combining terms
    "score_min": 0.35,
    # Merge nearby hits (source seconds)
    "merge_gap_sec": 0.25,
}


def scene_aba_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Merge cfg['scene_aba'] onto defaults."""
    out = dict(DEFAULT_SCENE_ABA)
    if not cfg:
        return out
    raw = cfg.get("scene_aba") or {}
    if isinstance(raw, dict):
        out.update(raw)
    return out


def fingerprint_gray(gray_small: np.ndarray) -> np.ndarray:
    """
    Color-agnostic appearance vector from a small grayscale frame.

    Layout: 16-bin intensity histogram (L1-normalized) + 2x2 block means.
    Works for white/gray/beige/dark painted walls alike.
    """
    if gray_small.ndim != 2:
        raise ValueError("fingerprint_gray expects a 2D grayscale array")
    img = gray_small.astype(np.float32)
    # Histogram via numpy so tests do not require OpenCV
    hist, _ = np.histogram(img, bins=16, range=(0.0, 256.0))
    hist = hist.astype(np.float64)
    hist_sum = float(hist.sum()) + 1e-9
    hist = hist / hist_sum
    h, w = img.shape
    blocks: list[float] = []
    for bi in range(2):
        for bj in range(2):
            y0, y1 = bi * h // 2, (bi + 1) * h // 2
            x0, x1 = bj * w // 2, (bj + 1) * w // 2
            blocks.append(float(img[y0:y1, x0:x1].mean()) / 255.0)
    return np.concatenate([hist, np.array(blocks, dtype=np.float64)])


def fingerprint_bgr(frame_bgr: np.ndarray, width: int = 135, height: int = 240) -> np.ndarray:
    """Resize BGR frame and build grayscale fingerprint."""
    if cv2 is None:
        raise RuntimeError("OpenCV is required for fingerprint_bgr")
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (int(width), int(height)))
    return fingerprint_gray(small)


def appearance_similarity(a: np.ndarray | list[float], b: np.ndarray | list[float]) -> float:
    """
    Similarity in [0, 1] between two fingerprints.

    Uses histogram intersection on the hist part and closeness on block means.
    """
    va = np.asarray(a, dtype=np.float64).ravel()
    vb = np.asarray(b, dtype=np.float64).ravel()
    if va.shape != vb.shape or va.size < 5:
        return 0.0
    # First 16 bins = hist, rest = layout
    n_hist = min(16, va.size - 1)
    ha, hb = va[:n_hist], vb[:n_hist]
    inter = float(np.minimum(ha, hb).sum())
    # Normalize intersection by average L1 mass (~1 if both normalized)
    hist_sim = inter / (0.5 * (float(ha.sum()) + float(hb.sum())) + 1e-9)
    hist_sim = float(np.clip(hist_sim, 0.0, 1.0))
    la, lb = va[n_hist:], vb[n_hist:]
    if la.size == 0:
        return hist_sim
    layout_dist = float(np.mean(np.abs(la - lb)))
    layout_sim = float(np.clip(1.0 - layout_dist * 2.5, 0.0, 1.0))
    return 0.75 * hist_sim + 0.25 * layout_sim


def _ensure_fingerprints(rows: list[dict]) -> list[np.ndarray] | None:
    """Return list of fingerprint vectors, or None if rows lack appearance."""
    if not rows:
        return None
    fps: list[np.ndarray] = []
    for r in rows:
        fp = r.get("appearance")
        if fp is None:
            fp = r.get("fp")
        if fp is None:
            return None
        fps.append(np.asarray(fp, dtype=np.float64).ravel())
    return fps


def _mean_fp(fps: list[np.ndarray], i0: int, i1: int) -> np.ndarray:
    i0 = max(0, i0)
    i1 = min(len(fps), max(i0 + 1, i1))
    stack = np.stack(fps[i0:i1], axis=0)
    return stack.mean(axis=0)


def _index_at_time(times: np.ndarray, t: float) -> int:
    """Nearest frame index for time t."""
    if len(times) == 0:
        return 0
    return int(np.clip(np.searchsorted(times, t, side="left"), 0, len(times) - 1))


def detect_scene_aba(
    rows: list[dict],
    cfg: dict[str, Any] | None = None,
) -> list[dict]:
    """
    Detect short scene digressions A→B→A on a per-frame analysis timeline.

    Each row should provide:
      - t: timestamp seconds
      - appearance or fp: fingerprint vector (see fingerprint_gray)
      - motion (optional): used if min_mid_motion > 0

    Returns a list of hits:
      {t0, t1, score, end_sim, mid_sim, mean_motion}
    sorted by t0. Overlapping hits are merged.
    """
    aba = scene_aba_config(cfg)
    if not bool(aba.get("enabled", True)):
        return []
    fps = _ensure_fingerprints(rows)
    if fps is None or len(rows) < 5:
        return []

    times = np.array([float(r["t"]) for r in rows], dtype=np.float64)
    motions = np.array(
        [float(r.get("motion", 0.0)) for r in rows], dtype=np.float64
    )
    duration = float(times[-1]) if len(times) else 0.0
    if duration <= 0:
        return []

    min_b = float(aba["min_b_sec"])
    max_b = float(aba["max_b_sec"])
    anchor = float(aba["anchor_sec"])
    step = max(0.05, float(aba["step_sec"]))
    end_sim_min = float(aba["end_sim_min"])
    mid_sim_max = float(aba["mid_sim_max"])
    min_mid_motion = float(aba["min_mid_motion"])
    score_min = float(aba["score_min"])

    raw_hits: list[dict] = []
    # B window start times
    t_start = anchor
    t_end_limit = duration - anchor
    if t_end_limit - t_start < min_b:
        return []

    b_len = min_b
    while b_len <= max_b + 1e-9:
        t0 = t_start
        while t0 + b_len <= t_end_limit + 1e-9:
            t1 = t0 + b_len
            # Anchors A before / after B
            a0, a1 = t0 - anchor, t0
            c0, c1 = t1, t1 + anchor
            ia0, ia1 = _index_at_time(times, a0), _index_at_time(times, a1)
            ib0, ib1 = _index_at_time(times, t0), _index_at_time(times, t1)
            ic0, ic1 = _index_at_time(times, c0), _index_at_time(times, c1)
            # ensure non-empty ranges
            if ia1 <= ia0:
                ia1 = ia0 + 1
            if ib1 <= ib0:
                ib1 = ib0 + 1
            if ic1 <= ic0:
                ic1 = ic0 + 1

            fa = _mean_fp(fps, ia0, ia1)
            fb = _mean_fp(fps, ib0, ib1)
            fc = _mean_fp(fps, ic0, ic1)

            end_sim = appearance_similarity(fa, fc)
            mid_sim = max(
                appearance_similarity(fa, fb),
                appearance_similarity(fc, fb),
            )
            mean_m = float(motions[ib0:ib1].mean()) if ib1 > ib0 else 0.0

            if end_sim >= end_sim_min and mid_sim <= mid_sim_max:
                if min_mid_motion <= 0.0 or mean_m >= min_mid_motion:
                    # Higher when ends match and middle diverges; prefer shorter B
                    short_factor = 1.0 - 0.35 * ((b_len - min_b) / max(max_b - min_b, 1e-6))
                    score = (
                        end_sim
                        * (1.0 - mid_sim)
                        * float(np.clip(short_factor, 0.55, 1.0))
                    )
                    if score >= score_min:
                        raw_hits.append(
                            {
                                "t0": float(t0),
                                "t1": float(t1),
                                "score": float(score),
                                "end_sim": float(end_sim),
                                "mid_sim": float(mid_sim),
                                "mean_motion": mean_m,
                            }
                        )
            t0 += step
        b_len += step

    return _merge_hits(raw_hits, gap=float(aba["merge_gap_sec"]))


def _merge_hits(hits: list[dict], gap: float = 0.25) -> list[dict]:
    """Merge overlapping / nearby hits; keep best score in a cluster."""
    if not hits:
        return []
    hits = sorted(hits, key=lambda h: (h["t0"], -h["score"]))
    merged: list[dict] = []
    cur = dict(hits[0])
    for h in hits[1:]:
        if h["t0"] <= cur["t1"] + gap:
            # expand and keep better score metrics
            if h["score"] > cur["score"]:
                cur["score"] = h["score"]
                cur["end_sim"] = h["end_sim"]
                cur["mid_sim"] = h["mid_sim"]
                cur["mean_motion"] = h["mean_motion"]
            cur["t1"] = max(cur["t1"], h["t1"])
            cur["t0"] = min(cur["t0"], h["t0"])
        else:
            merged.append(cur)
            cur = dict(h)
    merged.append(cur)
    return merged


def extract_fingerprints_from_video(
    video_path: str,
    *,
    resize_width: int = 135,
    resize_height: int = 240,
    sample_fps: float | None = None,
) -> tuple[list[dict], float]:
    """
    Read a video and return rows with t, motion, appearance fingerprints.

    Used by tests / offline probes without going through the full pipeline cache.
    """
    if cv2 is None:
        raise RuntimeError("OpenCV is required to read video fingerprints")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        stride = 1
        if sample_fps is not None and sample_fps > 0 and fps > sample_fps:
            stride = max(1, int(round(fps / sample_fps)))
        rows: list[dict] = []
        prev = None
        idx = 0
        kept = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if idx % stride == 0:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                small = cv2.resize(gray, (int(resize_width), int(resize_height)))
                if prev is None:
                    motion = 0.0
                else:
                    motion = float(cv2.absdiff(small, prev).mean())
                rows.append(
                    {
                        "idx": kept,
                        "t": idx / fps,
                        "motion": motion,
                        "appearance": fingerprint_gray(small),
                    }
                )
                prev = small
                kept += 1
            idx += 1
    finally:
        cap.release()
    if not rows:
        raise RuntimeError(f"No frames read from {video_path}")
    return rows, fps
