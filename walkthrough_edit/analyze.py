"""Per-frame motion / structure analysis."""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np


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


def analyze_motion(video_path: str, cfg: dict[str, Any]) -> tuple[list[dict], float]:
    """
    Return per-frame metrics and fps.

    Each row: idx, t, mean, std, edge, motion
    """
    a = cfg["analysis"]
    w, h = int(a["resize_width"]), int(a["resize_height"])
    canny_lo, canny_hi = int(a["canny_low"]), int(a["canny_high"])

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

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
        if prev is None:
            motion = 0.0
        else:
            motion = float(cv2.absdiff(small, prev).mean())
        rows.append(
            {
                "idx": idx,
                "t": idx / fps,
                "mean": mean,
                "std": std,
                "edge": edge_ratio,
                "motion": motion,
            }
        )
        prev = small
        idx += 1
    cap.release()

    if not rows:
        raise RuntimeError(f"No frames read from {video_path}")
    return rows, fps
