"""iMessage through Photon 2.0 (https://api.photon.codes/v1).

The lake manager's phone is assigned a shared Photon iMessage line. A shared line can't
send the first message, so the manager texts it once; after that the steward can alert them
and they can text back to ask why.
"""

import asyncio
import os
import uuid

import httpx

from steward.config import aws

API = "https://api.photon.codes/v1"
_cfg: dict | None = None
_http: httpx.AsyncClient | None = None


def _param(name: str, secret: bool = False) -> str:
    return aws("ssm").get_parameter(Name=name, WithDecryption=secret)["Parameter"]["Value"]


def config() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = {
            "key": os.environ.get("PHOTON_API_KEY") or _param("/buoy/photon-api-key", True),
            "project": os.environ.get("PHOTON_PROJECT_ID") or _param("/buoy/photon-project"),
            "manager": os.environ.get("ALERT_PHONE") or _param("/buoy/alert-phone"),
        }
    return _cfg


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=API, timeout=20)
    return _http


async def call(method: str, path: str, body: dict | None = None, idempotency: str | None = None) -> dict:
    c = await asyncio.to_thread(config)
    headers = {"Authorization": f"Bearer {c['key']}"}
    if idempotency:
        headers["Idempotency-Key"] = idempotency
    r = await http().request(method, f"/projects/{c['project']}{path}", json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"Photon {method} {path}: {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


async def agent_line(handle: str) -> str | None:
    """The shared iMessage number Photon assigned to this person, if any."""
    res = await call("GET", "/platforms/imessage/assignments")
    for a in res.get("assignments", []):
        if a.get("handle") == handle and a.get("assignedPhoneNumber") and not a.get("releasedAt"):
            return a["assignedPhoneNumber"]
    return None


async def send_text(text: str, to: str | None = None, space_id: str | None = None, sender_id: str | None = None,
                    reply_to: str | None = None, key: str | None = None) -> dict:
    """Send a plain-text iMessage. Pass space and sender from an incoming message to reply in that thread."""
    if not (space_id and sender_id):
        to = to or (await asyncio.to_thread(config))["manager"]
        line = await agent_line(to)
        if not line:
            raise RuntimeError(f"No Photon iMessage line is assigned to {to}")
        agent = await call("POST", "/users", {"platform": "imessage", "handle": line})
        person = await call("POST", "/users", {"platform": "imessage", "handle": to})
        space = await call(
            "POST", "/spaces", {"platform": "imessage", "participantIds": [agent["id"], person["id"]]},
            idempotency=f"space:{agent['id']}:{person['id']}",
        )
        space_id, sender_id = space["id"], agent["id"]
    body = {"spaceId": space_id, "senderId": sender_id, "content": {"type": "text", "format": "plain", "text": text}}
    if reply_to:
        body["parentMessageId"] = reply_to
    return await call("POST", "/messages", body, idempotency=key or f"lake-steward:{uuid.uuid4()}")
