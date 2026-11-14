"""Settings shared by every graph in the deployment."""

import os
from functools import cache

import boto3
from langchain_anthropic import ChatAnthropic

REGION = os.environ.get("AWS_REGION", "us-east-2")
TABLE = os.environ.get("BUOY_TABLE", "")
BUCKET = os.environ.get("BUOY_BUCKET", "")
ALERT_TOPIC = os.environ.get("ALERT_TOPIC", "")

SUPERVISOR_MODEL = os.environ.get("STEWARD_MODEL", "claude-sonnet-5-5")
WORKER_MODEL = os.environ.get("WORKER_MODEL", "claude-haiku-5-5")

# Lake Carnegie, Princeton NJ
LAKE = {"lat": 40.343, "lon": -74.640}
LAKE_BBOX = (-74.675, 40.322, -74.612, 40.356)  # west, south, east, north
BUOYS = {
    1: {"lon": -74.6413, "lat": 40.3433},
    2: {"lon": -74.6398, "lat": 40.34338},
    3: {"lon": -74.6389, "lat": 40.34295},
    4: {"lon": -74.6409, "lat": 40.34262},
}
USER_AGENT = "lake-steward/0.1 (HackPrinceton; denizlapsekili@gmail.com)"


@cache
def aws(service: str):
    return boto3.client(service, region_name=REGION)


def supervisor_model():
    # Summarized adaptive thinking so the dashboard can show how it weighs evidence
    return ChatAnthropic(
        model=SUPERVISOR_MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": "medium"},
        max_retries=3,
    )


def worker_model():
    return ChatAnthropic(model=WORKER_MODEL, max_tokens=4000, output_config={"effort": "low"}, max_retries=3)
