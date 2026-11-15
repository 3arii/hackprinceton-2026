"""Buoy camera: every photo the Pi uploads is labelled clean, microplastic or blocked by that camera's
clean-mesh model (vision/microplastics/mesh.py), and the label is kept for the dashboard. Then the
camera's recent labels are turned into microplastics per litre (vision/microplastics/concentration.py),
which becomes the buoy's microplastics reading.

A model knows one camera angle, so the camera teaches itself new ones: when photos stop matching the
model (the camera was re-aimed, bumped, or the buoy went into the water) and BASELINE photos in a row
agree on what they see, they become the new model, and checking carries on from that view. Particles
already on the mesh then become part of the clean view, so concentration counts what arrives after.

Each camera has these files in the bucket, picked up with the next photo, without a redeploy:

  models/<node>.npz       the clean-mesh model from `mp fit` (deploy.sh uploads vision/models/)
  models/<node>/auto.npz  the model the camera learnt for its newest view; the newer of the two is used
  models/<node>.json      the rig: {"flow_lpm": litres per minute through the mesh, "view_fraction": how
                          much of the mesh the camera sees, "measured": false while those are guesses}
"""

import io
import json
import logging
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
from botocore.exceptions import ClientError
from microplastics import concentration, mesh

import store

log = logging.getLogger()
log.setLevel(logging.INFO)
FILES = {}  # bucket key -> (etag, parsed)
HISTORY = timedelta(hours=6)  # labels an estimate looks back over
BASELINE = 4  # photos in a row that must agree on a new view before it becomes the model
SETTLE = timedelta(minutes=15)  # how far back those photos may go


def _load(key, parse):
    """A file from the bucket, parsed, and re-read only when it changes. None if it isn't there."""
    try:
        etag = store.S3.head_object(Bucket=store.BUCKET, Key=key)["ETag"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise
    if FILES.get(key, (None,))[0] != etag:
        body = store.S3.get_object(Bucket=store.BUCKET, Key=key)["Body"].read()
        FILES[key] = (etag, parse(body))
    return FILES[key][1]


def _npz(body):
    return mesh.Model.load(io.BytesIO(body))


def model(node):
    """The camera's model: whichever is newer, the one trained with mp fit or the one it learnt."""
    found = [m for m in (_load(f"models/{node}.npz", _npz), _load(f"models/{node}/auto.npz", _npz)) if m]
    return max(found, key=lambda m: m.info["trained"]) if found else None


def rig(node):
    return _load(f"models/{node}.json", json.loads)


def _photo(key):
    """The photo, and when it was uploaded: S3's own time, so a photo keeps one record however often
    it's processed (the upload event's time can be a second off)."""
    res = store.S3.get_object(Bucket=store.BUCKET, Key=key)
    img = cv2.imdecode(np.frombuffer(res["Body"].read(), np.uint8), cv2.IMREAD_COLOR)
    return img, res["LastModified"].strftime("%Y-%m-%dT%H:%M:%SZ")


def learn(node, key, img, time):
    """A new model for this view if, with this photo, BASELINE photos in a row agree on it: the
    photos that just failed to match the model, newest first, while each aligns with this one.
    Returns (model, [(key, time, image)] it learnt from), or (None, how many agree so far)."""
    since = (datetime.fromisoformat(time) - SETTLE).strftime("%Y-%m-%dT%H:%M:%SZ")
    agree = [(key, time, img)]
    ref = mesh._log(mesh._small(img)).mean(2)
    for p in sorted(store.photos_since(node, since), key=lambda p: p["time"], reverse=True):
        if p["key"] == key or p["time"] > time:
            continue
        if not p.get("new_view") or len(agree) == 2 * BASELINE:
            break  # the run ends at the newest photo that matched a model
        other, _ = _photo(p["key"])
        got = other is not None and other.shape == img.shape and mesh._shift(ref, mesh._log(mesh._small(other)).mean(2))
        if not got or got[1] < mesh.MIN_CC or np.hypot(*got[0]) / mesh.SCALE > mesh.MAX_SHIFT:
            break
        agree.append((p["key"], p["time"], other))
    if len(agree) < BASELINE:
        return None, len(agree)
    try:
        new = mesh.fit_images([(k, im) for k, _, im in agree])
    except SystemExit as e:  # they don't agree closely enough after all
        log.info("new view for %s not learnt: %s", node, e)
        return None, 1
    new.info.update(auto=True, learnt_from=[k for k, _, _ in agree])
    new.save("/tmp/auto.npz")
    with open("/tmp/auto.npz", "rb") as f:
        store.S3.put_object(Bucket=store.BUCKET, Key=f"models/{node}/auto.npz", Body=f.read())
    log.info("camera %s learnt a new view from %s", node, json.dumps(new.info["learnt_from"]))
    return new, agree


def label(m, img, key):
    try:
        r = mesh.check(m, img, key)
    except ValueError as e:  # not the size the model was trained on
        r = {"label": "blocked", "reason": str(e), "score": None, "shift": None, "spots": []}
    r.pop("photo", None)
    h, w = img.shape[:2]  # spots are in these pixels
    return {"width": w, "height": h, "model": m.info["trained"], **r}


def estimate(node, m):
    """Microplastics per litre from the camera's labels under its current model. None without a rig
    config or before a run spans two photos."""
    r = rig(node)
    if not r or not r.get("flow_lpm"):
        return None
    since = (datetime.now(UTC) - HISTORY).strftime("%Y-%m-%dT%H:%M:%SZ")
    photos = [p for p in store.photos_since(node, since) if p.get("model") == m.info["trained"]]  # one view
    est = concentration.estimate(photos, float(r["flow_lpm"]), float(r.get("view_fraction", 1.0)))
    if est:
        est["measured"] = bool(r.get("measured"))  # false: flow and view are guesses, so is the result
        store.save_estimate(node, est)
    return est


def on_photo(event, context):
    """S3 Object Created under images/ (via EventBridge): one photo, images/<node>/yyyy/mm/dd/<file>.jpg."""
    key = event["detail"]["object"]["key"]
    node = key.split("/")[1]
    m = model(node)
    if m is None:
        log.info("photo %s: camera %s has no model yet (train one with mp fit)", key, node)
        return None
    img, time = _photo(key)
    if img is None:
        log.warning("photo %s isn't an image", key)
        return None
    r = label(m, img, key)
    if r.get("new_view"):
        new, learnt = learn(node, key, img, time)
        if new:
            m = new
            for k, t, im in learnt:  # the photos it learnt from are the new view's clean start
                if k != key:
                    store.save_photo({"key": k, "node": node, "time": t, **label(m, im, k)})
            r = label(m, img, key)
        else:
            r["reason"] = f"new camera angle, learning it ({learnt} of {BASELINE} photos agree)"
    p = {"key": key, "node": node, "time": time, **r}
    store.save_photo(p)
    est = estimate(node, m)
    log.info("photo %s estimate %s", json.dumps(p), json.dumps(est))
    return p
