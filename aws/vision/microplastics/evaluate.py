"""Score the detector against a folder with truth.json (from synth, or hand-labelled real frames)."""

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from microplastics.detect import IGNORE, analyze


def evaluate(src, rig, blank=None, model=None):
    truth = json.loads((Path(src) / "truth.json").read_text())
    particles, summary = analyze(src, rig, blank, model)
    by_frame = defaultdict(list)
    for p in particles:
        by_frame[p.frame].append(p)

    conf, size_err, false_pos = Counter(), [], 0
    for name, items in truth.items():
        dets, used = by_frame.get(name, []), set()
        for t in items:
            x0, y0, x1, y1 = t["bbox"]
            near = [
                (math.hypot(d.x - t["x"], d.y - t["y"]), j)
                for j, d in enumerate(dets)
                if j not in used and x0 - 3 <= d.x <= x1 + 3 and y0 - 3 <= d.y <= y1 + 3
            ]
            if not near:
                conf[t["shape"], "missed"] += 1
                continue
            j = min(near)[1]
            used.add(j)
            conf[t["shape"], dets[j].shape] += 1
            if t["shape"] not in IGNORE:
                size_err.append(dets[j].size_um / (t["size_px"] * rig.um_per_px) - 1)
        false_pos += sum(1 for j, d in enumerate(dets) if j not in used and d.shape not in IGNORE)

    shapes = sorted({s for s, _ in conf})
    preds = sorted({p for _, p in conf} - {"missed"}) + ["missed"]
    total = {s: sum(conf[s, p] for p in preds) for s in shapes}
    plastic = [s for s in shapes if s not in IGNORE]
    n_true = sum(total[s] for s in plastic)
    found = sum(conf[s, p] for s in plastic for p in preds if p not in IGNORE and p != "missed")
    return {
        "confusion": {s: {p: conf[s, p] for p in preds if conf[s, p]} for s in shapes},
        "recall": round(found / max(n_true, 1), 3),  # real particles detected and counted
        "shape_accuracy": round(sum(conf[s, s] for s in shapes) / max(sum(total.values()), 1), 3),
        "bubbles_rejected": round(conf["bubble", "bubble"] / max(total.get("bubble", 0), 1), 3),
        "false_pos_per_frame": round(false_pos / max(len(truth), 1), 2),
        "size_error_median": round(float(np.median(np.abs(size_err))), 3) if size_err else None,
        "true_per_L": round(n_true / summary["liters"], 2),
        "measured_per_L": summary["microplastics"],
        "ci95": summary["ci95"],
    }
