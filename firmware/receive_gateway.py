#!/usr/bin/env python3
"""Save camera JPEGs received through the shore ESP32 USB gateway."""

import argparse
import base64
import fcntl
from glob import glob
import hashlib
import json
import os
from pathlib import Path
import re
import select
import tempfile
import termios
import time
import tty

from events import SIGNALS, EventDetector, describe as describe_event


class ImageReceiver:
    def __init__(self, output):
        self.output = Path(output)
        self.reset()

    def reset(self):
        self.header = None
        self.image = bytearray()

    def feed(self, line):
        if len(line) > 1024:
            raise ValueError("USB line exceeds limit")
        line = line.strip()
        if line.startswith(b"FLOTSAM_BEGIN "):
            self.reset()
            fields = line.decode("ascii").split()
            if len(fields) != 5:
                raise ValueError("Invalid image header")
            _, node, image_id, size, digest = fields
            if (not re.fullmatch(r"[a-f0-9]{12}", node)
                    or not re.fullmatch(r"[a-f0-9-]{1,79}", image_id)
                    or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or not size.isdecimal() or not 4 <= int(size) <= 128 * 1024):
                raise ValueError("Invalid image metadata")
            self.header = node, image_id, int(size), digest
        elif self.header and line.startswith(b"FLOTSAM_DATA "):
            self.image.extend(base64.b64decode(line.split(maxsplit=1)[1], validate=True))
            if len(self.image) > self.header[2]:
                raise ValueError("Image exceeds announced length")
        elif self.header and line == b"FLOTSAM_END":
            node, image_id, size, digest = self.header
            data = bytes(self.image)
            if (len(data) != size or not data.startswith(b"\xff\xd8")
                    or not data.endswith(b"\xff\xd9") or hashlib.sha256(data).hexdigest() != digest):
                raise ValueError("Image length, JPEG markers, or SHA256 mismatch")
            self.output.mkdir(parents=True, exist_ok=True)
            destination = self.output / f"{node}_{image_id}.jpg"
            # Link atomically so retries cannot overwrite a different image with the same ID.
            with tempfile.NamedTemporaryFile(dir=self.output, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    try:
                        os.link(temporary, destination)
                    except FileExistsError:
                        if destination.read_bytes() != data:
                            raise ValueError("Image ID already exists with different content")
                finally:
                    temporary.unlink(missing_ok=True)
            directory = os.open(self.output, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.reset()
            return destination, f"FLOTSAM_ACK {node} {image_id}\n".encode()
        return None


READINGS_FILE = "readings.jsonl"
READING_FIELDS = {"node", "seq", "uptime_ms", "temp_c", "light_mv", "ambient_mv", "cond_mv", "rssi"}


def parse_reading(line):
    """FLOTSAM_READING <node> <json> -> dict, or ValueError. The JSON node must match the line's node."""
    fields = line.strip().decode("ascii").split(" ", 2)
    if len(fields) != 3 or fields[0] != "FLOTSAM_READING" or not re.fullmatch(r"[a-f0-9]{12}", fields[1]):
        raise ValueError("Invalid reading header")
    reading = json.loads(fields[2])
    if not isinstance(reading, dict) or reading.get("node") != fields[1] or not READING_FIELDS <= set(reading):
        raise ValueError("Reading JSON is missing fields or names another node")
    for key in READING_FIELDS - {"node"}:
        value = reading[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError(f"Reading field {key} is not a number")
    level = reading.get("level")
    if level is not None and (isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 16):
        raise ValueError("Reading field level is not a mesh level")
    reading = {key: reading[key] for key in sorted(READING_FIELDS)}
    if level is not None:
        reading["level"] = level            # mesh hop: 1 = talks to the gateway directly, 2 = relayed by another buoy
    reading["received_at"] = round(time.time(), 3)
    return reading


def save_reading(output, reading):
    """Append one reading to <output>/readings.jsonl and flush it to disk."""
    output.mkdir(parents=True, exist_ok=True)
    with open(output / READINGS_FILE, "a") as stream:
        stream.write(json.dumps(reading, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def describe(reading):
    def show(value, unit):
        return "--" if value is None else f"{value}{unit}"
    return (f"Reading {reading['node']} #{reading['seq']}: temp {show(reading['temp_c'], ' C')}, "
            f"light {show(reading['light_mv'], ' mV')} (ambient {show(reading['ambient_mv'], ' mV')}), "
            f"conductivity {show(reading['cond_mv'], ' mV')}, rssi {reading['rssi']}, mesh level {reading.get('level', '?')}")


def write_ack(fd, acknowledgment):
    deadline = time.monotonic() + 2
    while acknowledgment:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
            raise OSError("USB acknowledgment timed out")
        try:
            count = os.write(fd, acknowledgment)
        except BlockingIOError:
            continue
        if count == 0:
            raise OSError("USB disconnected during acknowledgment")
        acknowledgment = acknowledgment[count:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Shore ESP32 USB port (required if several devices are connected)")
    parser.add_argument("--output", type=Path, default=Path("/tmp/flotsam-images"))
    parser.add_argument("--count", type=int, default=0, help="Exit after this many deliveries; 0 runs continuously")
    parser.add_argument("--timeout", type=float, default=0, help="Exit if no complete image arrives for this many seconds")
    parser.add_argument("--events", default="temperature",
                        help="Signals that raise live events: comma list of " + ", ".join(SIGNALS) + "; empty turns events off")
    args = parser.parse_args(argv)
    if args.count < 0 or args.timeout < 0:
        parser.error("count and timeout must be nonnegative")
    ports = sorted(set(glob("/dev/cu.usbmodem*") + glob("/dev/ttyACM*") + glob("/dev/ttyUSB*")))
    if not args.port and len(ports) != 1:
        parser.error(f"Specify the gateway --port; connected devices: {ports}")
    receiver = ImageReceiver(args.output.resolve())
    kinds = [k for k in args.events.split(",") if k]
    if any(k not in SIGNALS for k in kinds):
        parser.error(f"--events takes {', '.join(SIGNALS)}")
    detector = EventDetector(receiver.output, kinds) if kinds else None
    if detector:
        primed = detector.warm_from(receiver.output / READINGS_FILE)
        print(f"Live events on: {', '.join(kinds)} (baseline primed from {primed} saved readings)", flush=True)
    pending = bytearray()
    saved = 0
    last_image = time.monotonic()
    try:
        fd = os.open(args.port or ports[0], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OSError("Gateway receiver is already running for this USB port") from None
            original = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                settings = termios.tcgetattr(fd)
                settings[4] = settings[5] = termios.B115200
                termios.tcsetattr(fd, termios.TCSANOW, settings)
                print(f"Listening on {args.port or ports[0]}; images and {READINGS_FILE}: {receiver.output}", flush=True)
                while not args.count or saved < args.count:
                    if args.timeout and time.monotonic() - last_image > args.timeout:
                        raise TimeoutError("No complete image received before timeout")
                    if not select.select([fd], [], [], 0.5)[0]:
                        continue
                    data = os.read(fd, 4096)
                    if not data:
                        raise OSError("Gateway USB disconnected")
                    pending.extend(data)
                    while b"\n" in pending:
                        line, _, pending = pending.partition(b"\n")
                        if line.startswith(b"FLOTSAM_READING "):
                            try:
                                reading = parse_reading(bytes(line))
                                save_reading(receiver.output, reading)
                                print(describe(reading), flush=True)
                                for event in detector.feed(reading) if detector else ():
                                    print(describe_event(event), flush=True)
                            except (ValueError, UnicodeError) as exc:
                                print(f"Rejected reading: {exc}", flush=True)
                            continue
                        try:
                            result = receiver.feed(bytes(line))
                            if result:
                                path, ack = result
                                write_ack(fd, ack)
                                saved += 1
                                last_image = time.monotonic()
                                print(f"Saved {path} ({path.stat().st_size} bytes); acknowledged", flush=True)
                        except (ValueError, UnicodeError) as exc:
                            receiver.reset()
                            print(f"Rejected transfer: {exc}", flush=True)
                    if len(pending) > 1024:
                        pending.clear()
                        receiver.reset()
                        print("Rejected oversized USB line", flush=True)
            finally:
                termios.tcsetattr(fd, termios.TCSANOW, original)
        finally:
            os.close(fd)
        return 0
    except OSError as exc:
        parser.exit(1, f"Gateway receiver failed: {exc}\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
