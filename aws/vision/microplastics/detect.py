"""Find, measure and count particles in backlit (bright-field) frames of the buoy's water intake.

Under a backlight a particle is a dark silhouette on a bright field. Everything here is classical
image processing, so it works on the first frames off the rig, before anyone has labelled data:

  1. flat-field  divide by a background (a blank shot of clean water, or the median of the burst),
                 which removes vignetting, LED hot spots and dirt stuck on the window
  2. segment     threshold the darkening with hysteresis, fill holes, one contour per particle
  3. measure     size in µm from the rig calibration, plus shape and opacity features
  4. classify    shape rules (fiber, fragment, film, bead, bubble), or a model trained on
                 labelled crops (train.py)
  5. count       particles per litre of water actually imaged, with a Poisson 95% interval
"""

import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, fields
from pathlib import Path

import cv2
import numpy as np

IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
# Classes that are not microplastics and never count. A trained model may use the others too.
IGNORE = {"bubble", "organic", "sediment", "noise"}
FIBER_ASPECT = 3.0  # length:width above 3 is a fiber, the usual convention in microplastics surveys
KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


@dataclass
class Rig:
    """Camera and flow-cell geometry, loaded from rig.json.

    Calibrate um_per_px by photographing a ruler or stage micrometer in the cell. Concentration is
    particles per litre of water actually imaged, so every frame must see fresh water (pump, stop,
    settle, shoot; or a frame interval longer than the time water takes to cross the view),
    otherwise one particle is counted in several frames.
    """

    um_per_px: float = 5.0
    depth_um: float = 1000.0  # gap of the flow cell along the optical axis
    liters_per_frame: float | None = None  # overrides the geometry, e.g. for a filtered sample
    threshold: float = 0.08  # darker than the background by this fraction = part of a particle
    min_px: int = 12  # smallest blob that counts; anything smaller is sensor noise
    max_um: float = 5000.0  # microplastics are under 5 mm by definition

    @classmethod
    def load(cls, path=None):
        if not path or not Path(path).exists():
            return cls()
        data = json.loads(Path(path).read_text())
        return cls(**{k: v for k, v in data.items() if k in {f.name for f in fields(cls)}})

    def liters(self, shape):
        if self.liters_per_frame:
            return self.liters_per_frame
        return shape[0] * shape[1] * self.um_per_px**2 * self.depth_um * 1e-15  # 1 µm³ = 1e-15 L


@dataclass
class Particle:
    frame: str
    shape: str
    x: float
    y: float
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive)
    size_um: float  # longest dimension; for fibers, the length along the curve
    diameter_um: float  # diameter of the circle with the same area
    width_um: float
    aspect: float  # length:width
    circularity: float
    solidity: float
    opacity: float  # mean darkening inside the particle, 0 to 1; films are faint
    hollow: float  # darkening at the core over the whole; bubbles are bright in the middle
    clipped: bool  # touches the frame edge, so its size is a lower bound
    counted: bool  # counts toward concentration (edge rule: left and top edges in, right and bottom out)


def to_float(a):
    return a.astype(np.float32) / (np.iinfo(a.dtype).max if a.dtype.kind in "ui" else 1.0)


def _read(path):
    a = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_ANYDEPTH)
    if a is None:
        raise ValueError(f"can't read {path}")
    return a


def load(src):
    """[(name, loader)] for a folder of images or a video file. Loaders return raw arrays."""
    p = Path(src)
    if p.is_dir():
        return [(f.name, lambda f=f: _read(f)) for f in sorted(p.iterdir()) if f.suffix.lower() in IMAGES]
    if p.suffix.lower() in IMAGES:
        return [(p.name, lambda: _read(p))]
    cap, out = cv2.VideoCapture(str(p)), []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        out.append((f"{p.stem}-{len(out):05d}", lambda g=g: g))
    return out


def background(frames, k=31):
    """Per-pixel median of up to k frames spread across the burst. Moving particles vanish from it;
    anything stuck on the window stays, so it is divided out and never counted."""
    if len(frames) < 5:
        print("warning: under 5 frames and no blank, so particles may leak into the background", file=sys.stderr)
    idx = np.linspace(0, len(frames) - 1, min(k, len(frames))).round().astype(int)
    stack = np.stack([frames[i][1]() for i in idx])
    return to_float(np.median(stack, axis=0).astype(stack.dtype))


def darkening(frame, bg):
    flat = frame / np.maximum(bg, 1e-3)
    flat /= np.median(flat)  # exposure and LED flicker between frames
    return np.clip(1.0 - flat, 0.0, 1.0)


