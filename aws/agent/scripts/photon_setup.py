"""One-time Photon setup: give the manager's phone a shared iMessage line and point Photon's
webhook at the dashboard. Prints the agent's number, which the manager must text once.

    AWS_PROFILE=hackprinceton uv run python scripts/photon_setup.py https://3-151-189-120.sslip.io
"""

import asyncio
import json
import sys

from steward import photon
from steward.config import aws


async def main(site: str) -> None:
    c = photon.config()
    phone = c["manager"]

    line = await photon.agent_line(phone)
    if not line:
        a = await photon.call("POST", "/platforms/imessage/assignments", {"handle": phone}, idempotency=f"assign:{phone}")
        line = a["assignedPhoneNumber"]
    print(f"manager {phone} -> agent line {line}")

    url = f"{site.rstrip('/')}/api/photon/webhook"
    existing = await photon.call("GET", "/webhooks/destinations")
    if any(d.get("url") == url for d in existing["destinations"]):
        print("webhook already registered:", url)
        return
    versions = await photon.call("GET", "/webhooks/api-versions")
    version = sorted(v["version"] for v in versions["apiVersions"] if v.get("selectable"))[-1]
    types = await photon.call("GET", f"/webhooks/event-types?apiVersion={version}")
    names = [t["type"] for t in types["events"]]
    events = [n for n in names if n.startswith("message.") and n.endswith("received")] or ["*"]
    d = await photon.call(
        "POST",
        "/webhooks/destinations",
        {"name": "lake-steward", "url": url, "apiVersion": version, "enabledEvents": events, "status": "enabled"},
        idempotency=f"webhook:{url}",
    )
    secret = d.get("secret") or d.get("signingSecret")
    aws("ssm").put_parameter(Name="/buoy/photon-webhook-secret", Value=secret, Type="SecureString", Overwrite=True)
    print("webhook registered:", url, "events:", json.dumps(events), "(secret saved to SSM)")


asyncio.run(main(sys.argv[1]))
