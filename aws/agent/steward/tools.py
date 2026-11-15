"""Tools, grouped by the agent that owns them. Every tool reads the case from runtime context."""

import asyncio
import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from deepagents.backends.utils import create_file_data
from langchain.tools import ToolRuntime, tool

from steward import bluesky, fleet, lake, photon, sentinel
from steward.config import ALERT_TOPIC, BUOYS, aws
from steward.harness import case_of


def _when(runtime: ToolRuntime) -> datetime:
    return datetime.fromisoformat(case_of(runtime)["reading_time"])


def _day(runtime: ToolRuntime) -> date:
    return _when(runtime).astimezone(timezone.utc).date()


def _is_live(runtime: ToolRuntime) -> bool:
    return abs(datetime.now(timezone.utc) - _when(runtime)) < timedelta(hours=6)


def _dump(obj) -> str:
    return json.dumps(obj, default=str)


# --- Shared ---------------------------------------------------------------------------------


@tool(parse_docstring=True)
async def buoy_network(runtime: ToolRuntime, hours_before: int = 6) -> str:
    """Readings from all four buoys around the anomaly: latest values, range, and first value in the window.

    Use it to tell a lake-wide change (every buoy moves together) from a local one (one buoy moves alone).

    Args:
        hours_before: Hours of history before the anomaly to include. Defaults to 6.
    """
    case = case_of(runtime)
    return _dump(await fleet.network(case["reading_time"], hours_before, case.get("run")))


# --- Hydrology ------------------------------------------------------------------------------


@tool(parse_docstring=True)
async def river_gauge(runtime: ToolRuntime, days_before: int = 10) -> str:
    """USGS Millstone River gauge at Blackwells Mills (the lake's baseline, ~15 km downstream).

    Daily means of water temperature, specific conductance (salinity proxy), turbidity, discharge and
    dissolved oxygen for the days before the anomaly, plus Stony Brook inflow. When the anomaly is live,
    also the latest 15-minute readings.

    Args:
        days_before: Days of daily history to return. Defaults to 10.
    """
    day = _day(runtime)
    out = {
        "site": "USGS 01402000 Millstone River at Blackwells Mills",
        "daily": await lake.usgs_daily(lake.WQ_GAUGE, (day - timedelta(days=days_before)).isoformat(), day.isoformat()),
        "stony_brook_inflow_daily": await lake.usgs_daily(
            lake.FLOW_GAUGE, (day - timedelta(days=days_before)).isoformat(), day.isoformat(), codes=("00060",)
        ),
    }
    out["salinity_psu_from_conductance"] = [
        {"date": r["date"], "psu": lake.conductance_to_psu(r["mean"])} for r in out["daily"].get("specific_conductance_us_cm", [])
    ]
    if _is_live(runtime):
        out["latest"] = await lake.usgs_latest(lake.WQ_GAUGE)
    return _dump(out)


@tool(parse_docstring=True)
async def weather(runtime: ToolRuntime, days_before: int = 7) -> str:
    """Daily air temperature, rain, snow and wind at the lake before the anomaly, compared with the 10-year normal.

    Includes active National Weather Service alerts (heat, flood, winter storm) when the anomaly is live.

    Args:
        days_before: Days of daily history to return. Defaults to 7.
    """
    day = _day(runtime)
    out = {
        "daily": await lake.weather_daily((day - timedelta(days=days_before)).isoformat(), day.isoformat()),
        "normals_for_date": await lake.air_normals(day.isoformat()),
    }
    out["nws_alerts"] = await lake.nws_alerts() if _is_live(runtime) else "historical date: NWS keeps alerts for about a week"
    return _dump(out)


# --- Satellite ------------------------------------------------------------------------------


@tool(parse_docstring=True)
async def satellite_scenes(runtime: ToolRuntime, days_back: int = 45, max_cloud_pct: int = 60) -> str:
    """List Sentinel-2 scenes over Lake Carnegie from the AWS Open Data registry, newest first.

    Args:
        days_back: How far back from the anomaly to search. Defaults to 45.
        max_cloud_pct: Skip scenes with more cloud than this over the whole tile. Defaults to 60.
    """
    items = await sentinel.scenes(_day(runtime).isoformat(), days_back, max_cloud_pct)
    return _dump([{"scene": i["id"], "acquired": i["properties"]["datetime"], "cloud_pct": round(i["properties"]["eo:cloud_cover"], 1)} for i in items])


