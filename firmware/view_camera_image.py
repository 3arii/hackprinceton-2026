#!/usr/bin/env python3
"""Request a camera-test JPEG over USB and save it on macOS or Linux."""

import argparse
import base64
from glob import glob
import os
from pathlib import Path
import select
import tempfile
import termios
import time
import tty


def receive_jpeg(lines):
    expected = None
    image = bytearray()
    for line in lines:
        if len(line) > 4096:
            raise ValueError("USB line is too long")
        line = line.strip()
        if line.startswith(b"FLOTSAM_JPEG_BEGIN "):
            if expected is not None:
                raise ValueError("Image transfer restarted before completing")
            expected = int(line.split()[1])
            if not 4 <= expected <= 512 * 1024:
                raise ValueError("Image size is outside the allowed range")
        elif expected is not None and line.startswith(b"FLOTSAM_JPEG_DATA "):
            image.extend(base64.b64decode(line.split(maxsplit=1)[1], validate=True))
            if len(image) > expected:
                raise ValueError("Image contains more bytes than announced")
        elif expected is not None and line == b"FLOTSAM_JPEG_END":
            if len(image) != expected or not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
                raise ValueError("Incomplete or invalid JPEG received")
            return bytes(image)
    raise ValueError("No complete image received; flash the USB export test and close other serial monitors")


def serial_lines(fd, timeout=30):
    deadline = time.monotonic() + timeout
    next_request = 0
    started = False
    pending = bytearray()
    while time.monotonic() < deadline:
        now = time.monotonic()
        if not started and now >= next_request:
            os.write(fd, b"p\n")
            next_request = now + 2
        if not select.select([fd], [], [], min(1, max(0, deadline - now)))[0]:
            continue
        data = os.read(fd, 4096)
        if not data:
            raise OSError("USB device disconnected")
        pending.extend(data)
        while b"\n" in pending:
            line, _, pending = pending.partition(b"\n")
            if line.startswith(b"FLOTSAM_JPEG_BEGIN "):
                started = True
            yield bytes(line)
        if len(pending) > 4096:
            raise ValueError("USB line is too long")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="USB serial port; auto-detects a single connected device")
    parser.add_argument("--output", type=Path, default=Path("/tmp/flotsam-camera.jpg"))
    args = parser.parse_args(argv)
    ports = sorted(set(glob("/dev/cu.usbmodem*") + glob("/dev/ttyACM*") + glob("/dev/ttyUSB*")))
    if args.port:
        port = args.port
    elif len(ports) == 1:
        port = ports[0]
    else:
        parser.error(f"expected one USB device, found {ports}; use --port")

    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            original = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                settings = termios.tcgetattr(fd)
                settings[4] = settings[5] = termios.B115200
                termios.tcsetattr(fd, termios.TCSANOW, settings)
                print(f"Requesting an image from {port}...", flush=True)
                image = receive_jpeg(serial_lines(fd))
            finally:
                termios.tcsetattr(fd, termios.TCSANOW, original)
        finally:
            os.close(fd)
        output = args.output.resolve()
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as temp:
            temporary = Path(temp.name)
            try:
                temp.write(image)
                temp.flush()
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
        print(f"Saved {output} ({len(image)} bytes)")
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Image capture failed: {exc}\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
