"""Investigation agent: an anomaly comes in, Claude gathers public evidence with tools, then decides.

Verdicts: benign (explained by a natural cause), monitor (unclear, keep watching), warn (message the lake manager).
"""

import json
import logging
import os
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

import boto3

import buoys
import sources
import store

log = logging.getLogger()
log.setLevel(logging.INFO)

MODEL = os.environ.get("MODEL", "claude-haiku-5-5")
MAX_TURNS = 8
SNS = boto3.client("sns")
_key = None


def api_key():
    global _key
    if _key is None:
        _key = boto3.client("ssm").get_parameter(Name=os.environ["KEY_PARAM"], WithDecryption=True)["Parameter"]["Value"]
    return _key


SYSTEM = """You investigate water-quality anomalies for a network of four buoys in Lake Carnegie, Princeton NJ.

Context
- Lake Carnegie is a 5 km reservoir fed by the Millstone River and Stony Brook.
- Buoys 1-4 report temperature, microplastics, salinity and turbidity every few minutes. Buoy 1 is west, near the stream mouth; buoy 3 is the easternmost.
- The baseline is the USGS gauge on the Millstone River at Blackwells Mills, about 15 km downstream. The lake normally tracks it within a few degrees and a few NTU, so a river that moves with the lake means a regional cause.
- Several facilities hold Clean Water Act discharge permits within 5 miles, mostly east of the lake.

Your job
Decide whether an anomaly has a benign, natural explanation (heatwave, storm runoff, road salt after snow) or may be pollution or a danger that the lake manager must hear about now.
1. Gather evidence with the tools. Check at least: whether the other buoys show the same thing (lake-wide or localised), what the river gauge did, and the weather.
2. Weigh it. A natural cause should leave a consistent signature: lake-wide, matched by the river, and matched by the weather. A single hot or muddy buoy with a normal river and normal weather is suspicious, especially near a permitted discharger.
3. Call submit_decision exactly once.
   - benign: a natural cause clearly explains it.
   - monitor: no clear explanation, but no sign of harm yet.
   - warn: likely pollution, or conditions dangerous to wildlife or people, whatever the cause.
Before each set of tool calls, write one short sentence saying what you are checking and why.
Cite real numbers from the tools. Do not invent data. Be brief."""

TOOLS = [
    {
        "name": "buoy_network",
        "description": "Readings from all four buoys around the anomaly time: latest values and the range over the window. Shows whether the anomaly is lake-wide or local to one buoy.",
        "input_schema": {
            "type": "object",
            "properties": {"hours_before": {"type": "integer", "description": "Window before the anomaly, default 6"}},
        },
    },
    {
        "name": "river_gauge",
        "description": "USGS Millstone River gauge (the baseline): daily mean water temperature, specific conductance (salinity proxy) and turbidity for the days before the anomaly, plus Stony Brook flow when live.",
        "input_schema": {
            "type": "object",
            "properties": {"days_before": {"type": "integer", "description": "Days of history, default 7"}},
        },
    },
    {
        "name": "weather",
        "description": "Daily air temperature, rain and snow at the lake for the days before the anomaly, the 10-year normal high for that date, and any active National Weather Service alerts.",
        "input_schema": {
            "type": "object",
            "properties": {"days_before": {"type": "integer", "description": "Days of history, default 5"}},
        },
    },
    {
        "name": "dischargers",
        "description": "EPA ECHO list of facilities with Clean Water Act discharge permits within 5 miles, with their longitude relative to each buoy.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "satellite",
        "description": "Most recent clear Sentinel-2 scenes over the lake from the AWS Open Data registry (scene id, date, cloud cover, preview link).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_decision",
        "description": "Record the final verdict. Call exactly once, last.",
        "input_schema": {
            "type": "object",
            "required": ["verdict", "cause", "confidence", "summary", "evidence"],
            "properties": {
                "verdict": {"type": "string", "enum": ["benign", "monitor", "warn"]},
                "cause": {"type": "string", "description": "Most likely cause, a few words"},
                "confidence": {"type": "number", "description": "0 to 1"},
                "summary": {"type": "string", "description": "Two or three sentences for the dashboard"},
                "evidence": {"type": "array", "items": {"type": "string"}, "description": "Short facts with numbers and their source"},
                "alert_message": {"type": "string", "description": "Required when verdict is warn: a short text message to the lake manager saying what, where, and what to do"},
            },
        },
    },
]


