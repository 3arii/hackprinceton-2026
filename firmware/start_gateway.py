#!/usr/bin/env python3
"""Listen to the USB Super Mini and show received camera images locally."""

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

from receive_gateway import main as receive_images
from serve_gateway import Viewer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Super Mini USB port; auto-detected when it is the only USB board")
    parser.add_argument("--output", type=Path, default=Path("/tmp/flotsam-images"))
    parser.add_argument("--http-port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1", help="Viewer bind address; 0.0.0.0 to open it from another computer")
    parser.add_argument("--events", default="temperature", help="Signals that raise live events (see events.py); empty turns them off")
    args = parser.parse_args(argv)
    if not 0 <= args.http_port <= 65535:
        parser.error("HTTP port must be between 0 and 65535")
    output = args.output.expanduser().resolve()
    try:
        server = ThreadingHTTPServer((args.host, args.http_port), partial(Viewer, directory=output))
    except OSError as exc:
        parser.exit(1, f"Cannot start viewer: {exc}. Close the old viewer or use --http-port 8001.\n")
    with server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            print(f"Open http://{'localhost' if args.host == '127.0.0.1' else args.host}:{server.server_port}", flush=True)
            print("Waiting for the battery-powered camera. Keep the Super Mini connected by USB. Ctrl+C stops both services.", flush=True)
            receiver_args = ["--output", str(output)]
            if args.events != "temperature":           # the receiver's default
                receiver_args += ["--events", args.events]
            if args.port:
                receiver_args += ["--port", args.port]
            return receive_images(receiver_args)
        except KeyboardInterrupt:
            return 130
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
