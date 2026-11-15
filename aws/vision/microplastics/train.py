"""Labelling loop for real frames, once the rig produces them.

    mp crops frames/ crops/   cuts out every particle into crops/<guessed shape>/
    (fix the folders: drag wrong crops to the right one, add folders like organic/ or sediment/)
    mp train crops/           fits a classifier on the folder names, saves crops/model.joblib
    mp analyze frames/ --model crops/model.joblib

The rules only see shape. Telling plastic from algae, wood or grit takes labelled examples.
"""

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from microplastics.detect import analyze, vector


def crops(src, out, rig, blank=None, pad=8):
    out, feats = Path(out), {}

    def save(name, frame, ps):
        img = (frame * 255).clip(0, 255).astype(np.uint8)
        for i, p in enumerate(ps):
            x0, y0, x1, y1 = p.bbox
            fn = f"{Path(name).stem}_{i:03d}.png"
            (out / p.shape).mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out / p.shape / fn), img[max(0, y0 - pad) : y1 + pad, max(0, x0 - pad) : x1 + pad])
            feats[fn] = vector(p)

    analyze(src, rig, blank, on_frame=save)
    out.mkdir(parents=True, exist_ok=True)
    (out / "features.json").write_text(json.dumps(feats))
    return len(feats)


def train(crops_dir):
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score

    d = Path(crops_dir)
    feats = json.loads((d / "features.json").read_text())
    X, y = [], []
    for f in d.glob("*/*.png"):
        if f.name in feats:
            X.append(feats[f.name])
            y.append(f.parent.name)
    counts = Counter(y)
    if len(counts) < 2:
        raise SystemExit("need at least two labelled classes")
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=0)
    folds = min(5, min(counts.values()))
    score = cross_val_score(clf, X, y, cv=folds).mean() if folds >= 2 else None
    clf.fit(X, y)
    joblib.dump(clf, d / "model.joblib")
    return {"examples": dict(counts), "cv_accuracy": round(float(score), 3) if score is not None else None,
            "model": str(d / "model.joblib")}