class Case:
    def __init__(self, detail):
        self.reading = detail["reading"]
        self.anomaly = detail["anomaly"]
        self.baseline = detail["baseline"]
        self.when = datetime.fromisoformat(self.reading["time"])
        self.day = self.when.astimezone(timezone.utc).date()
        self.live = abs(datetime.now(timezone.utc) - self.when) < timedelta(hours=6)

    def buoy_network(self, hours_before=6):
        since = (self.when - timedelta(hours=hours_before)).isoformat(timespec="seconds")
        until = (self.when + timedelta(hours=1)).isoformat(timespec="seconds")
        run = self.reading.get("run")
        out = {}
        for b in buoys.BUOYS:
            rows = [r for r in store.readings(b, since) if r["time"] <= until and r.get("run") in (None, run)]
            if not rows:
                out[f"buoy {b}"] = "no readings in window"
                continue
            mine = [x for x in rows if run and x.get("run") == run]
            latest = (mine or rows)[-1]
            out[f"buoy {b}"] = {
                "latest": {m: latest[m] for m in buoys.METRICS},
                "range": {m: [min(r[m] for r in rows), max(r[m] for r in rows)] for m in buoys.METRICS},
                "readings": len(rows),
            }
        return out

    def river_gauge(self, days_before=7):
        start = (self.day - timedelta(days=days_before)).isoformat()
        out = {"site": "USGS 01402000 Millstone River at Blackwells Mills", "daily_means": sources.usgs_daily(sources.WQ_GAUGE, start, self.day.isoformat())}
        out["salinity_psu_from_conductance"] = [
            {"date": r["date"], "psu": sources.conductance_to_psu(r["mean"])}
            for r in out["daily_means"].get("specific_conductance_us_cm", [])
        ]
        if self.live:
            out["latest"] = sources.usgs_latest(sources.WQ_GAUGE)
            out["stony_brook_latest"] = sources.usgs_latest(sources.FLOW_GAUGE)
        return out

    def weather(self, days_before=5):
        start = (self.day - timedelta(days=days_before)).isoformat()
        out = {
            "daily": sources.weather_daily(start, self.day.isoformat()),
            "normal_high_c_for_date": sources.air_climatology(self.day.isoformat()),
        }
        out["nws_alerts"] = sources.nws_alerts() if self.live else "historical date: NWS only keeps alerts for ~7 days"
        return out

    def dischargers(self):
        facs = sources.dischargers()
        for f in facs:
            if f["longitude"] is not None:
                # ~85 m per 0.001° of longitude at this latitude
                f["km_east_of_buoy"] = {b: round((f["longitude"] - p["lon"]) * 85, 1) for b, p in buoys.BUOYS.items()}
        return facs

    def satellite(self):
        return sources.sentinel2_scenes(self.day.isoformat())


def call_claude(messages):
    body = {
        "model": MODEL,
        "max_tokens": 4000,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "medium"},
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "tools": TOOLS,
        "messages": messages,
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"x-api-key": api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


class Run:
    """Writes each step to DynamoDB as it happens, so the dashboard can follow the agent live."""

    def __init__(self, detail):
        r, a = detail["reading"], detail["anomaly"]
        self.started = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self.id = f"{self.started[:19].replace(':', '').replace('-', '')}-b{r['buoy']}-{a['metric']}"
        self.n = 0
        self.meta = {
            "id": self.id,
            "started": self.started,
            "status": "running",
            "buoy": r["buoy"],
            "metric": a["metric"],
            "value": a["value"],
            "unit": a.get("unit"),
            "reason": a["reason"],
            "reading_time": r["time"],
            "source": (r.get("run") or "live").split("-")[0],
            "model": MODEL,
        }
        store.save_run(self.meta)

    def step(self, kind, **data):
        store.add_step(self.id, self.n, {"type": kind, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **data})
        self.n += 1

    def finish(self, **data):
        self.meta.update(status="done", finished=datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **data)
        store.save_run(self.meta)


def investigate(detail, run):
    case = Case(detail)
    a, r = case.anomaly, case.reading
    opening = (
        f"Anomaly at buoy {r['buoy']}, {case.when.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}: {a['reason']}.\n"
        f"Full reading: {json.dumps({m: r[m] for m in buoys.METRICS})}\n"
        f"Baseline at detection: {json.dumps(case.baseline.get('usgs', {}))}\n"
        "Investigate and decide."
    )
    messages = [{"role": "user", "content": opening}]
    trace, usage = [], {"input": 0, "output": 0}
    decision = None

    for _ in range(MAX_TURNS):
        run.step("model", status="thinking")
        res = call_claude(messages)
        usage["input"] += res["usage"]["input_tokens"] + res["usage"].get("cache_read_input_tokens", 0)
        usage["output"] += res["usage"]["output_tokens"]
        messages.append({"role": "assistant", "content": res["content"]})
        for c in res["content"]:
            if c["type"] == "thinking" and c.get("thinking"):
                run.step("thinking", text=c["thinking"])
            elif c["type"] == "text" and c["text"].strip():
                run.step("text", text=c["text"].strip())
        calls = [c for c in res["content"] if c["type"] == "tool_use"]
        if not calls:
            break
        results = []
        for c in calls:
            if c["name"] == "submit_decision":
                decision = c["input"]
                results.append({"type": "tool_result", "tool_use_id": c["id"], "content": "recorded"})
                continue
            run.step("tool_call", tool=c["name"], input=c["input"], call_id=c["id"])
            t0 = time.time()
            try:
                out, ok = getattr(case, c["name"])(**c["input"]), True
            except Exception as e:  # a failing source is evidence too; let the model work around it
                out, ok = {"error": str(e)}, False
            ms = int((time.time() - t0) * 1000)
            text = json.dumps(out, default=str)[:6000]
            shown = json.loads(text) if len(text) < 6000 else text
            run.step("tool_result", tool=c["name"], call_id=c["id"], ok=ok, ms=ms, output=shown)
            trace.append({"tool": c["name"], "input": c["input"], "ms": ms, "output": shown})
            results.append({"type": "tool_result", "tool_use_id": c["id"], "content": text})
        messages.append({"role": "user", "content": results})
        if decision:
            break

    log.info("raw decision %s", json.dumps(decision))
    d = {"verdict": "monitor", "cause": "undetermined", "confidence": 0, "summary": "The agent did not reach a decision.", "evidence": [], **(decision or {})}
    if isinstance(d["evidence"], str):
        d["evidence"] = [d["evidence"]]
    return d, trace, usage


