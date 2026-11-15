#!/usr/bin/env python3
"""Show the latest saved gateway image on a local debugging page."""

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit


IMAGE_NAME = re.compile(r"[a-f0-9]{12}_[a-f0-9-]{1,79}\.jpg")
PAGE = b"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lincoln</title>
<style>
body { max-width: 960px; margin: 32px auto; padding: 0 20px;
       font: 16px system-ui, sans-serif; color: #202522; background: #f4f5f2; }
h1 { font-size: 24px; } p { overflow-wrap: anywhere; }
img { display: block; max-width: 100%; height: auto; background: #ddd; }
small { color: #58615b; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 16px 0; }
.card { background: #fff; border-radius: 8px; padding: 12px 14px; }
.card b { display: block; font-size: 28px; font-variant-numeric: tabular-nums; }
.card span { color: #58615b; font-size: 13px; }
.alert { background: #c92a2a; color: #fff; border-radius: 8px; padding: 12px 16px; margin: 10px 0; font-size: 17px; }
.alert b { font-size: 20px; } .alert.done { background: #e9ecef; color: #343a40; font-size: 14px; }
.card.hot { outline: 3px solid #c92a2a; }
.buoy { margin: 20px 0 28px; } .buoy h2 { font-size: 19px; margin: 0 0 4px; } .buoy p { margin: 0; color: #58615b; }
canvas { width: 100%; height: 180px; background: #fff; border-radius: 8px; }
</style>
<h1>Lincoln</h1>
<p id="rstatus" role="status">Waiting for sensor readings...</p>
<div id="buoys"></div>
<small>Orange: temperature. Blue: light with the LED on. Each buoy sends a reading every 5 seconds.</small>
<h2>Camera</h2>
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

const fmt = (v, unit, digits = 0) => v === null || v === undefined ? '--' : v.toFixed(digits) + unit;
function line(ctx, values, color, w, h, minSpan) {
  const v = values.filter(x => x !== null && x !== undefined);
  if (v.length < 2) return;
  const mid = (Math.min(...v) + Math.max(...v)) / 2, span = Math.max(Math.max(...v) - Math.min(...v), minSpan), lo = mid - span / 2;
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
  let started = false;
  values.forEach((x, i) => {
    if (x === null || x === undefined) return;
    const px = 8 + i * (w - 16) / (values.length - 1), py = h - 12 - (x - lo) / span * (h - 24);
    started ? ctx.lineTo(px, py) : ctx.moveTo(px, py); started = true;
  });
  ctx.stroke();
}
const route = level => level === 1 ? 'talks to the gateway directly'
  : level > 1 ? 'relayed through ' + (level - 1) + ' other buoy' + (level > 2 ? 's' : '') + ' (mesh hop ' + level + ')' : 'mesh level unknown';
function panel(node) {
  let el = document.getElementById('buoy-' + node);
  if (el) return el;
  el = document.createElement('section'); el.className = 'buoy'; el.id = 'buoy-' + node;
  el.innerHTML = '<h2>Buoy ' + node + '</h2><p class="meta" role="status"></p>'
    + '<div class="alert" role="alert" hidden></div><div class="alert done" hidden></div><div class="cards">'
    + '<div class="card"><span>Water temperature</span><b class="temp">--</b></div>'
    + '<div class="card"><span>Light, LED on</span><b class="light">--</b></div>'
    + '<div class="card"><span>Turbidity signal (on - ambient)</span><b class="turb">--</b></div>'
    + '<div class="card"><span>Conductivity</span><b class="cond">--</b></div></div>'
    + '<canvas width="920" height="160" aria-label="Temperature and light for buoy ' + node + '"></canvas>';
  document.getElementById('buoys').appendChild(el);
  return el;
}
const KIND = {temperature: ['temp', 'Water temperature'], turbidity: ['turb', 'Turbidity'], conductivity: ['cond', 'Conductivity']};
const NOW = {temperature: r => r.temp_c, conductivity: r => r.cond_mv,
             turbidity: r => r.light_mv === null || r.ambient_mv === null ? null : r.light_mv - r.ambient_mv};
const signed = (v, unit) => (v >= 0 ? '+' : '') + v + ' ' + unit;
function showEvents(ev, latest) {
  document.querySelectorAll('.buoy').forEach(el => {
    el.querySelectorAll('.alert').forEach(box => box.hidden = true);
    el.querySelectorAll('.card').forEach(c => c.classList.remove('hot'));
  });
  ev.active.forEach(e => {
    const el = panel(e.node), box = el.querySelector('.alert'), k = KIND[e.kind] || [e.kind, e.kind];
    const mins = Math.max(0, Math.round((Date.now() / 1000 - e.since) / 60));
    const r = latest[e.node], cur = r && NOW[e.kind] ? NOW[e.kind](r) : null;
    const now = cur === null || cur === undefined ? e.value : +cur.toFixed(e.unit === 'C' ? 2 : 0);
    const delta = +(now - e.baseline).toFixed(e.unit === 'C' ? 2 : 0);
    box.innerHTML = '<b>' + k[1] + ' event</b><br>Now ' + now + ' ' + e.unit + ', ' + signed(delta, e.unit)
      + ' from normal (' + e.baseline + ' ' + e.unit + '). Peak ' + e.peak + ' ' + e.unit + '. Started '
      + new Date(e.since * 1000).toLocaleTimeString() + (mins ? ' (' + mins + ' min ago)' : '') + '.';
    box.hidden = false;
    const card = el.querySelector('.' + k[0]);
    if (card) card.parentElement.classList.add('hot');
  });
  const last = {};
  ev.recent.filter(e => e.state === 'end').forEach(e => last[e.node] = e);
  Object.values(last).forEach(e => {
    if (ev.active.some(a => a.node === e.node)) return;
    const box = panel(e.node).querySelector('.alert.done'), k = KIND[e.kind] || [e.kind, e.kind];
    box.textContent = 'Last event: ' + k[1].toLowerCase() + ' peaked at ' + e.peak + ' ' + e.unit + ' (normal '
      + e.baseline + ' ' + e.unit + '), ' + new Date(e.since * 1000).toLocaleTimeString() + ' to '
      + new Date(e.received_at * 1000).toLocaleTimeString() + '.';
    box.hidden = false;
  });
}
async function readings() {
  try {
    const [rows, ev] = await Promise.all([fetch('/api/readings?n=400', {cache: 'no-store'}).then(x => x.json()),
                                          fetch('/api/events', {cache: 'no-store'}).then(x => x.json())]);
    const rstatus = document.getElementById('rstatus');
    if (!rows.length) { rstatus.textContent = 'Waiting for sensor readings. Keep the USB receiver running.'; return; }
    const byNode = {};
    rows.forEach(r => (byNode[r.node] = byNode[r.node] || []).push(r));
    const nodes = Object.keys(byNode).sort();
    rstatus.textContent = nodes.length + ' buoy' + (nodes.length > 1 ? 's' : '') + ' reporting to this Pi.';
    nodes.forEach(node => {
      const list = byNode[node].slice(-120), r = list[list.length - 1], el = panel(node);
      const age = Math.max(0, Math.floor(Date.now() / 1000 - r.received_at));
      el.querySelector('.meta').textContent = 'Reading #' + r.seq + ' at ' + new Date(r.received_at * 1000).toLocaleTimeString()
        + ' (' + age + 's ago), ' + route(r.level) + ', signal ' + r.rssi + ' dBm.'
        + (age > 20 ? ' No new reading recently; check this buoy.' : '');
      el.querySelector('.temp').textContent = fmt(r.temp_c, ' \\u00b0C', 2);
      el.querySelector('.light').textContent = fmt(r.light_mv, ' mV');
      el.querySelector('.turb').textContent = r.light_mv === null ? '--' : fmt(r.light_mv - r.ambient_mv, ' mV');
      el.querySelector('.cond').textContent = fmt(r.cond_mv, ' mV');
      const c = el.querySelector('canvas'), ctx = c.getContext('2d');
      ctx.clearRect(0, 0, c.width, c.height);
      line(ctx, list.map(x => x.temp_c), '#e8590c', c.width, c.height, 1.0);
      line(ctx, list.map(x => x.light_mv), '#1c7ed6', c.width, c.height, 200);
    });
    const latest = {};
    rows.forEach(r => latest[r.node] = r);
    showEvents(ev, latest);
  } catch (error) {
    document.getElementById('rstatus').textContent = 'Readings error: ' + error.message + '. Retrying...';
  } finally {
    setTimeout(readings, 2000);
  }
}
readings();
</script></html>"""


def latest_readings(directory, count):
    """Last `count` readings from readings.jsonl (reads only the file's tail)."""
    path = Path(directory) / "readings.jsonl"
    try:
        with open(path, "rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - count * 400))
            tail = stream.read().splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for raw in tail[-count:]:
        try:
            row = json.loads(raw)
        except ValueError:
            continue          # first line of the tail may be cut mid-record
        if isinstance(row, dict):
            rows.append(row)
    return rows


def event_state(directory, count=200):
    """Open events (latest record per buoy and signal that is not an "end") plus the recent event log."""
    recent = []
    try:
        with open(Path(directory) / "events.jsonl", "rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - count * 500))
            for raw in stream.read().splitlines()[-count:]:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(row, dict) and "node" in row and "kind" in row:
                    recent.append(row)
    except FileNotFoundError:
        pass
    latest = {}
    for row in recent:
        latest[(row["node"], row["kind"])] = row
    active = [row for row in latest.values() if row.get("state") != "end"]
    return {"active": active, "recent": recent[-20:]}


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
            elif route == "/api/events":
                self.reply(200, "application/json", json.dumps(event_state(self.directory)).encode())
            elif route == "/api/readings":
                query = parse_qs(urlsplit(self.path).query)
                count = min(max(int((query.get("n") or ["120"])[0]), 1), 2000) if (query.get("n") or ["120"])[0].isdecimal() else 120
                self.reply(200, "application/json", json.dumps(latest_readings(self.directory, count)).encode())
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
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; 0.0.0.0 to view from another computer")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        with ThreadingHTTPServer((args.host, args.port), partial(Viewer, directory=args.images.resolve())) as server:
            print(f"Camera viewer: http://localhost:{args.port} | images: {args.images.resolve()}", flush=True)
            server.serve_forever()
    except OSError as exc:
        parser.exit(1, f"Viewer failed: {exc}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