def segment(dark, rig):
    """Hysteresis threshold: faint pixels join a particle only if it has some clearly dark pixels."""
    smooth = cv2.GaussianBlur(dark, (0, 0), 1.0)
    weak = cv2.morphologyEx((smooth > rig.threshold).astype(np.uint8), cv2.MORPH_CLOSE, KERNEL)
    n, labels = cv2.connectedComponents(weak, connectivity=8)
    keep = np.zeros(n, bool)
    keep[np.unique(labels[smooth > 2 * rig.threshold])] = True
    keep[0] = False
    contours, _ = cv2.findContours(keep[labels].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return contours


def measure(c, dark, rig, frame):
    H, W = dark.shape
    x, y, w, h = cv2.boundingRect(c)
    mask = np.zeros((h, w), np.uint8)
    cv2.drawContours(mask, [c - (x, y)], -1, 1, cv2.FILLED)  # filled, so a bubble's bright core is inside
    area = int(mask.sum())
    if area < rig.min_px:
        return None
    d = dark[y : y + h, x : x + w]
    dt = cv2.distanceTransform(mask, cv2.DIST_L2, 3)  # distance to the particle's edge
    opacity = float(d[mask > 0].mean())
    hollow = float(d[dt > 0.5 * dt.max()].mean() / max(opacity, 1e-6))

    # The contour runs through boundary pixel centres; +π puts the perimeter on the pixel edges
    per = cv2.arcLength(c, True) + math.pi
    circularity = min(1.0, 4 * math.pi * area / per**2)
    hull = cv2.contourArea(cv2.convexHull(c))
    solidity = min(1.0, cv2.contourArea(c) / hull) if hull > 0 else 0.0
    # Width is the widest inscribed circle, so area / width² is length:width even for a curled fiber
    width = 2 * float(dt.max())
    aspect = area / width**2
    feret = max(cv2.minAreaRect(c)[1]) + 1
    # Fiber length along the curve: a ribbon with this perimeter, 2(L+W), and area, LW
    ribbon = (per + math.sqrt(max(per**2 - 16 * area, 0))) / 4
    size = max(feret, ribbon) if aspect >= FIBER_ASPECT else feret

    m = cv2.moments(mask, binaryImage=True)
    um = rig.um_per_px
    right, bottom = x + w >= W, y + h >= H
    return Particle(
        frame=frame,
        shape="",
        x=round(x + m["m10"] / m["m00"], 1),
        y=round(y + m["m01"] / m["m00"], 1),
        bbox=(x, y, x + w, y + h),
        size_um=round(size * um, 1),
        diameter_um=round(2 * math.sqrt(area / math.pi) * um, 1),
        width_um=round(width * um, 1),
        aspect=round(aspect, 2),
        circularity=round(circularity, 3),
        solidity=round(solidity, 3),
        opacity=round(opacity, 3),
        hollow=round(hollow, 3),
        clipped=bool(x == 0 or y == 0 or right or bottom),
        counted=not (right or bottom) and size * um <= rig.max_um,
    )


def rule_shape(p):
    if p.circularity > 0.6 and p.hollow < 0.7:
        return "bubble"
    if p.aspect >= FIBER_ASPECT:
        return "fiber"
    if p.opacity < 0.35:
        return "film"
    if p.circularity > 0.85 and p.solidity > 0.9:
        return "bead"
    return "fragment"


def vector(p):
    """Features for a trained classifier. Changing this invalidates crops cut before the change."""
    return [
        math.log1p(p.size_um), math.log1p(p.diameter_um), math.log1p(p.width_um),
        p.aspect, p.circularity, p.solidity, p.opacity, p.hollow,
    ]


def detect(frame, bg, rig, name="frame", model=None):
    dark = darkening(frame, bg)
    ps = [p for c in segment(dark, rig) if (p := measure(c, dark, rig, name))]
    shapes = model.predict([vector(p) for p in ps]) if model is not None and ps else [rule_shape(p) for p in ps]
    for p, s in zip(ps, shapes):
        p.shape = str(s)
    return ps


def poisson95(n):
    """95% interval on a Poisson count (Wilson-Hilferty, within about 1% of exact)."""
    lo = 0.0 if n == 0 else n * (1 - 1 / (9 * n) - 1.96 / (3 * math.sqrt(n))) ** 3
    m = n + 1
    return lo, m * (1 - 1 / (9 * m) + 1.96 / (3 * math.sqrt(m))) ** 3


def summarize(particles, frames, liters, rig):
    kept = [p for p in particles if p.shape not in IGNORE]
    counted = [p for p in kept if p.counted]
    n = len(counted)
    lo, hi = poisson95(n)
    sizes = np.array([p.size_um for p in kept if not p.clipped])
    return {
        "frames": frames,
        "liters": float(f"{liters:.4g}"),
        "count": n,
        "microplastics": round(n / liters, 2),  # particles/L, the buoy metric
        "ci95": [round(lo / liters, 2), round(hi / liters, 2)],
        "by_shape": dict(Counter(p.shape for p in counted).most_common()),
        "ignored": dict(Counter(p.shape for p in particles if p.shape in IGNORE).most_common()),
        "size_um": {f"p{q}": round(float(np.percentile(sizes, q)), 1) for q in (10, 50, 90)} if sizes.size else None,
        "smallest_um": round(2 * math.sqrt(rig.min_px / math.pi) * rig.um_per_px, 1),
    }


def analyze(src, rig, blank=None, model=None, on_frame=None):
    frames = load(src)
    if not frames:
        raise SystemExit(f"no frames in {src}")
    bg = to_float(_read(blank)) if blank else background(frames)
    particles = []
    for name, get in frames:
        frame = to_float(get())
        ps = detect(frame, bg, rig, name, model)
        particles += ps
        if on_frame:
            on_frame(name, frame, ps)
    return particles, summarize(particles, len(frames), len(frames) * rig.liters(bg.shape), rig)


COLORS = {"fiber": (0, 165, 255), "fragment": (60, 60, 255), "film": (255, 200, 0), "bead": (80, 220, 80)}


def overlay(frame, particles):
    img = cv2.cvtColor((frame * 255).clip(0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    for p in particles:
        col = COLORS.get(p.shape, (150, 150, 150))
        x0, y0, x1, y1 = p.bbox
        cv2.rectangle(img, (x0 - 2, y0 - 2), (x1 + 1, y1 + 1), col, 1)
        cv2.putText(img, f"{p.shape} {p.size_um:.0f}um", (x0, max(10, y0 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    return img
