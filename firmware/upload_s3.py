#!/usr/bin/env python3
"""Upload what the shore collector saves (camera images, sensor readings, live events) to private S3, with retries.

The collector (firmware/start_gateway.py) atomically saves each JPEG to IMAGE_DIR as
<node-id>_<image-id>.jpg before it acknowledges the camera. This service watches that
directory and uploads every image it has not uploaded yet. Local files are never deleted,
so a network or AWS outage only delays uploads; nothing is lost.

Config (environment, usually from /home/pi/flotsam/uploader.env):
  FLOTSAM_BUCKET   S3 bucket name (required unless --dry-run)
  AWS_REGION       e.g. us-east-2
  FLOTSAM_PREFIX   key prefix, default "images/"
  FLOTSAM_IMAGES   image directory, default ~/flotsam-images
  FLOTSAM_STATE    where upload markers are kept, default ~/.flotsam-upload-state
  FLOTSAM_READINGS_PREFIX  e.g. "readings/" to also upload readings and events once a minute; empty = off.
                   Needs s3:PutObject on <bucket>/readings/*. Keys:
                     readings/<node>/<yyyy>/<mm>/<dd>/<utc>-<offset>.jsonl          (readings.jsonl lines)
                     readings/events/<node>/<yyyy>/<mm>/<dd>/<utc>-<offset>.jsonl   (events.jsonl lines, see events.py)
                   Dedupe readings on (node, seq): a batch that failed half way is sent again.
  AWS credentials come from the normal AWS chain (~/.aws/credentials, AWS_PROFILE, ...).

Usage:
  upload_s3.py            run forever
  upload_s3.py --once     upload what is there now, then exit
  upload_s3.py --dry-run  list what would be uploaded; no AWS access needed
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("flotsam-uploader")

NAME = re.compile(r"^(?P<node>[0-9a-fA-F]{12})_(?P<image>[A-Za-z0-9._-]+)\.jpg$")
SCAN_SECONDS = 5
MIN_AGE_SECONDS = 2          # collector renames atomically; this is just extra caution
BACKOFF_START, BACKOFF_MAX = 5, 300
READINGS_SECONDS = 60
STREAMS = (("readings.jsonl", "", "readings.offset"), ("events.jsonl", "events/", "events.offset"))


def settings():
    home = Path.home()
    return {
        "bucket": os.environ.get("FLOTSAM_BUCKET", "").strip(),
        "region": os.environ.get("AWS_REGION", "us-east-2").strip(),
        "prefix": os.environ.get("FLOTSAM_PREFIX", "images/").strip(),
        "images": Path(os.environ.get("FLOTSAM_IMAGES", home / "flotsam-images")).expanduser(),
        "state": Path(os.environ.get("FLOTSAM_STATE", home / ".flotsam-upload-state")).expanduser(),
        "readings_prefix": os.environ.get("FLOTSAM_READINGS_PREFIX", "").strip(),
    }


def pending(cfg):
    """Complete images that have no upload marker yet, oldest first."""
    now = time.time()
    out = []
    for p in cfg["images"].glob("*.jpg"):
        m = NAME.match(p.name)
        if not m:
            continue
        if (cfg["state"] / (p.name + ".done")).exists():
            continue
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        if now - st.st_mtime < MIN_AGE_SECONDS:
            continue
        out.append((st.st_mtime, p, m["node"].lower(), m["image"]))
    return [x[1:] for x in sorted(out)]


def key_for(cfg, path, node, received):
    day = received.strftime("%Y/%m/%d")
    return f"{cfg['prefix'].rstrip('/')}/{node}/{day}/{path.name}"


def upload(s3, cfg, path, node, image):
    data = path.read_bytes()
    if not (data.startswith(b"\xff\xd8") and data.rstrip(b"\x00").endswith(b"\xff\xd9")):
        log.warning("skip %s: not a complete JPEG", path.name)
        return False
    received = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    digest = hashlib.sha256(data).digest()
    key = key_for(cfg, path, node, received)
    s3.put_object(
        Bucket=cfg["bucket"],
        Key=key,
        Body=data,
        ContentType="image/jpeg",
        ServerSideEncryption="AES256",
        ChecksumSHA256=base64.b64encode(digest).decode(),   # S3 rejects the upload if bytes change in transit
        Metadata={
            "node-id": node,
            "image-id": image,
            "received-at": received.isoformat(timespec="seconds"),
            "sha256": digest.hex(),
            "source": "flotsam-shore-pi",
        },
    )
    (cfg["state"] / (path.name + ".done")).write_text(f"s3://{cfg['bucket']}/{key}\n")
    log.info("uploaded %s -> s3://%s/%s (%d bytes)", path.name, cfg["bucket"], key, len(data))
    return True


def new_lines(cfg, name, marker_name):
    """Complete lines appended to <name> since the last upload: (start_offset, end_offset, {node: [lines]})."""
    path = cfg["images"] / name
    marker = cfg["state"] / marker_name
    start = int(marker.read_text()) if marker.exists() else 0
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            if f.tell() < start:          # file was replaced or truncated: start over
                start = 0
            f.seek(start)
            chunk = f.read()
    except FileNotFoundError:
        return start, start, {}
    complete = chunk[: chunk.rfind(b"\n") + 1]           # never upload a half-written line
    by_node = {}
    for raw in complete.splitlines():
        try:
            node = json.loads(raw).get("node", "unknown")
        except ValueError:
            continue
        if re.fullmatch(r"[0-9a-f]{12}", str(node)):
            by_node.setdefault(node, []).append(raw)
    return start, start + len(complete), by_node


def upload_readings(s3, cfg):
    return sum(upload_stream(s3, cfg, *stream) for stream in STREAMS)


def upload_stream(s3, cfg, name, sub, marker_name):
    start, end, by_node = new_lines(cfg, name, marker_name)
    if end == start:
        return 0
    now = datetime.now(timezone.utc)
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y/%m/%d")
    count = 0
    for node, lines in sorted(by_node.items()):
        body = b"\n".join(lines) + b"\n"
        # The start offset in the key makes a retry after a crash overwrite the same object instead of duplicating it.
        key = f"{cfg['readings_prefix'].rstrip('/')}/{sub}{node}/{day}/{stamp}-{start}.jsonl"
        s3.put_object(Bucket=cfg["bucket"], Key=key, Body=body, ContentType="application/x-ndjson",
                      ServerSideEncryption="AES256",
                      ChecksumSHA256=base64.b64encode(hashlib.sha256(body).digest()).decode(),
                      Metadata={"node-id": node, "readings": str(len(lines)), "source": "flotsam-shore-pi"})
        log.info("uploaded %d %s line(s) from %s -> s3://%s/%s", len(lines), name.split(".")[0], node, cfg["bucket"], key)
        count += len(lines)
    (cfg["state"] / marker_name).write_text(str(end))
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="upload what is pending, then exit")
    ap.add_argument("--dry-run", action="store_true", help="show what would be uploaded; no AWS calls")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    cfg = settings()
    cfg["images"].mkdir(parents=True, exist_ok=True)
    cfg["state"].mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        items = pending(cfg)
        for path, node, image in items:
            received = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            print(f"would upload {path.name} -> s3://{cfg['bucket'] or '<FLOTSAM_BUCKET>'}/{key_for(cfg, path, node, received)}")
        print(f"{len(items)} image(s) pending in {cfg['images']}")
        return 0

    if not cfg["bucket"]:
        log.error("FLOTSAM_BUCKET is not set; put it in /home/pi/flotsam/uploader.env (checking again in 5 min)")
        if not args.once:
            time.sleep(300)   # systemd restarts us afterwards; avoids a tight restart loop until configured
        return 2

    import boto3
    from botocore.config import Config

    s3 = boto3.client("s3", region_name=cfg["region"], config=Config(retries={"max_attempts": 3, "mode": "standard"}))
    log.info("watching %s -> s3://%s/%s (region %s)", cfg["images"], cfg["bucket"], cfg["prefix"], cfg["region"])
    if cfg["readings_prefix"]:
        log.info("sensor readings -> s3://%s/%s every %d s", cfg["bucket"], cfg["readings_prefix"], READINGS_SECONDS)

    backoff = BACKOFF_START
    next_readings = 0.0
    while True:
        failed = False
        if cfg["readings_prefix"] and (args.once or time.monotonic() >= next_readings):
            next_readings = time.monotonic() + READINGS_SECONDS
            try:
                upload_readings(s3, cfg)
            except Exception as e:  # e.g. AccessDenied until readings/* is allowed; images keep flowing
                log.warning("readings upload failed (will retry in %d s): %s", READINGS_SECONDS, e)
        for path, node, image in pending(cfg):
            try:
                upload(s3, cfg, path, node, image)
            except Exception as e:  # network down, expired credentials, throttling: keep the file, retry later
                log.warning("upload failed for %s: %s", path.name, e)
                failed = True
                break
        if args.once:
            return 1 if failed else 0
        if failed:
            log.info("retrying in %d s", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
        else:
            backoff = BACKOFF_START
            time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
