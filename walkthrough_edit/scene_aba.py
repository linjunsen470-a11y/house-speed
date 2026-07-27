"""Detect scene digressions A → B → A (content flash, not speed ABA).

A *scene ABA* is a short look-away: endpoints look like the same place A,
while the middle looks like a different place B — or the camera path
goes out and returns (optical-flow path return). Playback speed may be
constant across the whole event.

Wall color is irrelevant.
"""
from __future__ import annotations

from typing import Any

import numpy as np

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
    # --- Path A: appearance contrast (classic different place) ---
    "end_sim_min": 0.82,
    "mid_sim_max": 0.62,
    "min_mid_motion": 0.0,
    "score_min": 0.35,
    # --- Path B: camera path out-and-back (same room whip) ---
    "use_flow_return": True,
    "path_return_min": 0.42,
    "flow_end_sim_min": 0.55,  # ends still somewhat similar
    "flow_min_mid_motion": 7.0,
    "flow_score_min": 0.28,
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
    """
    if gray_small.ndim != 2:
        raise ValueError("fingerprint_gray expects a 2D grayscale array")
    img = gray_small.astype(np.float32)
    hist, _ = np.histogram(img, bins=16, range=(0.0, 256.0))
    hist = hist.astype(np.float64)
    hist = hist / (float(hist.sum()) + 1e-9)
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
    """Similarity in [0, 1] between two fingerprints."""
    va = np.asarray(a, dtype=np.float64).ravel()
    vb = np.asarray(b, dtype=np.float64).ravel()
    if va.shape != vb.shape or va.size < 5:
        return 0.0
    n_hist = min(16, va.size - 1)
    ha, hb = va[:n_hist], vb[:n_hist]
    inter = float(np.minimum(ha, hb).sum())
    hist_sim = inter / (0.5 * (float(ha.sum()) + float(hb.sum())) + 1e-9)
    hist_sim = float(np.clip(hist_sim, 0.0, 1.0))
    la, lb = va[n_hist:], vb[n_hist:]
    if la.size == 0:
        return hist_sim
    layout_dist = float(np.mean(np.abs(la - lb)))
    layout_sim = float(np.clip(1.0 - layout_dist * 2.5, 0.0, 1.0))
    return 0.75 * hist_sim + 0.25 * layout_sim


def path_return_score(
    flow_dx: np.ndarray | list[float],
    flow_dy: np.ndarray | list[float],
) -> float:
    """
    Score in [0, 1] for out-and-back camera paths.

    High when integrated path length is large but net displacement is small
    (went somewhere and came back), and/or first-half vs second-half flow
    vectors point roughly opposite.
    """
    dx = np.asarray(flow_dx, dtype=np.float64).ravel()
    dy = np.asarray(flow_dy, dtype=np.float64).ravel()
    n = min(dx.size, dy.size)
    if n < 3:
        return 0.0
    dx, dy = dx[:n], dy[:n]
    vecs = np.stack([dx, dy], axis=1)
    mags = np.linalg.norm(vecs, axis=1)
    path_len = float(mags.sum())
    if path_len < 1e-6:
        return 0.0
    net = float(np.linalg.norm(vecs.sum(axis=0)))
    loop = float(np.clip(1.0 - net / path_len, 0.0, 1.0))

    mid = n // 2
    v1 = vecs[:mid].sum(axis=0)
    v2 = vecs[mid:].sum(axis=0)
    n1 = float(np.linalg.norm(v1))
    n2 = float(np.linalg.norm(v2))
    if n1 < 1e-6 or n2 < 1e-6:
        opp = 0.0
    else:
        cos = float(np.dot(v1, v2) / (n1 * n2))
        # cos=-1 → opposite → 1.0; cos=1 → same → 0.0
        opp = float(np.clip((-cos + 1.0) / 2.0, 0.0, 1.0))

    # Require some absolute motion so noise does not look like a loop
    activity = float(np.clip(path_len / max(n, 1) / 2.0, 0.0, 1.0))
    return float(np.clip(0.50 * loop + 0.35 * opp + 0.15 * activity, 0.0, 1.0))


def _ensure_fingerprints(rows: list[dict]) -> list[np.ndarray] | None:
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


def _flow_arrays(rows: list[dict]) -> tuple[np.ndarray, np.ndarray] | None:
    """Per-row flow_dx/flow_dy if present on all rows."""
    if not rows or "flow_dx" not in rows[0]:
        return None
    try:
        dx = np.array([float(r.get("flow_dx", 0.0)) for r in rows], dtype=np.float64)
        dy = np.array([float(r.get("flow_dy", 0.0)) for r in rows], dtype=np.float64)
    except (TypeError, ValueError):
        return None
    return dx, dy


def _mean_fp(fps: list[np.ndarray], i0: int, i1: int) -> np.ndarray:
    i0 = max(0, i0)
    i1 = min(len(fps), max(i0 + 1, i1))
    return np.stack(fps[i0:i1], axis=0).mean(axis=0)


def _index_at_time(times: np.ndarray, t: float) -> int:
    if len(times) == 0:
        return 0
    return int(np.clip(np.searchsorted(times, t, side="left"), 0, len(times) - 1))


def detect_scene_aba(
    rows: list[dict],
    cfg: dict[str, Any] | None = None,
) -> list[dict]:
    """
    Detect short scene digressions A→B→A.

    Rows should provide:
      - t
      - appearance / fp
      - motion (optional)
      - flow_dx, flow_dy (optional; enables path-return path)

    Each hit:
      {t0, t1, score, end_sim, mid_sim, mean_motion, path_return, mode}
      mode: "appearance" | "flow_return" | "both"
    """
    aba = scene_aba_config(cfg)
    if not bool(aba.get("enabled", True)):
        return []
    fps = _ensure_fingerprints(rows)
    if fps is None or len(rows) < 5:
        return []

    times = np.array([float(r["t"]) for r in rows], dtype=np.float64)
    motions = np.array([float(r.get("motion", 0.0)) for r in rows], dtype=np.float64)
    flows = _flow_arrays(rows)
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

    use_flow = bool(aba.get("use_flow_return", True)) and flows is not None
    path_return_min = float(aba.get("path_return_min", 0.42))
    flow_end_sim_min = float(aba.get("flow_end_sim_min", 0.55))
    flow_min_mid_motion = float(aba.get("flow_min_mid_motion", 7.0))
    flow_score_min = float(aba.get("flow_score_min", 0.28))

    raw_hits: list[dict] = []
    t_start = anchor
    t_end_limit = duration - anchor
    if t_end_limit - t_start < min_b:
        return []

    flow_dx = flows[0] if flows is not None else None
    flow_dy = flows[1] if flows is not None else None

    b_len = min_b
    while b_len <= max_b + 1e-9:
        t0 = t_start
        while t0 + b_len <= t_end_limit + 1e-9:
            t1 = t0 + b_len
            a0, a1 = t0 - anchor, t0
            c0, c1 = t1, t1 + anchor
            ia0, ia1 = _index_at_time(times, a0), _index_at_time(times, a1)
            ib0, ib1 = _index_at_time(times, t0), _index_at_time(times, t1)
            ic0, ic1 = _index_at_time(times, c0), _index_at_time(times, c1)
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

            pr = 0.0
            if flow_dx is not None and flow_dy is not None:
                # flow at index i describes motion into frame i; use (ib0+1)..ib1
                f0 = max(ib0, 1)
                f1 = max(f0 + 1, ib1)
                pr = path_return_score(flow_dx[f0:f1], flow_dy[f0:f1])

            short_factor = 1.0 - 0.35 * (
                (b_len - min_b) / max(max_b - min_b, 1e-6)
            )
            short_factor = float(np.clip(short_factor, 0.55, 1.0))

            hit_app = False
            hit_flow = False
            score_app = 0.0
            score_flow = 0.0

            # Path A: appearance digression
            if end_sim >= end_sim_min and mid_sim <= mid_sim_max:
                if min_mid_motion <= 0.0 or mean_m >= min_mid_motion:
                    score_app = end_sim * (1.0 - mid_sim) * short_factor
                    hit_app = score_app >= score_min

            # Path B: out-and-back camera path (same-room whip)
            if use_flow and pr >= path_return_min:
                if mean_m >= flow_min_mid_motion and end_sim >= flow_end_sim_min:
                    # Prefer some mid divergence when available, but do not require it
                    mid_term = 0.35 + 0.65 * float(np.clip(1.0 - mid_sim, 0.0, 1.0))
                    score_flow = pr * mid_term * short_factor
                    # Boost when ends are strongly similar
                    score_flow *= 0.7 + 0.3 * float(np.clip(end_sim, 0.0, 1.0))
                    hit_flow = score_flow >= flow_score_min

            if hit_app or hit_flow:
                if hit_app and hit_flow:
                    mode = "both"
                    score = max(score_app, score_flow) + 0.05 * min(score_app, score_flow)
                elif hit_app:
                    mode = "appearance"
                    score = score_app
                else:
                    mode = "flow_return"
                    score = score_flow
                raw_hits.append(
                    {
                        "t0": float(t0),
                        "t1": float(t1),
                        "score": float(score),
                        "end_sim": float(end_sim),
                        "mid_sim": float(mid_sim),
                        "mean_motion": mean_m,
                        "path_return": float(pr),
                        "mode": mode,
                    }
                )
            t0 += step
        b_len += step

    return _merge_hits(raw_hits, gap=float(aba["merge_gap_sec"]))


def _merge_hits(hits: list[dict], gap: float = 0.25) -> list[dict]:
    if not hits:
        return []
    hits = sorted(hits, key=lambda h: (h["t0"], -h["score"]))
    merged: list[dict] = []
    cur = dict(hits[0])
    for h in hits[1:]:
        if h["t0"] <= cur["t1"] + gap:
            if h["score"] > cur["score"]:
                for key in (
                    "score",
                    "end_sim",
                    "mid_sim",
                    "mean_motion",
                    "path_return",
                    "mode",
                ):
                    if key in h:
                        cur[key] = h[key]
            cur["t1"] = max(cur["t1"], h["t1"])
            cur["t0"] = min(cur["t0"], h["t0"])
        else:
            merged.append(cur)
            cur = dict(h)
    merged.append(cur)
    return merged


def frame_flow_phase(
    prev_gray: np.ndarray, cur_gray: np.ndarray
) -> tuple[float, float]:
    """
    Global translation (dx, dy) between two grayscale frames via phase correlation.
    Returns (0, 0) if OpenCV missing or correlation fails.
    """
    if cv2 is None:
        return 0.0, 0.0
    dx, dy, _response = frame_flow_phase_response(prev_gray, cur_gray)
    return dx, dy


def frame_flow_phase_response(
    prev_gray: np.ndarray, cur_gray: np.ndarray
) -> tuple[float, float, float]:
    """Global translation plus phase-correlation confidence in ``[0, 1]``."""
    if cv2 is None:
        return 0.0, 0.0, 0.0
    a = prev_gray.astype(np.float32)
    b = cur_gray.astype(np.float32)
    try:
        (dx, dy), response = cv2.phaseCorrelate(a, b)
        return (
            float(dx),
            float(dy),
            float(np.clip(response, 0.0, 1.0)),
        )
    except cv2.error:
        return 0.0, 0.0, 0.0


def extract_fingerprints_from_video(
    video_path: str,
    *,
    resize_width: int = 135,
    resize_height: int = 240,
    sample_fps: float | None = None,
) -> tuple[list[dict], float]:
    """
    Read a video and return rows with appearance + per-frame flow.

    Each row: idx, t, motion, appearance, flow_dx, flow_dy
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
                    fdx, fdy = 0.0, 0.0
                else:
                    motion = float(cv2.absdiff(small, prev).mean())
                    fdx, fdy = frame_flow_phase(prev, small)
                rows.append(
                    {
                        "idx": kept,
                        "t": idx / fps,
                        "motion": motion,
                        "appearance": fingerprint_gray(small),
                        "flow_dx": fdx,
                        "flow_dy": fdy,
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
