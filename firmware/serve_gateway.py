#!/usr/bin/env python3
"""Show the latest saved gateway image on a local debugging page."""

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


IMAGE_NAME = re.compile(r"[a-f0-9]{12}_[a-f0-9-]{1,79}\.jpg")
PAGE = b"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flotsam camera test</title>
<style>
body { max-width: 960px; margin: 32px auto; padding: 0 20px;
       font: 16px system-ui, sans-serif; color: #202522; background: #f4f5f2; }
h1 { font-size: 24px; } p { overflow-wrap: anywhere; }
img { display: block; max-width: 100%; height: auto; background: #ddd; }
small { color: #58615b; }
</style>
<h1>Flotsam camera test</h1>
<p id="status" role="status">Waiting for a saved image...</p>
<img id="picture" alt="Latest picture received from the buoy camera" hidden>
<p id="details"></p>
<small>Camera sends every 30 seconds. This page checks for saved images every 2 seconds.</small>
<script>
const picture = document.getElementById('picture');
const status = document.getElementById('status');
const details = document.getElementById('details');
let displayed = '';
async function refresh() {
  try {
    const response = await fetch('/api/latest', {cache: 'no-store'});
    if (!response.ok) throw new Error('Server returned ' + response.status);
    const image = await response.json();
    if (!image) {
      status.textContent = 'Waiting for a saved image. Keep the USB receiver running.';
      return;
    }
    if (image.name !== displayed) {
      const next = new Image();
      next.src = '/images/' + encodeURIComponent(image.name);
      await next.decode();
      picture.src = next.src;
      picture.hidden = false;
      displayed = image.name;
      details.textContent = image.name + ' | ' + image.bytes.toLocaleString() + ' bytes';
    }
    const age = Math.max(0, Math.floor(Date.now() / 1000 - image.received_at));
    status.textContent = 'Received ' + new Date(image.received_at * 1000).toLocaleTimeString()
      + ' (' + age + 's ago).' + (age > 60 ? ' No new image recently; check the camera and USB receiver.' : '');
  } catch (error) {
    status.textContent = 'Viewer error: ' + error.message + '. Retrying...';
  } finally {
    setTimeout(refresh, 2000);
  }
}
refresh();
</script></html>"""


class Viewer(BaseHTTPRequestHandler):
    def __init__(self, *args, directory, **kwargs):
        self.directory = Path(directory)
        super().__init__(*args, **kwargs)

    def reply(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlsplit(self.path).path
        try:
            if route == "/":
                self.reply(200, "text/html; charset=utf-8", PAGE)
            elif route == "/api/latest":
                latest = None
                for path in self.directory.glob("*.jpg"):
                    if not IMAGE_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                        continue
                    info = path.stat()
                    if latest is None or info.st_mtime > latest["received_at"]:
                        latest = {"name": path.name, "bytes": info.st_size, "received_at": info.st_mtime}
                self.reply(200, "application/json", json.dumps(latest).encode())
            elif route.startswith("/images/") and IMAGE_NAME.fullmatch(route[len("/images/"):]):
                path = self.directory / route[len("/images/"):]
                if path.is_symlink():
                    self.reply(404, "text/plain", b"Image not found")
                else:
                    self.reply(200, "image/jpeg", path.read_bytes())
            else:
                self.reply(404, "text/plain", b"Not found")
        except FileNotFoundError:
            self.reply(404, "text/plain", b"Image not found")
        except OSError:
            self.reply(500, "text/plain", b"Cannot read saved image directory")

    def log_message(self, format, *args):
        # Polling every two seconds otherwise fills the debug terminal with access logs.
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, default=Path("/tmp/flotsam-images"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), partial(Viewer, directory=args.images.resolve())) as server:
            print(f"Camera viewer: http://localhost:{args.port} | images: {args.images.resolve()}", flush=True)
            server.serve_forever()
    except OSError as exc:
        parser.exit(1, f"Viewer failed: {exc}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
