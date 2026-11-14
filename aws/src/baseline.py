"""The USGS river gauge is the slow, trusted baseline; buoys are the fast, local signal."""

from datetime import date, datetime, timedelta, timezone

import sources


def snapshot():
    """Everything the public sources say right now."""
    today = date.today()
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "usgs": sources.usgs_latest(sources.WQ_GAUGE),
        "stony_brook": sources.usgs_latest(sources.FLOW_GAUGE),
        "weather": sources.weather_daily((today - timedelta(days=2)).isoformat(), today.isoformat()),
        "alerts": sources.nws_alerts(),
    }


def historical(when):
    """Baseline for a past moment, from USGS daily means (used when replaying a real day)."""
    day = when.date().isoformat()
    daily = sources.usgs_daily(sources.WQ_GAUGE, day, day)
    return {
        "time": day,
        "usgs": {k: {"value": rows[0]["mean"], "time": day} for k, rows in daily.items() if rows},
    }


def for_time(when, stored):
    """Stored live baseline when the reading is current; otherwise rebuild it for that day."""
    if abs(datetime.now(timezone.utc) - when) < timedelta(hours=6):
        return stored or snapshot()
    return historical(when)
