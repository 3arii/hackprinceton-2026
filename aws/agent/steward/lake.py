"""Public data around Lake Carnegie, fetched asynchronously.

USGS gauges are the slow, trusted baseline; NWS and Open-Meteo give the weather;
EPA ECHO lists who is permitted to discharge; Sentinel-2 comes from the AWS Open
Data registry.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx

from steward.config import LAKE, USER_AGENT

WQ_GAUGE = "01402000"  # Millstone River at Blackwells Mills, ~15 km downstream, full water quality
FLOW_GAUGE = "01401000"  # Stony Brook at Princeton, flow into the lake

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

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    return _client


async def get_json(url: str, params: dict | None = None, **kw):
    r = await client().get(url, params=params, **kw)
    r.raise_for_status()
    return r.json()


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def conductance_to_psu(us_cm: float | None) -> float | None:
    """Rough freshwater conversion of specific conductance (µS/cm at 25 °C) to practical salinity."""
    return None if us_cm is None else round(us_cm * 0.00064, 3)


# --- USGS ---------------------------------------------------------------------------------


async def usgs_latest(site: str) -> dict:
    """Latest instantaneous value of each parameter, skipping sensors that stopped reporting."""
    d = await get_json(
        "https://api.waterdata.usgs.gov/ogcapi/v0/collections/latest-continuous/items",
        {"f": "json", "monitoring_location_id": f"USGS-{site}"},
    )
    fresh = datetime.now(timezone.utc) - timedelta(days=2)
    out = {}
    for f in d["features"]:
        p = f["properties"]
        name = USGS_PARAMS.get(p["parameter_code"])
        if name and datetime.fromisoformat(p["time"]) > fresh:
            out[name] = {"value": _num(p["value"]), "time": p["time"]}
    return out


async def usgs_daily(site: str, start: str, end: str, codes=("00010", "00095", "63680", "00060", "00300")) -> dict:
    """Daily means per parameter, oldest first."""

    async def one(code):
        d = await get_json(
            "https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items",
            {
                "f": "json",
                "monitoring_location_id": f"USGS-{site}",
                "parameter_code": code,
                "statistic_id": "00003",
                "time": f"{start}/{end}",
                "limit": 400,
            },
        )
        rows = sorted((f["properties"]["time"], _num(f["properties"]["value"])) for f in d["features"])
        return USGS_PARAMS[code], [{"date": t, "mean": v} for t, v in rows]

    return dict(await asyncio.gather(*(one(c) for c in codes)))


# --- Weather ------------------------------------------------------------------------------


async def weather_daily(start: str, end: str) -> list[dict]:
    q = {
        "latitude": LAKE["lat"],
        "longitude": LAKE["lon"],
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,wind_speed_10m_max",
        "timezone": "America/New_York",
    }
    recent = date.fromisoformat(end) >= date.today() - timedelta(days=5)
    if recent:
        url = "https://api.open-meteo.com/v1/forecast"
        q |= {"past_days": max(1, (date.today() - date.fromisoformat(start)).days + 1), "forecast_days": 1}
    else:
        url = "https://archive-api.open-meteo.com/v1/archive"
        q |= {"start_date": start, "end_date": end}
    d = (await get_json(url, q))["daily"]
    return [
        {
            "date": t,
            "air_max_c": d["temperature_2m_max"][i],
            "air_min_c": d["temperature_2m_min"][i],
            "rain_mm": d["precipitation_sum"][i],
            "snow_cm": d["snowfall_sum"][i],
            "wind_max_kmh": d["wind_speed_10m_max"][i],
        }
        for i, t in enumerate(d["time"])
        if start <= t <= end
    ]


async def air_normals(day: str, years: int = 10, window: int = 3) -> dict:
    """Mean and spread of the daily high for the same calendar window over past years."""
    d = date.fromisoformat(day)
    q = {
        "latitude": LAKE["lat"],
        "longitude": LAKE["lon"],
        "start_date": date(d.year - years, 1, 1).isoformat(),
        "end_date": date(d.year - 1, 12, 31).isoformat(),
        "daily": "temperature_2m_max,precipitation_sum",
        "timezone": "America/New_York",
    }
    r = (await get_json("https://archive-api.open-meteo.com/v1/archive", q))["daily"]
    highs, rain = [], []
    for t, hi, pr in zip(r["time"], r["temperature_2m_max"], r["precipitation_sum"]):
        td = date.fromisoformat(t)
        try:
            same = td.replace(year=d.year)
        except ValueError:  # Feb 29
            continue
        if abs((same - d).days) <= window and hi is not None:
            highs.append(hi)
            rain.append(pr or 0)
    if not highs:
        return {}
    mean = sum(highs) / len(highs)
    sd = (sum((h - mean) ** 2 for h in highs) / len(highs)) ** 0.5
    return {
        "normal_high_c": round(mean, 1),
        "high_sd_c": round(sd, 1),
        "record_high_in_window_c": max(highs),
        "normal_daily_rain_mm": round(sum(rain) / len(rain), 1),
        "years": years,
    }


async def nws_alerts() -> list[dict]:
    """Active National Weather Service alerts at the lake. NWS keeps only about a week of alerts."""
    d = await get_json(
        "https://api.weather.gov/alerts/active",
        {"point": f"{LAKE['lat']},{LAKE['lon']}"},
        headers={"Accept": "application/geo+json"},
    )
    return [
        {k: f["properties"].get(k) for k in ("event", "severity", "headline", "onset", "ends")}
        for f in d["features"]
    ]


# --- EPA ECHO -----------------------------------------------------------------------------

ECHO = "https://echodata.epa.gov/echo"


async def dischargers(radius_miles: float = 5) -> list[dict]:
    """Facilities holding a Clean Water Act discharge permit near the lake."""
    q = {"output": "JSON", "p_lat": LAKE["lat"], "p_long": LAKE["lon"], "p_radius": radius_miles, "p_ptype": "NPD"}
    qid = (await get_json(f"{ECHO}/cwa_rest_services.get_facilities", q))["Results"]["QueryID"]
    facs = (await get_json(f"{ECHO}/cwa_rest_services.get_qid", {"qid": qid, "output": "JSON"}))["Results"]["Facilities"]
    return [
        {
            "name": f["CWPName"],
            "permit": f["SourceID"],
            "kind": "sewage treatment plant" if f.get("CWPFacilityTypeIndicator") == "POTW" else "industrial / other",
            "city": f.get("CWPCity"),
            "naics": f.get("CWPNAICSCodes"),
        }
        for f in facs
    ]


async def facility_report(permit: str) -> dict:
    """EPA Detailed Facility Report, cut to what matters for a water-quality investigation."""
    d = (await get_json(f"{ECHO}/dfr_rest_services.get_dfr", {"output": "JSON", "p_id": permit}))["Results"]
    loc = next((p for p in d.get("Permits") or [] if p.get("Latitude")), {})
    quarters = []
    for src in (d.get("CWA3YrCompliance") or {}).get("Sources") or []:
        for st in src.get("Status") or []:
            quarters += [v for k, v in st.items() if k.startswith("Qtr") and v and "No Violation" not in v]
    loads = [
        {"type": l["LoadingsType"], "by_year": [l.get(f"Year{i}Value") for i in range(1, 6)]}
        for l in (d.get("DmrPollLoads") or {}).get("Loads") or []
        if l.get("SourceID") == permit
    ]
    cwa = next((s for s in (d.get("ComplianceSummary") or {}).get("Source") or [] if s.get("Statute") == "CWA"), {})
    return {
        "permit": permit,
        "name": loc.get("FacilityName"),
        "lat": _num(loc.get("Latitude")),
        "lon": _num(loc.get("Longitude")),
        "cwa_quarters_in_noncompliance_3yr": cwa.get("QtrsInNC"),
        "significant_noncomplier": cwa.get("CurrentSNC"),
        "recent_violation_statuses": sorted(set(quarters))[:6],
        "dmr_loads_lb_per_year_2022_to_2026": loads[:4],
        "receiving_watersheds": [
            {"name": w.get("WBD12Name"), "possible_impairing": w.get("PossibleImpairingParameters")}
            for w in (d.get("Watersheds") or {}).get("WBD12s") or []
        ][:3],
    }
