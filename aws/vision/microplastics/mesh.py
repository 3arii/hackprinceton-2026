"""Is anything on the mesh? A model of the clean mesh, trained only on photos of it clean.

The buoy camera looks at the intake mesh with an LED behind it, so anything caught on the mesh
blocks or tints the light. There are no photos of microplastics yet, so instead of learning what
plastic looks like, fit() learns what clean looks like: it keeps the clean photos, and check()
explains a new photo patch by patch from them.

  1. align the photo to the clean ones (the rig shifts by a few pixels)
  2. match overall brightness and colour cast per channel (exposure and white balance drift)
  3. for every small patch, find the closest patch in the nearest clean photos, letting it move a
     little (parallax: the LED behind the mesh moves against the holes when the camera does)
  4. the patches that no clean photo explains, grouped, are the spots

and labels the photo

  clean          every patch looks like the clean mesh did
  microplastic   one or more spots: something is on the mesh (position and size of each)
  blocked        the camera can't see the mesh as it was trained: moved, covered, dark or relit

The threshold comes from leave-one-out over the training photos: each is scored against the others,
and the threshold sits a margin above the worst of them, region by region, so the edges of the
bright spots (which shimmer as the camera wobbles) tolerate more than the steady background. The
more clean photos, the more wobble and lighting drift it tolerates. Refit whenever the camera or
the mesh is moved for good.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

SCALE = 0.25  # the photos are heavily defocused, so a quarter resolution loses nothing
EPS = 4.0  # log(x + EPS): brightness changes become additive and dark pixels' noise stays bounded
SAT = 250  # a clipped pixel says nothing about how much light got through
PATCH = 3  # px at working scale (12 at full resolution)
REACH = 4  # px a patch may move to find its match, at working scale (16 at full resolution)
NEAREST = 8  # clean photos compared, the closest in camera position
FLOOR = 0.03  # noise per pixel, in log units (3% brightness); the score is RMS patch error over this
MARGIN = 1.3  # threshold over the worst clean photo in leave-one-out
SPREAD = 5  # px at working scale the worst clean score is pooled over: it moves with the camera
MIN_LIMIT = 3.0  # lowest threshold before the margin: a few clean photos understate the noise
MAX_SHIFT = 24  # px at full resolution from the nearest clean photo; further than that, parallax
#                 changes the view too much to compare and the camera has to be refitted
MIN_CC = 0.9  # alignment correlation under this: not the view the model was trained on
BLOCKED = 0.25  # over this fraction of the photo differs: not something on the mesh
CAST = 0.15  # colour cast against the clean photos, log units, past which the lighting has changed
EXPOSURE = 0.4  # brightness change against the clean photos, log units (x1.5)
MIN_SPOT = 3  # px at working scale; smaller clusters are noise
EDGE = 6  # px at working scale along the frame edge left out: the camera moving uncovers it
FAR = 1e6  # patch error where a clean photo has no pixels
IMAGES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
NAN3 = (np.nan,) * 3


def read(path):
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"can't read {path}")
    return img


def files(paths):
    """Image files from a mix of files and folders, in name order."""
    out = []
    for p in map(Path, paths):
        out += sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGES) if p.is_dir() else [p]
    return out


def _small(img):
    return cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)


def _log(small):
    return np.log(small.astype(np.float32) + EPS)


def _shift(ref, grey):
    """(translation in working px that maps the photo onto ref, alignment correlation), or None if
    the photo has nothing in common with ref."""
    # Rough shift first, by finding the middle of the photo in ref: ECC alone only finds small ones
    h, w = ref.shape
    my, mx = h // 4, w // 4
    hit = cv2.matchTemplate(ref, grey[my : h - my, mx : w - mx], cv2.TM_CCOEFF_NORMED)
    v, u = np.unravel_index(np.argmax(hit), hit.shape)
    warp = np.float32([[1, 0, mx - u], [0, 1, my - v]])
    try:
        _, warp = cv2.findTransformECC(
            ref, grey, warp, cv2.MOTION_TRANSLATION,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), None, 5,
        )
    except cv2.error:
        return None
    # ECC's own correlation drops at half-pixel shifts, so measure it on the aligned photo
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    moved = cv2.warpAffine(grey, warp, (w, h), flags=flags)
    inside = cv2.warpAffine(np.ones_like(grey), warp, (w, h), flags=flags) > 0.999
    if inside.mean() < 0.5:
        return None
    cc = np.corrcoef(ref[inside], moved[inside])[0, 1]
    if not np.isfinite(cc):  # a flat photo: dark or covered
        return None
    return np.array([warp[0, 2], warp[1, 2]], np.float32), float(cc)


def _move(a, d, border):
    """a moved by d (working px): out[y, x] = a[y + dy, x + dx]."""
    h, w = a.shape[:2]
    m = np.float32([[1, 0, d[0]], [0, 1, d[1]]])
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    return cv2.warpAffine(a, m, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def _error(L, sat, clean, clean_sat, d):
    """Mean squared error of every patch of the photo against one clean photo moved by d onto it,
    the best of small local moves, the photo's signed difference at that best match, and its
    brightness against the clean photo per channel."""
    T = _move(clean, d, NAN3)
    Ts = _move(clean_sat.astype(np.uint8), d, (0, 0, 0)) > 0
    ok = ~np.isnan(T).any(2)
    off = np.zeros(3, np.float32)  # brightness and colour cast, from pixels clipped in neither
    for c in range(3):
        m = ok & ~sat[..., c] & ~Ts[..., c]
        if m.sum() > 50:
            off[c] = np.median(L[..., c][m] - T[..., c][m])
    Tp = cv2.copyMakeBorder(T + off, REACH, REACH, REACH, REACH, cv2.BORDER_CONSTANT, value=NAN3)
    Sp = cv2.copyMakeBorder(Ts.astype(np.uint8), REACH, REACH, REACH, REACH, cv2.BORDER_CONSTANT, value=(0, 0, 0)) > 0
    h, w = L.shape[:2]
    best = np.full((h, w), FAR, np.float32)
    resid = np.zeros((h, w), np.float32)
    for dy in range(2 * REACH + 1):
        for dx in range(2 * REACH + 1):
            r = L - Tp[dy : dy + h, dx : dx + w]
            r[sat & Sp[dy : dy + h, dx : dx + w]] = 0  # clipped in both: no information
            e = (r**2).mean(2)
            gap = np.isnan(e)
            e[gap] = 0  # kept out of the filter: huge values would swamp its running sums
            e = cv2.boxFilter(e, -1, (PATCH, PATCH), borderType=cv2.BORDER_REPLICATE)
            e[cv2.boxFilter(gap.astype(np.float32), -1, (PATCH, PATCH), borderType=cv2.BORDER_REPLICATE) > 0] = FAR
            better = e < best
            best[better] = e[better]
            resid[better] = np.nan_to_num(r.mean(2))[better]
    return best, resid, off


@dataclass
class Model:
    clean: np.ndarray  # K x H x W x 3 uint8: the clean photos at working scale
    shifts: np.ndarray  # K x 2: where each sits against the reference photo, working px
    ref: int  # index of the reference photo, the most typical one
    limit: np.ndarray  # H x W threshold on the score, from leave-one-out
    info: dict

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:  # np.savez would append .npz to any other suffix
            np.savez_compressed(f, clean=self.clean, shifts=self.shifts, ref=self.ref, limit=self.limit,
                                info=json.dumps(self.info))

    @classmethod
    def load(cls, path):
        z = np.load(path)
        return cls(z["clean"], z["shifts"], int(z["ref"]), z["limit"], json.loads(str(z["info"])))

    def prepped(self):
        if not hasattr(self, "_prepped"):
            Ls = np.stack([_log(c) for c in self.clean])
            self._prepped = Ls, self.clean >= SAT, Ls[self.ref].mean(2)
        return self._prepped


def _score(model, small, skip=None):
    """(RMS patch error over FLOOR, signed difference, shift, why it's blocked or None), the maps at
    working scale. skip leaves one clean photo out."""
    Ls, sats, ref = model.prepped()
    L, sat = _log(small), small >= SAT
    got = _shift(ref, L.mean(2))
    if not got or got[1] < MIN_CC:
        return None, None, None, "can't see the mesh"
    shift = got[0]
    d = shift - model.shifts  # where this photo sits against each clean one
    dist = np.hypot(*d.T) / SCALE
    if skip is not None:
        dist[skip] = np.inf
    near = [k for k in np.argsort(dist)[:NEAREST] if dist[k] <= MAX_SHIFT]
    if not near:
        return None, None, shift, f"camera moved {dist.min():.0f} px from where it was trained"
    best = np.full(L.shape[:2], FAR, np.float32)
    resid = np.zeros(L.shape[:2], np.float32)
    for k in near:
        e, r, off = _error(L, sat, Ls[k], sats[k], -d[k])
        if k == near[0] and (np.ptp(off) > CAST or abs(off.mean()) > EXPOSURE):
            return None, None, shift, "lighting changed"  # the LED, exposure or white balance
        better = e < best
        best[better], resid[better] = e[better], r[better]
    score = np.sqrt(np.maximum(best, 0)) / FLOOR  # the filter can round a hair below 0
    score[best >= FAR] = 0  # no clean photo covers this patch
    score[:EDGE], score[-EDGE:], score[:, :EDGE], score[:, -EDGE:] = 0, 0, 0, 0
    return score, resid, shift, None


def fit(paths):
    """Train on photos of the clean mesh. Returns the model; model.info is the training report."""
    paths = files(paths)
    if len(paths) < 3:
        raise SystemExit("need at least 3 photos of the clean mesh")
    smalls = [_small(read(p)) for p in paths]
    if len({s.shape for s in smalls}) > 1:
        raise SystemExit("photos differ in size")

    # Reference: the photo closest to all the others. Then where every photo sits against it.
    grey = np.stack([_log(s).mean(2) for s in smalls])
    ref = int(np.argmin([np.abs(grey - g).mean() for g in grey]))
    found = [_shift(grey[ref], g) for g in grey]
    keep = [i for i, f in enumerate(found) if f and f[1] >= MIN_CC]
    if len(keep) < 3:
        raise SystemExit("under 3 photos show the same view; are these all the clean mesh?")
    model = Model(np.stack([smalls[i] for i in keep]), np.stack([found[i][0] for i in keep]),
                  keep.index(ref), None, {})

    # Leave-one-out: score each photo against the others; the threshold clears the worst, per region
    loo, maps = {}, []
    for k, i in enumerate(keep):
        score, _, _, blocked = _score(model, smalls[i], skip=k)
        loo[paths[i].name] = None if blocked else round(float(score.max()), 2)  # None: alone where it sits
        if not blocked:
            maps.append(score)
    if not maps:
        raise SystemExit("every photo is far from the others; are these all the clean mesh?")
    worst = cv2.dilate(np.max(maps, 0), np.ones((SPREAD, SPREAD), np.uint8))
    model.limit = (MARGIN * np.maximum(worst, MIN_LIMIT)).astype(np.float32)
    h, w = smalls[0].shape[:2]
    model.info = {
        "trained": datetime.now(UTC).isoformat(timespec="seconds"),
        "photos": [paths[i].name for i in keep],
        "dropped": [paths[i].name for i in range(len(paths)) if i not in keep],  # not the same view
        "size": [round(w / SCALE), round(h / SCALE)],
        "threshold": {q: round(float(np.percentile(model.limit, p)), 2) for q, p in (("min", 0), ("median", 50), ("max", 100))},
        "leave_one_out": loo,
        "camera_wobble_px": round(float(np.hypot(*(model.shifts - model.shifts.mean(0)).T).max() / SCALE), 1),
        "clipped": round(float((smalls[ref] >= SAT).any(2).mean()), 3),  # too bright to see into
    }
    return model


def check(model, img, name=""):
    """Label one photo: clean, microplastic or blocked, with the spots that differ."""
    small = _small(img)
    if small.shape != model.clean.shape[1:]:
        raise ValueError(f"{name}: photo is {img.shape[1]}x{img.shape[0]}, model was trained on "
                         f"{model.info['size'][0]}x{model.info['size'][1]}")
    out = {"photo": name, "label": "blocked", "reason": None, "score": None, "shift": None, "spots": []}
    score, resid, shift, blocked = _score(model, small)
    if shift is not None:
        out["shift"] = [round(float(v) / SCALE, 1) for v in shift]
    if blocked:
        out["reason"] = blocked
        return out
    score /= model.limit
    out["score"] = round(float(score.max()), 2)  # 1 or more is flagged
    hot = score > 1
    if hot.mean() > BLOCKED:
        out["reason"] = f"{hot.mean():.0%} of the photo differs"
        return out

    n, labels, stats, centroids = cv2.connectedComponentsWithStats(hot.astype(np.uint8), connectivity=8)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < MIN_SPOT:
            continue
        inside = labels == i
        out["spots"].append({
            "x": round(float(centroids[i][0]) / SCALE), "y": round(float(centroids[i][1]) / SCALE),
            "bbox": [round(x / SCALE), round(y / SCALE), round((x + w) / SCALE), round((y + h) / SCALE)],
            "area_px": round(float(area) / SCALE**2),
            "strength": round(float(score[inside].max()), 2),
            "darker": bool(resid[inside].mean() < 0),  # blocks light, as an opaque particle does
        })
    out["spots"].sort(key=lambda p: -p["strength"])
    out["label"] = "microplastic" if out["spots"] else "clean"
    return out


COLORS = {"clean": (90, 200, 90), "microplastic": (40, 40, 255), "blocked": (0, 190, 255)}


def overlay(img, result):
    img = img.copy()
    col = COLORS[result["label"]]
    for p in result["spots"]:
        x0, y0, x1, y1 = p["bbox"]
        cv2.rectangle(img, (x0 - 4, y0 - 4), (x1 + 4, y1 + 4), col, 2)
    text = result["label"] + (f"  {len(result['spots'])} spot(s)" if result["spots"] else "")
    if result["reason"]:
        text += f": {result['reason']}"
    cv2.putText(img, text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2, cv2.LINE_AA)
    return img
