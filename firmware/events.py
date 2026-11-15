#!/usr/bin/env python3
"""Live water events on the shore Pi: flag a sudden change at a buoy within seconds.

Each buoy gets a rolling baseline per signal (median of its last few minutes of readings) and a noise
estimate (median absolute deviation). A reading that sits more than `threshold` away from the baseline for
`confirm` readings in a row opens an event; the baseline is frozen while the event is open so the new water
is not learned as normal; `clear` readings back inside half the threshold close it.

Signals (fields from FLOTSAM_READING lines):
  temperature  temp_c                  on by default; threshold max(0.8 C, 6 x noise)
  turbidity    light_mv - ambient_mv   off until the turbidity LED is wired; max(80 mV, 6 x noise)
  conductivity cond_mv                 off until the electrodes are wired; max(60 mV, 6 x noise)

Events are appended to <output>/events.jsonl, one JSON object per line:
  {"node", "kind", "state": "start" | "update" | "end", "value", "baseline", "delta", "threshold", "unit",
   "peak", "since", "seq", "received_at", "level"}
"start" opens an event, "update" follows a new peak while it is open, "end" closes it.
replay.py runs this same detector over a recorded clip.
"""

import json
import os
import statistics
import time
from collections import deque
from pathlib import Path

EVENTS_FILE = "events.jsonl"
HISTORY = 60          # readings in the baseline window (5 min at one reading per 5 s)
WARMUP = 12           # readings needed before a buoy can raise events (1 min)
CONFIRM = 2           # consecutive readings past the threshold to open an event
CLEAR = 3             # consecutive readings back inside half the threshold to close it
NOISE_K = 6.0

SIGNALS = {
    "temperature": {"unit": "C", "floor": 0.8, "digits": 2, "value": lambda r: r.get("temp_c")},
    "turbidity": {"unit": "mV", "floor": 80.0, "digits": 0,
                  "value": lambda r: None if r.get("light_mv") is None or r.get("ambient_mv") is None
                  else r["light_mv"] - r["ambient_mv"]},
    "conductivity": {"unit": "mV", "floor": 60.0, "digits": 0, "value": lambda r: r.get("cond_mv")},
}
DEFAULT_KINDS = ("temperature",)


class Track:
    """One signal at one buoy."""

    def __init__(self, kind):
        self.kind = kind
        self.spec = SIGNALS[kind]
        self.history = deque(maxlen=HISTORY)
        self.over = 0
        self.under = 0
        self.event = None             # open event dict, or None

    def baseline(self):
        values = list(self.history)
        mid = statistics.median(values)
        noise = statistics.median(abs(v - mid) for v in values) * 1.4826
        return mid, max(self.spec["floor"], NOISE_K * noise)

    def feed(self, reading):
        value = self.spec["value"](reading)
        if value is None:
            return None
        if len(self.history) < WARMUP:
            self.history.append(value)
            return None
        base, threshold = (self.event["baseline"], self.event["threshold"]) if self.event else self.baseline()
        delta = value - base
        digits = self.spec["digits"]
        record = {"node": reading["node"], "kind": self.kind, "value": round(value, digits),
                  "baseline": round(base, digits), "delta": round(delta, digits), "threshold": round(threshold, digits),
                  "unit": self.spec["unit"], "seq": reading.get("seq"), "received_at": reading.get("received_at"),
                  "level": reading.get("level")}
        if self.event is None:
            if abs(delta) >= threshold:
                self.over += 1
                if self.over >= CONFIRM:
                    self.over = 0
                    self.event = dict(record, state="start", peak=record["value"], since=record["received_at"])
                    return dict(self.event)
                return None                                  # unconfirmed outlier: keep it out of the baseline
            self.over = 0
            self.history.append(value)
            return None
        # event open: the baseline stays frozen
        if abs(delta) > abs(self.event["peak"] - base) and abs(delta) >= threshold:
            self.event["peak"] = record["value"]
            self.under = 0
            return dict(record, state="update", peak=self.event["peak"], since=self.event["since"])
        if abs(delta) < threshold / 2:
            self.under += 1
            if self.under >= CLEAR:
                ended = dict(record, state="end", peak=self.event["peak"], since=self.event["since"])
                self.event, self.under = None, 0
                self.history.append(value)
                return ended
        else:
            self.under = 0
        return None


class EventDetector:
    def __init__(self, output, kinds=DEFAULT_KINDS):
        self.output = Path(output)
        self.kinds = tuple(kinds)
        self.tracks = {}

    def warm_from(self, readings_path):
        """Prime baselines from the tail of readings.jsonl so a restart does not need a fresh warm-up."""
        try:
            with open(readings_path, "rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 2 * HISTORY * 400 * 4))
                lines = stream.read().splitlines()[1:]
        except FileNotFoundError:
            return 0
        count = 0
        for raw in lines:
            try:
                reading = json.loads(raw)
            except ValueError:
                continue
            if isinstance(reading, dict) and "node" in reading:
                for kind in self.kinds:
                    value = SIGNALS[kind]["value"](reading)
                    if value is not None:
                        self._track(reading["node"], kind).history.append(value)
                count += 1
        return count

    def _track(self, node, kind):
        return self.tracks.setdefault((node, kind), Track(kind))

    def feed(self, reading):
        """Feed one parsed reading; returns the events it produced (also appended to events.jsonl)."""
        out = []
        for kind in self.kinds:
            event = self._track(reading["node"], kind).feed(reading)
            if event:
                out.append(event)
        if out:
            self.output.mkdir(parents=True, exist_ok=True)
            with open(self.output / EVENTS_FILE, "a") as stream:
                for event in out:
                    stream.write(json.dumps(event, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return out


def describe(event):
    sign = "+" if event["delta"] >= 0 else ""
    words = {"start": "EVENT", "update": "event peak", "end": "event over"}[event["state"]]
    return (f"{words}: {event['kind']} at {event['node']} {event['value']} {event['unit']} "
            f"({sign}{event['delta']} {event['unit']} vs normal {event['baseline']}, peak {event['peak']})")
