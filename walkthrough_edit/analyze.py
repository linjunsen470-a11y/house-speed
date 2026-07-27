"""Per-frame motion / structure analysis."""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .scene_aba import fingerprint_gray, frame_flow_phase_response


def smooth(arr: np.ndarray, k: int = 7) -> np.ndarray:
    """Odd-window moving average with edge padding."""
    k = max(1, int(k))
    if k % 2 == 0:
        k += 1
    if k == 1:
        return arr.astype(float)
    pad = k // 2
    x = np.pad(arr.astype(float), (pad, pad), mode="edge")
    kernel = np.ones(k) / k
    return np.convolve(x, kernel, mode="valid")


def frame_entropy(gray: np.ndarray) -> float:
    """Shannon entropy in bits for an 8-bit grayscale frame."""
    hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).ravel()
    total = float(hist.sum())
    if total <= 0:
        return 0.0
    probs = hist[hist > 0] / total
    return float(-(probs * np.log2(probs)).sum())


def _attach_temporal_scores(rows: list[dict], window: int) -> None:
    """Attach interpretable candidate scores without changing legacy metrics."""
    if not rows:
        return
    edge = np.array([float(r["edge"]) for r in rows], dtype=float)
    motion = np.array([float(r["motion"]) for r in rows], dtype=float)
    entropy = np.array([float(r["entropy"]) for r in rows], dtype=float)
    sharpness = np.array([float(r["sharpness"]) for r in rows], dtype=float)
    dx = np.array([float(r["flow_dx"]) for r in rows], dtype=float)
    dy = np.array([float(r["flow_dy"]) for r in rows], dtype=float)
    response = np.array([float(r["flow_response"]) for r in rows], dtype=float)

    magnitude = np.hypot(dx, dy)
    half = max(1, int(window) // 2)
    consistency = np.zeros(len(rows), dtype=float)
    for i in range(len(rows)):
        lo, hi = max(0, i - half), min(len(rows), i + half + 1)
        vec = np.stack([dx[lo:hi], dy[lo:hi]], axis=1)
        path = float(np.linalg.norm(vec, axis=1).sum())
        consistency[i] = (
            float(np.linalg.norm(vec.sum(axis=0))) / path if path > 1e-6 else 0.0
        )

    edge_score = np.clip((edge - 0.025) / 0.11, 0.0, 1.0)
    entropy_score = np.clip((entropy - 2.0) / 2.8, 0.0, 1.0)
    sharp_score = np.clip(np.log1p(sharpness) / np.log1p(900.0), 0.0, 1.0)
    information = 0.55 * edge_score + 0.25 * entropy_score + 0.20 * sharp_score

    motion_score = np.clip(motion / 22.0, 0.0, 1.0)
    flow_score = np.clip(magnitude / 8.0, 0.0, 1.0) * response
    blur_score = 1.0 - sharp_score
    camera_motion = np.clip(
        0.55 * motion_score + 0.25 * flow_score + 0.20 * blur_score,
        0.0,
        1.0,
    )
    for i, row in enumerate(rows):
        row["flow_magnitude"] = float(magnitude[i])
        row["flow_direction_consistency"] = float(consistency[i])
        row["information_score"] = float(information[i])
        row["camera_motion_score"] = float(camera_motion[i])


def analyze_motion(video_path: str, cfg: dict[str, Any]) -> tuple[list[dict], float]:
    """
    Return per-frame metrics and fps.

    Each row includes the legacy metrics plus appearance, flow, entropy,
    sharpness, and two normalized candidate scores.

    ``appearance`` / flow support scene A→B→A digression demotion (local CV only).
    """
    a = cfg["analysis"]
    w, h = int(a["resize_width"]), int(a["resize_height"])
    canny_lo, canny_hi = int(a["canny_low"]), int(a["canny_high"])

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        prev = None
        rows: list[dict] = []
        idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (w, h))
            mean = float(small.mean())
            std = float(small.std())
            edges = cv2.Canny(small, canny_lo, canny_hi)
            edge_ratio = float(edges.mean()) / 255.0
            entropy = frame_entropy(small)
            sharpness = float(cv2.Laplacian(small, cv2.CV_32F).var())
            if prev is None:
                motion = 0.0
                fdx, fdy, flow_response = 0.0, 0.0, 0.0
            else:
                motion = float(cv2.absdiff(small, prev).mean())
                fdx, fdy, flow_response = frame_flow_phase_response(prev, small)
            rows.append(
                {
                    "idx": idx,
                    "t": idx / fps,
                    "mean": mean,
                    "std": std,
                    "edge": edge_ratio,
                    "motion": motion,
                    "appearance": fingerprint_gray(small),
                    "flow_dx": fdx,
                    "flow_dy": fdy,
                    "flow_response": flow_response,
                    "entropy": entropy,
                    "sharpness": sharpness,
                }
            )
            prev = small
            idx += 1
    finally:
        cap.release()

    if not rows:
        raise RuntimeError(f"No frames read from {video_path}")
    _attach_temporal_scores(rows, int(a.get("smooth_window", 7)))
    return rows, fps
