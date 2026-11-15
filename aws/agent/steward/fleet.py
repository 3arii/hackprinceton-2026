"""Buoy readings and past incidents, from the pipeline's DynamoDB table."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from boto3.dynamodb.conditions import Key
import boto3

from steward.config import BUOYS, REGION, TABLE

METRICS = ("temperature", "microplastics", "salinity", "turbidity")
_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    return _table


def plain(obj):
    if isinstance(obj, list):
        return [plain(v) for v in obj]
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


def _readings(buoy: int, since: str, until: str) -> list[dict]:
    res = table().query(KeyConditionExpression=Key("pk").eq(f"BUOY#{buoy}") & Key("sk").between(f"R#{since}", f"R#{until}"))
    return [plain(i) for i in res["Items"]]


async def network(at: str, hours_before: float = 6, run: str | None = None) -> dict:
    """Every buoy's readings in a window ending one hour after `at`: latest values, range and trend."""
    when = datetime.fromisoformat(at)
    since = (when - timedelta(hours=hours_before)).isoformat(timespec="seconds")
    until = (when + timedelta(hours=1)).isoformat(timespec="seconds")
    rows = await asyncio.gather(*(asyncio.to_thread(_readings, b, since, until) for b in BUOYS))
    out = {}
    for b, rs in zip(BUOYS, rows):
        # Live readings, plus this demo run's own readings
        rs = [r for r in rs if r.get("run") in (None, run)]
        if not rs:
            out[f"buoy_{b}"] = "no readings in window"
            continue
        mine = [r for r in rs if run and r.get("run") == run]
        latest = (mine or rs)[-1]
        # A hardware buoy reports None for sensors that failed or aren't wired yet
        vals = {m: [r[m] for r in rs if r.get(m) is not None] for m in METRICS}
        out[f"buoy_{b}"] = {
            "latest": {m: latest.get(m) for m in METRICS},
            "latest_time": latest["time"],
            "range": {m: [min(v), max(v)] if v else None for m, v in vals.items()},
            "first_in_window": {m: v[0] if v else None for m, v in vals.items()},
            "readings": len(rs),
        }
        if latest.get("source") == "hardware":
            out[f"buoy_{b}"]["hardware"] = {
                "node": latest.get("node"),
                "mesh_level": latest.get("level"),  # 1 talks to the gateway, 2 is relayed through another buoy
                "rssi": latest.get("rssi"),
                "temp_samples_last_minute": latest.get("temp_samples"),
                # How microplastics was estimated: particles caught on the mesh, litres filtered, 95% interval
                "camera": latest.get("camera"),
                "note": (
                    "temperature is a sensor; microplastics is estimated from the mesh camera (particles caught "
                    "over water filtered; camera.measured false means the flow rate is a guess, so treat it as "
                    "rough), and is null while the camera is off or blocked; salinity and turbidity aren't connected yet"
                ),
            }
    return out


def _incidents(limit: int) -> list[dict]:
    res = table().query(KeyConditionExpression=Key("pk").eq("INCIDENT"), ScanIndexForward=False, Limit=limit)
    return [plain(i) for i in res["Items"]]


async def incidents(limit: int = 25) -> list[dict]:
    keep = ("detected_at", "buoy", "metric", "value", "verdict", "cause", "confidence", "summary", "run")
    return [{k: i.get(k) for k in keep} for i in await asyncio.to_thread(_incidents, limit)]


def _put(item: dict) -> None:
    table().put_item(Item=item)


async def save(item: dict) -> None:
    import json

    await asyncio.to_thread(_put, json.loads(json.dumps(item, default=str), parse_float=Decimal))