def handler(event, context):
    detail = event["detail"]
    r, a = detail["reading"], detail["anomaly"]
    run = Run(detail)
    run.step("trigger", buoy=r["buoy"], metric=a["metric"], value=a["value"], unit=a.get("unit"), reason=a["reason"], reading={m: r[m] for m in buoys.METRICS})
    time.sleep(4)  # let the rest of the fleet's readings for this moment land
    try:
        decision, trace, usage = investigate(detail, run)
    except Exception as e:
        run.step("error", text=str(e))
        run.finish(verdict="error", cause=str(e)[:200])
        raise
    inc = {
        "id": f"{r['time']}#{r['buoy']}#{a['metric']}",
        "run_id": run.id,
        "detected_at": r["time"],
        "investigated_at": run.started,
        "buoy": r["buoy"],
        "metric": a["metric"],
        "value": a["value"],
        "reason": a["reason"],
        "run": r.get("run"),
        **decision,
        "tools_used": [t["tool"] for t in trace],
        "tokens": usage,
        "model": MODEL,
    }
    if decision["verdict"] == "warn":
        SNS.publish(
            TopicArn=os.environ["ALERT_TOPIC"],
            Subject=f"Lake Carnegie alert: buoy {r['buoy']} {a['metric']}"[:100],
            Message=f"{decision.get('alert_message') or decision['summary']}\n\nCause: {decision['cause']} (confidence {decision['confidence']})\n\nEvidence:\n- "
            + "\n- ".join(decision["evidence"]),
        )
        inc["alerted"] = True
    run.step("decision", **decision, alerted=inc.get("alerted", False), tokens=usage)
    run.finish(verdict=decision["verdict"], cause=decision["cause"], confidence=decision["confidence"], alerted=inc.get("alerted", False), tokens=usage)
    store.save_incident(inc)
    store.put_json(f"incidents/{run.id}.json", {**inc, "detail": detail, "trace": trace})
    log.info("verdict %s", json.dumps(inc, default=str))
    return inc


def _steward(path, body):
    req = urllib.request.Request(
        os.environ["STEWARD_URL"].rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-api-key": os.environ.get("STEWARD_KEY", "")},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def open_case(reading, anomaly):
    """Hand an anomaly to the Lake Steward deep agent on the Agent Server."""
    case_id = f"{reading['time'][:19].replace(':', '').replace('-', '')}-b{reading['buoy']}-{anomaly['metric']}"
    case = {
        "case_id": case_id,
        "buoy": reading["buoy"],
        "metric": anomaly["metric"],
        "value": anomaly["value"],
        "unit": anomaly.get("unit", ""),
        "reason": anomaly["reason"],
        "reading_time": reading["time"],
        "run": reading.get("run"),
    }
    thread = _steward("/threads", {"metadata": {"kind": "case", **{k: v for k, v in case.items() if v is not None}}})
    _steward(
        f"/threads/{thread['thread_id']}/runs",
        {
            "assistant_id": "steward",
            "input": {"messages": [{"role": "user", "content": (
                f"New anomaly. Buoy {case['buoy']}, {case['metric']} = {case['value']} {case['unit']} "
                f"at {case['reading_time']}. {case['reason']}. Investigate and decide.")}]},
            "context": case,
        },
    )
    return {"case_id": case_id, "thread_id": thread["thread_id"]}


CASE_LIMIT = int(os.environ.get("CASE_LIMIT", "40"))


def dispatch(event, context):
    """EventBridge target: route the anomaly to the deep agent, or the single-agent fallback."""
    detail = event["detail"]
    if os.environ.get("STEWARD_URL"):
        # Hard ceiling on investigations so model spend can't run away
        if _steward("/threads/count", {"metadata": {"kind": "case"}}) >= CASE_LIMIT:
            log.warning("case limit %d reached; anomaly not investigated", CASE_LIMIT)
            return {"skipped": "case limit"}
        res = open_case(detail["reading"], detail["anomaly"])
        log.info("opened case %s", json.dumps(res))
        return res
    if os.environ.get("LEGACY_AGENT") == "true":
        return handler(event, context)
    log.info("no agent configured; anomaly not investigated")
    return {"skipped": True}
