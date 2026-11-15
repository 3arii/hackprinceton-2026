"""Lambda entry points for the data pipeline. The agent lives in agent.py."""

import base64
import hmac
import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import boto3

import baseline
import buoys
import store

log = logging.getLogger()
log.setLevel(logging.INFO)

EVENTS = boto3.client("events")
LOCK_SECONDS = int(os.environ.get("LOCK_SECONDS", "3600"))
# Hardware buoys: mesh node id -> buoy number. The simulator stops speaking for these buoys.
NODE_BUOYS = {k: int(v) for k, v in json.loads(os.environ.get("NODE_BUOYS") or "{}").items()}
# Until the hardware is in the lake, its anomalies are logged but don't open investigations
HARDWARE_ALERTS = os.environ.get("HARDWARE_ALERTS") == "true"
_iot = None


def iot():
    global _iot
    if _iot is None:
        host = boto3.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        _iot = boto3.client("iot-data", endpoint_url=f"https://{host}")
    return _iot


def publish(r):
    iot().publish(topic=f"buoys/{r['buoy']}/readings", qos=1, payload=json.dumps(r))


def ingest(event, context):
    """Every 15 minutes: pull the river gauges, weather and alerts into S3 and the baseline record."""
    snap = baseline.snapshot()
    now = datetime.now(timezone.utc)
    store.put_json(f"raw/gauges/{now:%Y/%m/%d/%H%M}.json", snap)
    store.save_baseline(snap)
    log.info("baseline %s", json.dumps(snap["usgs"]))
    return {"ok": True, "time": snap["time"]}


def simulate(event, context):
    """Every 5 minutes: the simulated fleet reports in over MQTT, exactly as hardware would."""
    simulated = [b for b in buoys.BUOYS if b not in NODE_BUOYS.values()]
    for b in simulated:
        publish(buoys.reading(b))
    return {"published": len(simulated)}


def on_reading(event, context):
    """IoT rule target: store the reading, compare it with the baseline, raise anomalies."""
    r = {k: event[k] for k in ("buoy", "time", *buoys.METRICS)}
    r["buoy"] = int(r["buoy"])
    if event.get("run"):
        r["run"] = event["run"]
    return evaluate(r)


def evaluate(r, alert=True):
    """Store a reading, compare it with the baseline, and raise its anomalies (or only log them)."""
    store.save_reading(r)
    when = datetime.fromisoformat(r["time"])
    base = baseline.for_time(when, store.baseline())
    found = buoys.check(r, base)
    if not alert:
        if found:
            log.info("anomaly not raised (hardware alerts off) buoy=%s %s", r["buoy"], json.dumps(found, default=str))
        return {"stored": True, "raised": [], "held": [a["metric"] for a in found]}
    raised = []
    for a in found:
        # One investigation per metric across the whole network, per live hour or per demo run
        if not store.claim_lock("NET", f"{a['metric']}#{r.get('run', 'live')}", LOCK_SECONDS):
            continue
        detail = {"reading": r, "anomaly": a, "baseline": base}
        EVENTS.put_events(
            Entries=[{"Source": "buoy.monitor", "DetailType": "AnomalyDetected", "Detail": json.dumps(detail, default=str)}]
        )
        raised.append(a["metric"])
    if raised:
        log.info("anomaly buoy=%s metrics=%s", r["buoy"], raised)
    return {"stored": True, "raised": raised}


def on_upload(event, context):
    """S3 Object Created under readings/ (via EventBridge): the Pi gateway's minute of samples for one
    node, as JSON lines (node, seq, uptime_ms, temp_c, light_mv, ambient_mv, cond_mv, rssi, level,
    received_at). Re-sent samples are dropped; each node's new samples become one buoy reading."""
    key = event["detail"]["object"]["key"]
    body = store.S3.get_object(Bucket=store.BUCKET, Key=key)["Body"].read().decode()
    by_node = defaultdict(list)
    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            s = json.loads(line)
        except json.JSONDecodeError:
            log.warning("bad line in %s: %.120s", key, line)
            continue
        if s.get("node") and s.get("seq") is not None and store.claim_sample(s["node"], s["seq"], s.get("uptime_ms")):
            by_node[s["node"]].append(s)
    out = {}
    for node, samples in by_node.items():
        if node not in NODE_BUOYS:
            log.warning("node %s isn't mapped to a buoy (NODE_BUOYS); %d samples from %s ignored", node, len(samples), key)
            continue
        r = buoys.from_samples(NODE_BUOYS[node], node, samples, camera=store.camera_estimate(node))
        out[node] = {"buoy": r["buoy"], "temperature": r["temperature"], **evaluate(r, alert=HARDWARE_ALERTS)}
    log.info("upload %s %s", key, json.dumps(out))
    return out


def _json(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


_debug_token = None


def debug_token():
    global _debug_token
    if _debug_token is None:
        _debug_token = boto3.client("ssm").get_parameter(Name=os.environ["DEBUG_TOKEN_PARAM"], WithDecryption=True)["Parameter"]["Value"]
    return _debug_token


def debug_reading(event):
    """Publish a hand-edited reading for one buoy through IoT Core, like real hardware would."""
    if not hmac.compare_digest(event.get("headers", {}).get("x-debug-token", ""), debug_token()):
        return _json(403, {"error": "forbidden"})
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    body = json.loads(raw)
    b = int(body.get("buoy", 0))
    if b not in buoys.BUOYS:
        return _json(400, {"error": "unknown buoy"})
    values = {}
    for m in buoys.METRICS:
        if m in body.get("values", {}):
            v = float(body["values"][m])
            if not 0 <= v <= 1000:
                return _json(400, {"error": f"{m} out of range"})
            values[m] = v
    r = buoys.reading(b, **values, run=f"debug-{uuid.uuid4().hex[:6]}")
    publish(r)
    # Same rules the IoT path applies, returned so the dashboard can open a case right away
    base = baseline.for_time(datetime.fromisoformat(r["time"]), store.baseline())
    return _json(200, {"reading": r, "anomalies": buoys.check(r, base), "baseline": base.get("usgs", {})})


def api(event, context):
    """Dashboard API (Lambda function URL). Reads are open; the debug write needs a token."""
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/").rstrip("/") or "/"
    q = event.get("queryStringParameters") or {}
    if method == "POST" and path == "/debug/reading":
        return debug_reading(event)
    if method != "GET":
        return _json(405, {"error": "method not allowed"})
    if path == "/incidents":
        return _json(200, store.incidents())
    if path == "/baseline":
        return _json(200, store.baseline())
    if path == "/runs":
        return _json(200, store.runs(limit=min(int(q.get("limit", 10)), 30)))
    if path == "/camera":  # microplastics per litre from each camera
        return _json(200, [{"buoy": b, **e} for node, b in NODE_BUOYS.items() if (e := store.camera_estimate(node))])
    if path == "/photos":  # camera photo labels, newest first per camera
        nodes = [q["node"]] if q.get("node") else list(NODE_BUOYS)
        limit = min(int(q.get("limit", 200)), 500)
        return _json(200, [p for node in nodes for p in store.photos(node, limit)])
    if path.startswith("/runs/"):
        run_id = path.split("/", 2)[2]
        return _json(200, {"steps": store.run_steps(run_id, int(q.get("after", -1)))})
    return _json(
        200,
        {
            "baseline": store.baseline(),
            "buoys": [store.latest_reading(b) for b in buoys.BUOYS],
            "incidents": store.incidents(limit=10),
        },
    )
