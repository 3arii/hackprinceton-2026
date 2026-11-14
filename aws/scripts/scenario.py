"""Push a demo scenario through IoT Core: all four buoys report one moment, one of them anomalous.

    uv run --with boto3 --with "botocore[crt]" python aws/scripts/scenario.py heatwave|runoff|discharge
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("TZ", "UTC")
from buoys import BUOYS, SCENARIOS, reading  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else ""
if name not in SCENARIOS:
    sys.exit(f"usage: scenario.py {'|'.join(SCENARIOS)}")

session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "hackprinceton"), region_name="us-east-2")
host = session.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
iot = session.client("iot-data", endpoint_url=f"https://{host}")

when, overrides = SCENARIOS[name]
when = when or datetime.now(timezone.utc)
run = f"{name}-{uuid.uuid4().hex[:6]}"
for b in BUOYS:
    r = reading(b, when, **overrides.get(b, {}), run=run)
    iot.publish(topic=f"buoys/{b}/readings", qos=1, payload=json.dumps(r))
    print(json.dumps(r))
print(f"\npublished run {run}; the verdict lands in DynamoDB in ~30 s")
