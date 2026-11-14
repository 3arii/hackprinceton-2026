"""Simulated buoy fleet (stand-in for hardware) and the anomaly rules applied to every reading.

The profiles mirror frontend/src/lib/buoyData.ts so the dashboard and the backend agree.
"""

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sources import conductance_to_psu

BUOYS = {
    1: {"lon": -74.6413, "lat": 40.3433},
    2: {"lon": -74.6398, "lat": 40.34338},
    3: {"lon": -74.6389, "lat": 40.34295},
    4: {"lon": -74.6409, "lat": 40.34262},
}
METRICS = ("temperature", "microplastics", "salinity", "turbidity")
UNITS = {"temperature": "°C", "microplastics": "particles/L", "salinity": "PSU", "turbidity": "NTU"}

# seed, temperature, microplastics, salinity, turbidity (same as buoyData.ts)
PROFILES = {
    1: (1.3, 9.4, 6.2, 0.17, 13.5),
    2: (2.9, 9.9, 4.1, 0.14, 8.2),
    3: (4.4, 10.2, 3.3, 0.13, 6.9),
    4: (6.1, 9.7, 4.8, 0.15, 10.4),
}


def _drift(hours, seed):
    return (
        math.sin(hours / 5.3 + seed) * 0.5
        + math.sin(hours / 2.1 + seed * 1.7) * 0.32
        + math.sin(hours / 0.83 + seed * 2.3) * 0.18
    )


def simulated(buoy, when):
    seed, temp, mp, sal, turb = PROFILES[buoy]
    hours = when.timestamp() / 3600
    local = when.astimezone(ZoneInfo("America/New_York"))
    hod = local.hour + local.minute / 60
    s = lambda i: seed + i * 0.9  # noqa: E731
    return {
        "temperature": round(temp + 0.45 * math.sin(2 * math.pi * (hod - 9) / 24) + 0.15 * _drift(hours, s(0)), 2),
        "microplastics": round(max(0, mp + 0.7 * _drift(hours, s(1))), 2),
        "salinity": round(max(0, sal + 0.008 * _drift(hours, s(2))), 3),
        "turbidity": round(max(0, turb + 1.8 * _drift(hours, s(3))), 2),
    }


def reading(buoy, when=None, **overrides):
    when = when or datetime.now(timezone.utc)
    return {
        "buoy": buoy,
        "time": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
        **simulated(buoy, when),
        **overrides,
    }


# Demo scenarios: (time, per-buoy overrides). Times are real days with real public data behind them.
SCENARIOS = {
    # 2 Jul 2026, 3 pm: air 39.4 °C, Millstone River daily mean 29.4 °C. Hot water, natural cause.
    "heatwave": (
        datetime(2026, 7, 2, 19, 0, tzinfo=timezone.utc),
        {
            1: {"temperature": 31.4, "turbidity": 16.2},
            2: {"temperature": 31.9, "turbidity": 10.1},
            3: {"temperature": 32.2, "turbidity": 8.4},
            4: {"temperature": 31.6, "turbidity": 12.0},
        },
    ),
    # 13 Nov 2026, 2 pm: the day after 49 mm of rain; the river's turbidity tripled. Muddy everywhere, natural cause.
    "runoff": (
        datetime(2026, 11, 13, 19, 0, tzinfo=timezone.utc),
        {
            1: {"turbidity": 92.0, "microplastics": 9.1},
            2: {"turbidity": 66.4},
            3: {"turbidity": 63.8},
            4: {"turbidity": 75.3},
        },
    ),
    # Right now: only buoy 3, the one nearest the permitted dischargers east of the lake, runs hot.
    "discharge": (None, {3: {"temperature": 19.6}}),
}


def check(r, base):
    """Return the metrics of reading `r` that are extreme against the USGS baseline `base`."""
    usgs = base.get("usgs", {})
    val = lambda k: (usgs.get(k) or {}).get("value")  # noqa: E731
    river_t, river_sc, river_turb = val("water_temp_c"), val("specific_conductance_us_cm"), val("turbidity_fnu")
    river_psu = conductance_to_psu(river_sc)
    out = []

    def flag(metric, ref, why):
        out.append({"metric": metric, "value": r[metric], "unit": UNITS[metric], "baseline": ref, "reason": why})

    t = r["temperature"]
    if t > 30:
        flag("temperature", river_t, f"{t} °C is above the 30 °C stress limit for fish")
    elif river_t is not None and t - river_t > 4:
        flag("temperature", river_t, f"{t} °C is {t - river_t:.1f} °C warmer than the Millstone River ({river_t} °C)")

    s = r["salinity"]
    if s > max(0.5, 2.5 * (river_psu or 0)):
        flag("salinity", river_psu, f"{s} PSU vs about {river_psu} PSU in the river")

    tb = r["turbidity"]
    if tb > max(40, 3 * (river_turb or 0)):
        flag("turbidity", river_turb, f"{tb} NTU vs {river_turb} FNU in the river")

    if r["microplastics"] > 12:
        flag("microplastics", None, f"{r['microplastics']} particles/L, above the 12 particles/L alert level")
    return out
