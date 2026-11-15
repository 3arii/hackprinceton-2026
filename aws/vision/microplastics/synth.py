"""Synthetic backlit intake frames with ground truth, so the pipeline can be tested and tuned before
the rig exists.

Each frame has a vignetted LED field, dust on the window (identical in every frame, so the
background model has to remove it), exposure flicker, optical blur, sensor noise, and a Poisson
number of fibers, fragments, films, beads and air bubbles.
"""

import json
import math
from pathlib import Path

import cv2
import numpy as np

MIX = {"fiber": 0.35, "fragment": 0.3, "film": 0.1, "bead": 0.1, "bubble": 0.15}


def _field(h, w, rng):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w * rng.uniform(0.4, 0.6), h * rng.uniform(0.4, 0.6)
    r = np.hypot((xx - cx) / w, (yy - cy) / h)
    f = 0.9 - 0.6 * r**2 + 0.04 * (xx / w - 0.5)
    dust = np.zeros((h, w), np.float32)
    for _ in range(25):
        c = (int(rng.uniform(0, w)), int(rng.uniform(0, h)))
        cv2.circle(dust, c, int(rng.integers(1, 6)), float(rng.uniform(0.2, 0.6)), -1)
    return f * (1 - cv2.GaussianBlur(dust, (0, 0), 1.2))


def _polygon(rng, r, k, jitter, stretch):
    ang = np.sort(rng.uniform(0, 2 * math.pi, k))
    rad = r * rng.uniform(1 - jitter, 1, k)
    pts = np.c_[rad * np.cos(ang) * stretch, rad * np.sin(ang)]
    t = rng.uniform(0, 2 * math.pi)
    return pts @ np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])


def _fiber_pts(rng):
    n, step, curl = int(rng.uniform(10, 100)), 4.0, rng.uniform(0.02, 0.15)
    th = np.cumsum(rng.normal(0, curl, n)) + rng.uniform(0, 2 * math.pi)
    pts = np.vstack([[0, 0], np.cumsum(np.c_[np.cos(th), np.sin(th)] * step, axis=0)])
    return pts - pts.mean(0), n * step


def _particle(kind, h, w, rng):
    """(mask, opacity, bright core mask or None, true size in px) placed at random, or None if it won't fit."""
    if kind == "fiber":
        pts, length = _fiber_pts(rng)
    elif kind == "fragment":
        pts = _polygon(rng, rng.uniform(6, 40), int(rng.integers(5, 10)), 0.45, rng.uniform(1, 1.8))
    elif kind == "film":
        pts = _polygon(rng, rng.uniform(25, 80), int(rng.integers(8, 15)), 0.25, rng.uniform(1, 1.5))
    else:
        r = rng.uniform(3, 22) if kind == "bead" else rng.uniform(6, 28)
        pts = np.array([[-r, -r], [r, r]])
    lo, hi = -pts.min(0) + 8, np.array([w, h]) - pts.max(0) - 8
    if (hi <= lo).any():
        return None
    c = rng.uniform(lo, hi)
    mask, core = np.zeros((h, w), np.uint8), None
    if kind == "fiber":
        cv2.polylines(mask, [(pts + c).round().astype(np.int32)], False, 1, int(rng.integers(1, 5)))
        return mask, rng.uniform(0.45, 0.85), None, length
    if kind in ("fragment", "film"):
        cv2.fillPoly(mask, [(pts + c).round().astype(np.int32)], 1)
        opacity = rng.uniform(0.6, 0.95) if kind == "fragment" else rng.uniform(0.18, 0.32)
    elif kind == "bead":
        cv2.circle(mask, tuple(c.round().astype(int)), round(r), 1, -1)
        opacity = rng.uniform(0.75, 0.95)
    else:  # bubble: thick dark rim, bright lensing core
        ri = round(r)
        cv2.circle(mask, tuple(c.round().astype(int)), ri - max(1, round(0.15 * r)), 1, max(2, round(0.3 * r)))
        core = np.zeros((h, w), np.uint8)
        cv2.circle(core, tuple(c.round().astype(int)), max(1, round(0.35 * r)), 1, -1)
        opacity = rng.uniform(0.55, 0.8)
    ys, xs = np.nonzero(mask)
    size = max(cv2.minAreaRect(np.c_[xs, ys].astype(np.float32))[1]) + 1
    return mask, opacity, core, size


def make(out, frames=40, seed=0, size=(1024, 1280), per_frame=6, um_per_px=5.0):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    h, w = size
    field = _field(h, w, rng)
    truth = {}
    for i in range(frames):
        dark, gain = np.zeros((h, w), np.float32), np.ones((h, w), np.float32)
        taken = np.zeros((h, w), np.uint8)
        items = []
        for _ in range(rng.poisson(per_frame)):
            kind = str(rng.choice(list(MIX), p=list(MIX.values())))
            for _ in range(20):
                got = _particle(kind, h, w, rng)
                if got and not (got[0] & cv2.dilate(taken, np.ones((9, 9), np.uint8))).any():
                    break
            else:
                continue
            mask, opacity, core, true_size = got
            taken |= mask | (core if core is not None else 0)
            dark = np.maximum(dark, mask * opacity)
            if core is not None:
                gain += core * 0.08
            ys, xs = np.nonzero(mask if core is None else mask | core)
            items.append({
                "shape": kind,
                "x": round(float(xs.mean()), 1),
                "y": round(float(ys.mean()), 1),
                "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                "size_px": round(float(true_size), 1),
            })
        img = field * rng.uniform(0.97, 1.03) * (1 - dark) * gain
        img = cv2.GaussianBlur(img, (0, 0), 0.8)
        img += rng.normal(0, 0.008, img.shape) + rng.normal(0, 1, img.shape) * np.sqrt(np.clip(img, 0, 1) / 4000)
        name = f"frame_{i:03d}.png"
        cv2.imwrite(str(out / name), (np.clip(img, 0, 1) * 255).round().astype(np.uint8))
        truth[name] = items
    (out / "truth.json").write_text(json.dumps(truth))
    (out / "rig.json").write_text(json.dumps({"um_per_px": um_per_px, "depth_um": 1000}))
    return out
