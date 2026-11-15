"""Buoy camera: every photo the Pi uploads is labelled clean, microplastic or blocked by that camera's
clean-mesh model (vision/microplastics/mesh.py), and the label is kept for the dashboard. Then the
camera's recent labels are turned into microplastics per litre (vision/microplastics/concentration.py),
which becomes the buoy's microplastics reading.

Each camera has two files in the bucket, which deploy.sh uploads from vision/models/ and which are
picked up with the next photo, without a redeploy:

  models/<node>.npz   the clean-mesh model, from `mp fit`
  models/<node>.json  the rig: {"flow_lpm": litres per minute through the mesh, "view_fraction": how
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


def model(node):
    return _load(f"models/{node}.npz", lambda b: mesh.Model.load(io.BytesIO(b)))


def rig(node):
    return _load(f"models/{node}.json", json.loads)


def estimate(node):
    """Microplastics per litre from the camera's labels over the last few hours. None without a rig
    config or before a run spans two photos."""
    r = rig(node)
    if not r or not r.get("flow_lpm"):
        return None
    since = (datetime.now(UTC) - HISTORY).strftime("%Y-%m-%dT%H:%M:%SZ")
    photos = [(p["time"], p["label"], len(p.get("spots") or [])) for p in store.photos_since(node, since)]
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
    data = store.S3.get_object(Bucket=store.BUCKET, Key=key)["Body"].read()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        log.warning("photo %s isn't an image", key)
        return None
    try:
        r = mesh.check(m, img, key)
    except ValueError as e:  # not the size the model was trained on
        r = {"label": "blocked", "reason": str(e), "score": None, "shift": None, "spots": []}
    r.pop("photo", None)
    time = event.get("time") or datetime.now(UTC).isoformat(timespec="seconds")
    h, w = img.shape[:2]  # spots are in these pixels
    p = {"key": key, "node": node, "time": time, "width": w, "height": h, "model": m.info["trained"], **r}
    store.save_photo(p)
    est = estimate(node)
    log.info("photo %s estimate %s", json.dumps(p), json.dumps(est))
    return p
