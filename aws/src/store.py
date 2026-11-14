"""DynamoDB single-table layout and S3 helpers.

pk               sk                         item
BASELINE         latest                     newest gauge + weather snapshot
BUOY#<n>         R#<iso time>               one buoy reading (expires after 7 days)
INCIDENT         <iso time>#<buoy>#<metric> an investigated anomaly and its verdict
LOCK#<n>#<m>     lock                       cooldown so one anomaly isn't investigated twice
"""

import json
import os
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE"])
S3 = boto3.client("s3")
BUCKET = os.environ["BUCKET"]

WEEK = 7 * 24 * 3600


def to_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_ddb(obj):
    if isinstance(obj, list):
        return [from_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


def put_json(key, obj):
    S3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj, default=str).encode(), ContentType="application/json")


def save_baseline(snapshot):
    TABLE.put_item(Item=to_ddb({"pk": "BASELINE", "sk": "latest", **snapshot}))


def baseline():
    return from_ddb(TABLE.get_item(Key={"pk": "BASELINE", "sk": "latest"}).get("Item") or {})


def save_reading(r):
    TABLE.put_item(Item=to_ddb({"pk": f"BUOY#{r['buoy']}", "sk": f"R#{r['time']}", "expires": int(time.time()) + WEEK, **r}))


def readings(buoy, since_iso, limit=200):
    res = TABLE.query(
        KeyConditionExpression=Key("pk").eq(f"BUOY#{buoy}") & Key("sk").gte(f"R#{since_iso}"),
        Limit=limit,
    )
    return [from_ddb(i) for i in res["Items"]]


def latest_reading(buoy):
    res = TABLE.query(KeyConditionExpression=Key("pk").eq(f"BUOY#{buoy}"), ScanIndexForward=False, Limit=1)
    return from_ddb(res["Items"][0]) if res["Items"] else None


def claim_lock(buoy, metric, seconds):
    """True if no investigation for this buoy+metric ran in the last `seconds`."""
    now = int(time.time())
    try:
        TABLE.put_item(
            Item={"pk": f"LOCK#{buoy}#{metric}", "sk": "lock", "expires": now + seconds},
            ConditionExpression="attribute_not_exists(pk) OR expires < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except TABLE.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def save_incident(inc):
    TABLE.put_item(Item=to_ddb({"pk": "INCIDENT", "sk": f"{inc['detected_at']}#{inc['buoy']}#{inc['metric']}", **inc}))


def incidents(limit=20):
    res = TABLE.query(KeyConditionExpression=Key("pk").eq("INCIDENT"), ScanIndexForward=False, Limit=limit)
    return [from_ddb(i) for i in res["Items"]]


# Agent runs, written step by step so the dashboard can follow along live
# RUNS       <started>#<id>   run summary (status, trigger, verdict)
# RUN#<id>   <step number>    one step: trigger, thinking, text, tool_call, tool_result, decision


def save_run(run):
    TABLE.put_item(Item=to_ddb({"pk": "RUNS", "sk": f"{run['started']}#{run['id']}", "expires": int(time.time()) + WEEK, **run}))


def add_step(run_id, n, step):
    TABLE.put_item(Item=to_ddb({"pk": f"RUN#{run_id}", "sk": f"{n:04d}", "expires": int(time.time()) + WEEK, "n": n, **step}))


def runs(limit=10):
    res = TABLE.query(KeyConditionExpression=Key("pk").eq("RUNS"), ScanIndexForward=False, Limit=limit)
    return [from_ddb(i) for i in res["Items"]]


def run_steps(run_id, after=-1):
    res = TABLE.query(KeyConditionExpression=Key("pk").eq(f"RUN#{run_id}") & Key("sk").gt(f"{after:04d}"))
    return [from_ddb(i) for i in res["Items"]]
