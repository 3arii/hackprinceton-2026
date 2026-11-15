#!/usr/bin/env python3
"""Replay a recorded stretch of buoy readings as if it were live, through the same event detector.

A safety net for demos: record a real event once, then play it back into its own folder and point the viewer
at that folder. The live collector and its files are never touched.

    python3 replay.py --clip clip.jsonl --output ~/buoy-replay              # real time, loops
    python3 serve_gateway.py --images ~/buoy-replay --port 8001 --host 0.0.0.0
    python3 replay.py --cut ~/flotsam-images/readings.jsonl --from 1794690000 --to 1794690300 --clip clip.jsonl
"""

import argparse
import json
import time
from pathlib import Path

from events import SIGNALS, EventDetector, describe
from receive_gateway import READINGS_FILE, save_reading


def load(path):
    rows = []
    for raw in Path(path).read_bytes().splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and "node" in row and "received_at" in row:
            rows.append(row)
    return rows


def cut(source, start, end, clip):
    rows = [r for r in load(source) if start <= r["received_at"] <= end]
    Path(clip).write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
    print(f"{len(rows)} readings from {len(set(r['node'] for r in rows))} buoy(s) -> {clip}")


def replay(clip, output, speed, loop, kinds):
    rows = load(clip)
    if not rows:
        raise SystemExit(f"No readings in {clip}")
    output = Path(output).expanduser()
    output.mkdir(parents=True, exist_ok=True)
    (output / READINGS_FILE).unlink(missing_ok=True)
    (output / "events.jsonl").unlink(missing_ok=True)
    detector = EventDetector(output, kinds)
    seq = {}
    span = rows[-1]["received_at"] - rows[0]["received_at"] + 5
    rounds = 0
    while True:
        t0, first = time.time(), rows[0]["received_at"]
        for row in rows:
            due = t0 + (row["received_at"] - first) / speed
            time.sleep(max(0.0, due - time.time()))
            reading = dict(row)
            seq[row["node"]] = seq.get(row["node"], 0) + 1
            reading["seq"] = seq[row["node"]]
            reading["received_at"] = round(time.time(), 3)
            save_reading(output, reading)
            for event in detector.feed(reading):
                print(describe(event), flush=True)
        rounds += 1
        if not loop:
            return
        print(f"replay round {rounds} done ({span / speed:.0f} s); starting again", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clip", type=Path, help="recorded readings (JSON lines)")
    parser.add_argument("--output", type=Path, default=Path("~/buoy-replay"))
    parser.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast")
    parser.add_argument("--once", action="store_true", help="play the clip once instead of looping")
    parser.add_argument("--events", default="temperature", help="comma list of " + ", ".join(SIGNALS))
    parser.add_argument("--cut", type=Path, help="make a clip: readings.jsonl to cut from (use with --from/--to/--clip)")
    parser.add_argument("--from", dest="start", type=float, help="Unix time where the clip starts")
    parser.add_argument("--to", dest="end", type=float, help="Unix time where the clip ends")
    args = parser.parse_args(argv)
    if args.cut:
        if args.start is None or args.end is None or not args.clip:
            parser.error("--cut needs --from, --to and --clip")
        return cut(args.cut, args.start, args.end, args.clip)
    if not args.clip:
        parser.error("--clip is required")
    replay(args.clip, args.output, args.speed, not args.once, [k for k in args.events.split(",") if k])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
