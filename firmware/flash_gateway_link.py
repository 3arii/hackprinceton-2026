#!/usr/bin/env python3
"""Build/flash the USB shore gateway or the matching ESP32-S3 buoy camera."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

from flash_camera_test import FIRMWARE_DIR, LOCAL_IDF, LOCAL_TOOLS


def credentials(path):
    if path.exists():
        values = json.loads(path.read_text())
        if set(values) != {"password", "token", "mesh_password"} or any(
                not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,63}", v)
                for v in values.values()):
            raise ValueError("Invalid saved gateway credentials")
        return values
    values = {key: secrets.token_urlsafe(24) for key in ("password", "token", "mesh_password")}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(values, stream)
    return values


def profile(role, values):
    if role == "gateway":
        return ('CONFIG_FLOTSAM_GATEWAY_SSID="Flotsam-Gateway"\n'
                f'CONFIG_FLOTSAM_GATEWAY_PASSWORD="{values["password"]}"\n'
                f'CONFIG_FLOTSAM_GATEWAY_TOKEN="{values["token"]}"\n')
    return ('CONFIG_FLOTSAM_CAMERA_MESH=y\nCONFIG_FLOTSAM_CAMERA_PINMAP_CONFIRMED=y\n'
            'CONFIG_FLOTSAM_MESH_ROOT=y\nCONFIG_FLOTSAM_NODE_SLOT=0\n'
            'CONFIG_FLOTSAM_PI_SSID="Flotsam-Gateway"\n'
            f'CONFIG_FLOTSAM_PI_PASSWORD="{values["password"]}"\n'
            f'CONFIG_BRIDGE_SOFTAP_PASSWORD="{values["mesh_password"]}"\n'
            'CONFIG_FLOTSAM_RECEIVER_URL="http://192.168.4.1/images"\n'
            f'CONFIG_FLOTSAM_UPLOAD_TOKEN="{values["token"]}"\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("gateway", "camera"))
    parser.add_argument("--port", help="Explicit USB port for the intended board")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--confirm-pins", action="store_true", help="Confirm the existing ESP32S3 CAM V1.2 pin map")
    parser.add_argument("--gateway-chip", choices=("esp32c3", "esp32s3"), default="esp32c3")
    args = parser.parse_args(argv)
    if not args.build_only and (not args.port or not Path(args.port).exists()):
        parser.error("Flashing requires --port pointing to the intended connected USB board")
    if args.role == "camera" and not args.confirm_pins:
        parser.error("Camera builds require --confirm-pins for the verified camera_pins.h")
    idf = Path(os.environ.get("IDF_PATH") or LOCAL_IDF).expanduser().resolve()
    try:
        version = (idf / "tools/cmake/version.cmake").read_text()
        if not all(re.search(rf"set\(IDF_VERSION_{key}\s+{value}\)", version)
                   for key, value in (("MAJOR", 5), ("MINOR", 4))):
            raise ValueError("ESP-IDF 5.4 is required")
        values = credentials(FIRMWARE_DIR / "gateway-credentials.local.json")
        project = FIRMWARE_DIR / "gateway" if args.role == "gateway" else FIRMWARE_DIR
        config = project / "sdkconfig.gateway-link.local"
        # Seed the role each run so a stale application choice cannot select a camera test.
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(profile(args.role, values))
        target = args.gateway_chip if args.role == "gateway" else "esp32s3"
        build = "build-link-" + target
        command = ["bash", "-c",
                   'source "$1/export.sh" && shift && idf.py "$@"',
                   "flash_gateway_link", str(idf), "-B", build, "-D", f"SDKCONFIG={config}",
                   "-D", f"IDF_TARGET={target}"]
        if not args.build_only:
            command += ["-p", args.port]
        command += ["build"] + ([] if args.build_only else ["flash"])
        env = os.environ.copy()
        if idf == LOCAL_IDF.resolve():
            env["IDF_TOOLS_PATH"] = str(LOCAL_TOOLS)
        print(f"{args.role}: {target}; {'build only' if args.build_only else args.port}", flush=True)
        return subprocess.run(command, cwd=project, env=env, check=False).returncode
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Gateway link setup failed: {exc}\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
