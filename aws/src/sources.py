"""Public data sources around Lake Carnegie, Princeton NJ. Standard library only."""

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

LAKE = (40.343, -74.640)  # lat, lon
UA = "buoy-monitor/0.1 (HackPrinceton; denizlapsekili@gmail.com)"

# Millstone River at Blackwells Mills: nearest gauge with live water quality, ~15 km downstream
WQ_GAUGE = "01402000"
# Stony Brook at Princeton: flow only, feeds the lake
FLOW_GAUGE = "01401000"

USGS_PARAMS = {
    "00010": "water_temp_c",
    "00095": "specific_conductance_us_cm",
    "63680": "turbidity_fnu",
    "00300": "dissolved_oxygen_mg_l",
    "00400": "ph",
    "00060": "discharge_cfs",
    "32315": "chlorophyll_rfu",
    "32321": "phycocyanin_rfu",
}


def get_json(url, data=None, headers=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def usgs_latest(site):
    """Latest instantaneous value of every parameter a USGS site reports."""
    url = (
        "https://api.waterdata.usgs.gov/ogcapi/v0/collections/latest-continuous/items?"
        + urllib.parse.urlencode({"f": "json", "monitoring_location_id": f"USGS-{site}"})
    )
    out = {}
    fresh = datetime.now(timezone.utc) - timedelta(days=2)
    for f in get_json(url)["features"]:
        p = f["properties"]
        name = USGS_PARAMS.get(p["parameter_code"])
        # Retired sensors keep their last value forever; skip anything stale
        if name and datetime.fromisoformat(p["time"]) > fresh:
            out[name] = {"value": _num(p["value"]), "time": p["time"]}
    return out


def usgs_daily(site, start, end, params=("00010", "00095", "63680")):
    """Daily means per parameter between two ISO dates, oldest first."""
    out = {}
    for code in params:
        url = (
            "https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items?"
            + urllib.parse.urlencode(
                {
                    "f": "json",
                    "monitoring_location_id": f"USGS-{site}",
                    "parameter_code": code,
                    "statistic_id": "00003",
                    "time": f"{start}/{end}",
                    "limit": 400,
                }
            )
        )
        rows = [(f["properties"]["time"], _num(f["properties"]["value"])) for f in get_json(url)["features"]]
        out[USGS_PARAMS[code]] = [{"date": d, "mean": v} for d, v in sorted(rows)]
    return out


def weather_daily(start, end):
    """Daily air temperature, rain and snow at the lake (Open-Meteo, ERA5-based)."""
    q = {
        "latitude": LAKE[0],
        "longitude": LAKE[1],
        "start_date": start,
        "end_date": end,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum",
        "timezone": "America/New_York",
    }
    recent = date.fromisoformat(end) >= date.today() - timedelta(days=5)
    host = "api.open-meteo.com/v1/forecast" if recent else "archive-api.open-meteo.com/v1/archive"
    if recent:
        q.pop("start_date"), q.pop("end_date")
        q["past_days"] = max(1, (date.today() - date.fromisoformat(start)).days + 1)
        q["forecast_days"] = 1
    d = get_json(f"https://{host}?" + urllib.parse.urlencode(q))["daily"]
    return [
        {
            "date": t,
            "air_max_c": d["temperature_2m_max"][i],
            "air_min_c": d["temperature_2m_min"][i],
            "rain_mm": d["precipitation_sum"][i],
            "snow_cm": d["snowfall_sum"][i],
        }
        for i, t in enumerate(d["time"])
        if start <= t <= end
    ]


def air_climatology(day, years=10, window=3):
    """Mean daily max air temperature for the same calendar window over past years."""
    d = date.fromisoformat(day)
    q = {
        "latitude": LAKE[0],
        "longitude": LAKE[1],
        "start_date": date(d.year - years, 1, 1).isoformat(),
        "end_date": date(d.year - 1, 12, 31).isoformat(),
        "daily": "temperature_2m_max",
        "timezone": "America/New_York",
    }
    r = get_json("https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(q), timeout=30)["daily"]
    vals = []
    for t, v in zip(r["time"], r["temperature_2m_max"]):
        td = date.fromisoformat(t)
        try:
            same = td.replace(year=d.year)
        except ValueError:  # Feb 29
            continue
        if v is not None and abs((same - d).days) <= window:
            vals.append(v)
    return round(sum(vals) / len(vals), 1) if vals else None


def nws_alerts():
    """Active NWS alerts at the lake (heat, flood, winter storm...). Live only: NWS keeps ~7 days."""
    url = f"https://api.weather.gov/alerts/active?point={LAKE[0]},{LAKE[1]}"
    feats = get_json(url, headers={"Accept": "application/geo+json"})["features"]
    return [
        {
            "event": f["properties"]["event"],
            "severity": f["properties"]["severity"],
            "headline": f["properties"]["headline"],
            "onset": f["properties"]["onset"],
            "ends": f["properties"]["ends"],
        }
        for f in feats
    ]


def dischargers(radius_miles=5):
    """EPA ECHO facilities holding a Clean Water Act discharge permit near the lake."""
    base = "https://echodata.epa.gov/echo/cwa_rest_services"
    q = {"output": "JSON", "p_lat": LAKE[0], "p_long": LAKE[1], "p_radius": radius_miles, "p_ptype": "NPD"}
    qid = get_json(f"{base}.get_facilities?" + urllib.parse.urlencode(q))["Results"]["QueryID"]
    facs = get_json(f"{base}.get_qid?" + urllib.parse.urlencode({"qid": qid, "output": "JSON"}))["Results"]["Facilities"]
    return [
        {
            "name": f["CWPName"],
            "permit": f["SourceID"],
            "type": "sewage treatment" if f.get("CWPFacilityTypeIndicator") == "POTW" else "industrial/other",
            "city": f.get("CWPCity"),
            "naics": f.get("CWPNAICSCodes"),
            "longitude": _num(f.get("FacLong")),
        }
        for f in facs
    ]


def sentinel2_scenes(day, days_back=30, max_cloud=40, limit=3):
    """Recent Sentinel-2 L2A scenes over the lake from the AWS Open Data registry (Earth Search STAC)."""
    end = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=days_back)
    body = {
        "collections": ["sentinel-2-l2a"],
        "intersects": {"type": "Point", "coordinates": [LAKE[1], LAKE[0]]},
        "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        "limit": limit,
    }
    r = get_json(
        "https://earth-search.aws.element84.com/v1/search",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return [
        {
            "id": f["id"],
            "datetime": f["properties"]["datetime"],
            "cloud_cover_pct": round(f["properties"]["eo:cloud_cover"], 1),
            "preview": f["assets"]["thumbnail"]["href"],
            "red_band": f["assets"]["red"]["href"],
            "green_band": f["assets"]["green"]["href"],
        }
        for f in r["features"]
    ]


def conductance_to_psu(us_cm):
    """Rough freshwater conversion of specific conductance (µS/cm at 25 °C) to practical salinity."""
    return None if us_cm is None else round(us_cm * 0.00064, 3)
