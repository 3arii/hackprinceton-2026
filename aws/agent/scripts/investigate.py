"""Open an investigation on the Agent Server and follow the steward and its specialists live.

    uv run python scripts/investigate.py --buoy 3 --metric temperature --value 19.6 \
        --reason "19.6 °C is 8.6 °C warmer than the Millstone River (11 °C)" \
        --time 2026-11-14T04:57:37+00:00 --run discharge-435d0e
"""

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from langgraph_sdk import get_client

from deepagents.backends.utils import create_file_data

SEED = Path(__file__).parent.parent / "steward" / "seed" / "AGENTS.md"


async def seed_memory(client) -> None:
    item = await client.store.get_item(["steward", "memories"], "/AGENTS.md")
    if not item:
        await client.store.put_item(["steward", "memories"], "/AGENTS.md", create_file_data(SEED.read_text()))
        print("seeded /memories/AGENTS.md")


def show(prefix: str, msg: dict) -> None:
    kind = msg.get("type")
    content = msg.get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
    for b in blocks:
        if b.get("type") == "thinking" and b.get("thinking"):
            print(f"{prefix} 💭 {b['thinking'][:300]}")
        elif b.get("type") == "text" and b.get("text", "").strip():
            label = "👤" if kind == "human" else "🔧" if kind == "tool" else "🤖"
            print(f"{prefix} {label} {b['text'][:400]}")
    for tc in msg.get("tool_calls") or []:
        print(f"{prefix} → {tc['name']}({json.dumps(tc['args'])[:200]})")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=os.environ.get("STEWARD_URL", "http://localhost:2024"))
    p.add_argument("--buoy", type=int, required=True)
    p.add_argument("--metric", required=True)
    p.add_argument("--value", type=float, required=True)
    p.add_argument("--unit", default="")
    p.add_argument("--reason", required=True)
    p.add_argument("--time", required=True)
    p.add_argument("--run", default=None)
    a = p.parse_args()

    # The SDK sends api_key as X-Api-Key, which is what the host's proxy checks
    client = get_client(url=a.url, api_key=os.environ.get("STEWARD_KEY"))
    await seed_memory(client)

    case_id = f"{a.time[:19].replace(':', '').replace('-', '')}-b{a.buoy}-{a.metric}-{uuid.uuid4().hex[:4]}"
    case = {"case_id": case_id, "buoy": a.buoy, "metric": a.metric, "value": a.value, "unit": a.unit,
            "reason": a.reason, "reading_time": a.time, "run": a.run}
    thread = await client.threads.create(metadata={"kind": "case", **{k: v for k, v in case.items() if v is not None}})
    tid = thread["thread_id"]
    print(f"case {case_id}  thread {tid}")
    await client.runs.create(
        tid,
        "steward",
        input={"messages": [{"role": "user", "content": (
            f"New anomaly. Buoy {a.buoy}, {a.metric} = {a.value} {a.unit} at {a.time}. {a.reason}. "
            "Investigate and decide.")}]},
        context=case,
    )

    seen: dict[str, int] = {}
    idle_polls = 0
    while True:
        await asyncio.sleep(2)
        threads = [(tid, "steward")] + [
            (t["thread_id"], t["metadata"].get("subagent_name", "?"))
            for t in await client.threads.search(metadata={"parent_thread_id": tid}, limit=20)
        ]
        busy = False
        for t_id, name in threads:
            state = await client.threads.get_state(t_id)
            msgs = (state.get("values") or {}).get("messages") or []
            for m in msgs[seen.get(t_id, 0):]:
                show(f"[{name:9}]", m)
            seen[t_id] = len(msgs)
            busy |= (await client.threads.get(t_id))["status"] == "busy"
        idle_polls = 0 if busy else idle_polls + 1
        if idle_polls >= 4:  # nothing running for ~8 s, and no wake-up queued
            break

    state = await client.threads.get_state(tid)
    print("\ntodos:", json.dumps(state["values"].get("todos"), indent=1)[:800])
    files = await client.store.search_items(["case", case_id], limit=20)
    print("case files:", [i["key"] for i in files["items"]])


asyncio.run(main())
