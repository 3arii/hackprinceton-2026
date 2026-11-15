"""Train the mesh network's water-quality grade (0-100) on the river's real record.

The buoys measure temperature and turbidity, but a water quality index needs more: above all
dissolved oxygen, which they can't measure. The USGS gauge on the Millstone River (the river Lake
Carnegie is a reservoir of) logs all of it, so this learns the index from that record:

  label     each day's Water Quality Index, from the NSF WQI sub-index curves (read off the published
            charts, piecewise linear) for dissolved oxygen (% saturation), turbidity and total dissolved
            solids (0.65 x specific conductance), with the NSF weights scaled to sum to 1. pH has no
            daily record at this gauge, so it isn't in.
  features  what a buoy measures: water temperature, and turbidity as log(1 + NTU)
  model     linear regression (least squares), scored two ways, then refitted on everything:
            5-fold cross-validation over shuffled days (how well it follows the seasons), and the
            newest year after training on the years before (harder: the river's level shifts
            between years, e.g. 2025-26 ran about 5 points higher, which no temperature predicts)

    vision/.venv/bin/python scripts/train_quality.py   # writes frontend/src/lib/qualityModel.json
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sources

YEARS = 3
OUT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "qualityModel.json"

# NSF WQI sub-index curves, (value, quality 0-100)
DO_SAT = [(0, 0), (10, 8), (20, 15), (30, 25), (40, 37), (50, 48), (60, 59), (70, 72), (80, 85), (85, 90),
          (90, 95), (100, 99), (110, 96), (120, 88), (130, 78), (140, 68), (150, 60)]
TURBIDITY = [(0, 100), (5, 87), (10, 77), (15, 69), (20, 62), (30, 52), (40, 44), (50, 38), (60, 33), (70, 28),
             (80, 24), (90, 20), (100, 17), (101, 5)]
TDS = [(0, 80), (50, 90), (100, 85), (150, 79), (200, 73), (250, 66), (300, 59), (350, 53), (400, 47), (450, 41),
       (500, 36), (501, 20)]
WEIGHTS = {"do": 0.17, "turbidity": 0.08, "tds": 0.07}  # NSF weights of the three we have


def curve(points, x):
    xs, ys = zip(*points)
    return float(np.interp(x, xs, ys))


def do_saturation(do_mg_l, temp_c):
    """Percent of the oxygen fresh water holds at this temperature (APHA, sea level)."""
    t = temp_c
    full = 14.652 - 0.41022 * t + 0.0079910 * t**2 - 0.000077774 * t**3
    return 100 * do_mg_l / full


def wqi(temp_c, do_mg_l, turbidity, conductance):
    q = {
        "do": curve(DO_SAT, do_saturation(do_mg_l, temp_c)),
        "turbidity": curve(TURBIDITY, turbidity),
        "tds": curve(TDS, 0.65 * conductance),
    }
    return sum(WEIGHTS[k] * q[k] for k in q) / sum(WEIGHTS.values())


def features(temp_c, turbidity):
    return [temp_c, np.log1p(max(turbidity, 0.0))]


def record(end):
    """Daily means with all four parameters, oldest first."""
    days = {}
    for y in range(YEARS):
        stop = end - timedelta(days=365 * y)
        start = stop - timedelta(days=364)
        got = sources.usgs_daily(sources.WQ_GAUGE, start.isoformat(), stop.isoformat(),
                                 params=("00010", "00095", "63680", "00300"))
        for name, rows in got.items():
            for r in rows:
                if r["mean"] is not None:
                    days.setdefault(r["date"][:10], {})[name] = r["mean"]
    keys = ("water_temp_c", "dissolved_oxygen_mg_l", "turbidity_fnu", "specific_conductance_us_cm")
    return [(d, v) for d, v in sorted(days.items()) if all(k in v for k in keys)]


def fit(X, y):
    A = np.c_[np.ones(len(X)), X]
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return coef


def predict(coef, X):
    return coef[0] + np.asarray(X) @ coef[1:]


def main():
    end = date.today() - timedelta(days=1)
    rows = record(end)
    X = np.array([features(v["water_temp_c"], v["turbidity_fnu"]) for _, v in rows])
    y = np.array([wqi(v["water_temp_c"], v["dissolved_oxygen_mg_l"], v["turbidity_fnu"], v["specific_conductance_us_cm"])
                  for _, v in rows])
    def r2(err, truth):
        return round(1 - float((err**2).sum() / ((truth - truth.mean()) ** 2).sum()), 3)

    order = np.random.default_rng(0).permutation(len(y))
    cv = np.empty(len(y))
    for fold in np.array_split(order, 5):
        rest = np.setdiff1d(order, fold)
        cv[fold] = predict(fit(X[rest], y[rest]), X[fold]) - y[fold]
    split = next(i for i, (d, _) in enumerate(rows) if d > (end - timedelta(days=365)).isoformat())
    ahead = predict(fit(X[:split], y[:split]), X[split:]) - y[split:]
    coef = fit(X, y)
    model = {
        "model": "linear regression",
        "target": "Water Quality Index from the NSF sub-index curves for dissolved oxygen (% saturation), "
                  "turbidity and total dissolved solids, NSF weights",
        "features": ["water_temp_c", "log1p_turbidity_ntu"],
        "intercept": round(float(coef[0]), 4),
        "coefficients": [round(float(c), 4) for c in coef[1:]],
        "median_turbidity_ntu": round(float(np.median([v["turbidity_fnu"] for _, v in rows])), 2),
        "trained_on": {
            "site": f"USGS {sources.WQ_GAUGE}, Millstone River at Blackwells Mills, NJ (daily means)",
            "days": len(rows), "from": rows[0][0], "to": rows[-1][0],
            "index_range": [round(float(y.min()), 1), round(float(y.max()), 1)],
        },
        "cross_validation": {"folds": 5, "r2": r2(cv, y), "mae": round(float(np.abs(cv).mean()), 2)},
        "newest_year": {"train_days": split, "test_days": len(rows) - split, "r2": r2(ahead, y[split:]),
                        "mae": round(float(np.abs(ahead).mean()), 2)},
    }
    OUT.write_text(json.dumps(model, indent=2) + "\n")
    print(json.dumps(model, indent=2))


if __name__ == "__main__":
    main()