@tool(parse_docstring=True)
async def analyze_lake_imagery(runtime: ToolRuntime, scenes: int = 5, days_back: int = 45) -> str:
    """Measure the lake's water in recent Sentinel-2 scenes and save a true-color image of the newest clear one.

    For each scene: how much of the lake is cloud-free, and over open water the turbidity index NDTI
    (red vs green; higher = more suspended sediment) and chlorophyll index NDCI (red-edge vs red;
    higher = more algae), lake-wide and within 60 m of each buoy. The newest scene with a clear view
    of the lake is saved to /case/satellite/lake.png (water outlined in blue, buoys in red); read it
    with read_file to look at it.

    Args:
        scenes: How many recent scenes to measure, newest first. Defaults to 5.
        days_back: How far back from the anomaly to search. Defaults to 45.
    """
    items = await sentinel.scenes(_day(runtime).isoformat(), days_back, 80, limit=max(1, min(scenes, 8)))
    results = await asyncio.gather(*(sentinel.analyze(i, image=True) for i in items), return_exceptions=True)
    rows, saved = [], None
    for item, res in zip(items, results):
        if isinstance(res, Exception):
            rows.append({"scene": item["id"], "error": str(res)})
            continue
        png = res.pop("png", None)
        if saved is None and png and res["lake_window_cloud_pct"] < 30 and res["lake"]:
            case = case_of(runtime)
            data = create_file_data(base64.b64encode(png).decode(), encoding="base64")
            await runtime.store.aput(("case", case["case_id"]), "/satellite/lake.png", data)
            saved = {"path": "/case/satellite/lake.png", "scene": res["scene"], "acquired": res["acquired"]}
        rows.append(res)
    clear = [r for r in rows if r.get("lake") and r.get("lake_window_cloud_pct", 100) < 30]
    summary = {}
    if clear:
        ndti = sorted(r["lake"]["ndti_mean"] for r in clear)
        summary = {
            "clear_scenes": len(clear),
            "newest_clear_ndti": clear[0]["lake"]["ndti_mean"],
            "median_ndti": ndti[len(ndti) // 2],
            "newest_clear_ndci": clear[0]["lake"]["ndci_mean"],
        }
    return _dump({"scenes": rows, "summary": summary, "image": saved or "no scene with a clear view of the lake"})


# --- Community (Bluesky) ------------------------------------------------------------------

SOCIAL_POSTS = "/social/posts.json"


async def _load_posts(runtime: ToolRuntime) -> list[dict]:
    item = await runtime.store.aget(("case", case_of(runtime)["case_id"]), SOCIAL_POSTS)
    return json.loads(item.value["content"]) if item else []


@tool(parse_docstring=True)
async def search_bluesky(runtime: ToolRuntime, query: str, hours_before: int = 72, hours_after: int = 24, limit: int = 40) -> str:
    """Search public Bluesky posts in a window around the anomaly, newest first. Every post found is kept
    for analyze_conversation.

    Bluesky search matches words, so search for what people would actually write: symptoms ("dead fish",
    "smells like sewage", "lake is brown", "heat advisory"), places ("Princeton", "Lake Carnegie",
    "Millstone River", "Mercer County"), and combinations of the two.

    Args:
        query: Search words. Short and concrete works best.
        hours_before: Hours before the anomaly to search from. Defaults to 72.
        hours_after: Hours after the anomaly to search to. Defaults to 24.
        limit: Most posts to fetch for this query. Defaults to 40.
    """
    when = _when(runtime)
    since = (when - timedelta(hours=hours_before)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = (when + timedelta(hours=hours_after)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = await bluesky.search(query, since, until, min(limit, 100))
    kept = {p["uri"]: p for p in await _load_posts(runtime)}
    new = [p for p in found if p["uri"] not in kept]
    kept.update({p["uri"]: p for p in found})
    await runtime.store.aput(
        ("case", case_of(runtime)["case_id"]), SOCIAL_POSTS, create_file_data(json.dumps(list(kept.values())))
    )
    shown = sorted(found, key=lambda p: (p["local"], p["likes"] + p["reposts"]), reverse=True)[:15]
    return _dump(
        {
            "query": query,
            "window": [since, until],
            "found": len(found),
            "new": len(new),
            "kept_total": len(kept),
            "mentioning_local_places": sum(p["local"] for p in found),
            "posts": [
                {"time": p["time"][:16], "handle": p["handle"], "local": p["local"], "engagement": p["likes"] + p["reposts"], "text": p["text"][:240]}
                for p in shown
            ],
        }
    )


@tool(parse_docstring=True)
async def analyze_conversation(runtime: ToolRuntime) -> str:
    """Analyse every post kept so far, content and connectivity together.

    Builds the graph of who replies to, quotes, reposts and follows whom among the authors, adds links
    between accounts that posted near-identical text within an hour, finds communities (Louvain), and
    scores each for coordination: copy-paste text, bursty timing, accounts under 30 days old, dense ties.
    Separates independent local voices from campaigns and automated mirror feeds. Saves the network to
    /case/social/network.json for the dashboard.

    Args:
        runtime: Injected.
    """
    posts = await _load_posts(runtime)
    result = await bluesky.analyze(posts)
    network = result.pop("network", None)
    if network:
        await runtime.store.aput(("case", case_of(runtime)["case_id"]), "/social/network.json", create_file_data(json.dumps(network)))
    return _dump(result)


# --- Permits --------------------------------------------------------------------------------


@tool(parse_docstring=True)
async def permitted_dischargers(runtime: ToolRuntime, radius_miles: float = 5) -> str:
    """EPA ECHO: facilities with a Clean Water Act discharge permit near the lake.

    Args:
        radius_miles: Search radius around the lake. Defaults to 5.
    """
    return _dump(await lake.dischargers(radius_miles))


@tool(parse_docstring=True)
async def facility_compliance(runtime: ToolRuntime, permit: str) -> str:
    """EPA Detailed Facility Report for one permit: location, distance and bearing from each buoy, 3-year
    Clean Water Act compliance, significant-noncomplier status, discharge loads by year, and the
    impairments listed for its receiving watershed.

    Args:
        permit: NPDES permit ID, for example NJ0023922.
    """
    import math

    rep = await lake.facility_report(permit)
    if rep.get("lat") and rep.get("lon"):
        rep["from_buoys"] = {}
        for n, b in BUOYS.items():
            dy = (rep["lat"] - b["lat"]) * 111.0
            dx = (rep["lon"] - b["lon"]) * 111.0 * math.cos(math.radians(b["lat"]))
            bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360
            compass = "N NE E SE S SW W NW".split()[round(bearing / 45) % 8]
            rep["from_buoys"][f"buoy_{n}"] = f"{math.hypot(dx, dy):.1f} km {compass}"
    return _dump(rep)


# --- History --------------------------------------------------------------------------------


@tool(parse_docstring=True)
async def past_incidents(runtime: ToolRuntime, limit: int = 25) -> str:
    """Earlier anomalies on this lake and how they were resolved (verdict, cause, confidence).

    Args:
        limit: How many recent incidents to return. Defaults to 25.
    """
    return _dump(await fleet.incidents(limit))


# --- Supervisor -----------------------------------------------------------------------------


@tool(parse_docstring=True)
async def submit_verdict(
    runtime: ToolRuntime,
    verdict: str,
    cause: str,
    confidence: float,
    summary: str,
    evidence: list[str],
) -> str:
    """Record the investigation's outcome and text it to the lake manager (iMessage). Call exactly once,
    after weighing every subagent's evidence.

    Args:
        verdict: "benign" (a natural cause explains it), "monitor" (unexplained, no harm yet) or "warn" (likely pollution or danger).
        cause: The most likely cause, a few words.
        confidence: 0 to 1.
        summary: Two or three sentences for the dashboard.
        evidence: Short facts with numbers, each naming its source.
    """
    if verdict not in ("benign", "monitor", "warn"):
        return "verdict must be benign, monitor or warn"
    case = case_of(runtime)
    thread_id = (runtime.config.get("configurable") or {}).get("thread_id")
    await fleet.save(
        {
            "pk": "INCIDENT",
            "sk": f"{case['reading_time']}#{case['buoy']}#{case['metric']}",
            "id": case["case_id"],
            "engine": "deepagents",
            "thread_id": thread_id,
            "detected_at": case["reading_time"],
            "investigated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "buoy": case["buoy"],
            "metric": case["metric"],
            "value": case["value"],
            "reason": case["reason"],
            "run": case.get("run"),
            "verdict": verdict,
            "cause": cause,
            "confidence": confidence,
            "summary": summary,
            "evidence": evidence,
        }
    )
    # Every verdict goes to the manager's phone, not only warnings
    icon = {"benign": "✅", "monitor": "👀", "warn": "⚠️"}[verdict]
    unit = f" {case['unit']}" if case.get("unit") else ""
    brief = summary if len(summary) <= 320 else summary[:317].rsplit(" ", 1)[0] + "…"
    text = (
        f"{icon} Lake Carnegie · Buoy {case['buoy']} {case['metric']} {case['value']}{unit}\n"
        f"{verdict.upper()} ({round(confidence * 100)}% confident): {cause}\n\n{brief}\n\nReply \"why\" for the evidence."
    )
    try:
        await photon.send_text(text, key=f"verdict:{case['case_id']}")
        texted = " and texted to the lake manager"
    except Exception as e:
        texted = f"; the text to the lake manager failed ({e})"
    return f"Recorded verdict '{verdict}' for case {case['case_id']}{texted}."


@tool(parse_docstring=True)
async def send_alert(runtime: ToolRuntime, subject: str, message: str) -> str:
    """Alert the lake manager by iMessage (Photon) and email (Amazon SNS). Use only after a "warn" verdict.

    Args:
        subject: Short subject line for the email, under 90 characters.
        message: What happened, where, how sure you are, and what the manager should do now. Plain text, under 120 words; it is read on a phone.
    """
    case = case_of(runtime)
    sent = []
    try:
        await photon.send_text(
            f"What to do now: {message}",
            key=f"alert:{case['case_id']}",
        )
        sent.append("iMessage")
    except Exception as e:  # keep the email path even if the phone line isn't ready
        sent.append(f"iMessage failed ({e})")
    if ALERT_TOPIC:
        aws("sns").publish(TopicArn=ALERT_TOPIC, Subject=subject[:100], Message=f"{message}\n\nCase {case['case_id']}")
        sent.append("email")
    return "Alert: " + ", ".join(sent)


# --- Liaison (answers the manager's texts) ----------------------------------------------


@tool(parse_docstring=True)
async def recent_cases(runtime: ToolRuntime, limit: int = 5) -> str:
    """The most recent investigations: buoy, metric, value, verdict, cause, confidence and summary, newest first.

    Args:
        limit: How many cases. Defaults to 5.
    """
    rows = [i for i in await fleet.incidents(limit * 3) if i.get("summary")][:limit]
    return _dump(rows)


@tool(parse_docstring=True)
async def case_evidence(runtime: ToolRuntime, case_id: str, specialist: str) -> str:
    """Read one specialist's evidence file from a case.

    Args:
        case_id: The case ID from recent_cases.
        specialist: hydrology, satellite, permits, community or history.
    """
    item = await runtime.store.aget(("case", case_id), f"/evidence/{specialist}.md")
    return item.value["content"][:6000] if item else f"No {specialist} evidence in case {case_id}."


@tool(parse_docstring=True)
async def lake_now(runtime: ToolRuntime) -> str:
    """Latest readings from all four buoys and the USGS river baseline, right now.

    Args:
        runtime: Injected.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    buoys, river = await asyncio.gather(fleet.network(now, 1), lake.usgs_latest(lake.WQ_GAUGE))
    return _dump({"buoys": buoys, "river": river})


@tool(parse_docstring=True)
async def reply(runtime: ToolRuntime, text: str) -> str:
    """Send a text to the person who texted, as an iMessage in their conversation. Call once per turn: the answer,
    or a one-line heads-up when you launch a live check.

    Args:
        text: Plain text, short enough for a phone screen (under 100 words). No markdown.
    """
    ctx = runtime.context if isinstance(runtime.context, dict) else vars(runtime.context)
    parent = ctx.get("message_id") or ""
    # Without the conversation (it shouldn't happen) the text still reaches the registered manager
    await photon.send_text(
        text, space_id=ctx.get("space_id"), sender_id=ctx.get("agent_id"), reply_to=parent if parent.startswith("pho_msg_") else None,
        key=f"reply:{ctx.get('message_id')}:{hashlib.sha1(text.encode()).hexdigest()[:12]}",
    )
    return "Sent."

