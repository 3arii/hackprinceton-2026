#!/usr/bin/env python3
"""Build, flash, and monitor USB camera tests or a camera Wi-Fi hotspot."""

import argparse
from glob import glob
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess

FIRMWARE_DIR = Path(__file__).resolve().parent
LOCAL_IDF = Path("/private/tmp/flotsam-esp-idf-v5.4.2")
LOCAL_TOOLS = Path("/private/tmp/flotsam-idf-tools")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="USB serial port; auto-detected if exactly one is present")
    parser.add_argument("--confirm-pins", action="store_true",
                        help="I verified main/camera_pins.h for this board and intend to replace its firmware")
    parser.add_argument("--dry-run", action="store_true", help="Print the command without writing or flashing")
    parser.add_argument("--wifi", action="store_true", help="Flash the protected 2.4 GHz camera hotspot and browser viewer")
    args = parser.parse_args(argv)
    if not args.dry_run and not args.confirm_pins:
        parser.error("verify main/camera_pins.h, then pass --confirm-pins; --dry-run needs no confirmation")

    ports = sorted(set(glob("/dev/cu.usbmodem*") + glob("/dev/ttyACM*") + glob("/dev/ttyUSB*")))
    if args.port:
        port = args.port
    elif len(ports) == 1:
        port = ports[0]
    else:
        parser.error(f"expected one USB serial port, found {ports}; connect the board or specify --port")
    if not Path(port).exists():
        parser.error(f"serial port does not exist: {port}")

    idf_path = Path(os.environ.get("IDF_PATH") or LOCAL_IDF).expanduser().resolve()
    version_file = idf_path / "tools/cmake/version.cmake"
    if not version_file.is_file() or not (idf_path / "export.sh").is_file():
        parser.error("ESP-IDF 5.4 not found; install it and set IDF_PATH to its directory")
    version = version_file.read_text()
    if not all(re.search(rf"set\(IDF_VERSION_{name}\s+{value}\)", version)
               for name, value in (("MAJOR", 5), ("MINOR", 4))):
        parser.error(f"ESP-IDF 5.4 is required; choose a 5.4 checkout with IDF_PATH (currently {idf_path})")

    build_dir = "build-camera-wifi" if args.wifi else "build-camera-test"
    config_name = "sdkconfig.camera-wifi.local" if args.wifi else "sdkconfig.flash-test.local"
    # Positional shell arguments keep device and SDK paths out of shell source.
    command = [
        "bash", "-c",
        'source "$1/export.sh" && '
        f'idf.py -B {build_dir} -D SDKCONFIG={config_name} build && '
        f'idf.py -B {build_dir} -D SDKCONFIG={config_name} -p "$2" flash monitor',
        "flash_camera_test", str(idf_path), port,
    ]
    application = "Camera Wi-Fi hotspot" if args.wifi else "Camera-only test"
    print(f"{application} on {port}. Flashing replaces the board's existing firmware.", flush=True)
    if args.dry_run:
        print(f"Working directory: {FIRMWARE_DIR}")
        print(shlex.join(command))
        return 0

    # Script-owned configurations prevent accidentally flashing the mesh application.
    config_file = FIRMWARE_DIR / config_name
    if args.wifi:
        previous = config_file.read_text() if config_file.exists() else ""
        match = re.search(r'^CONFIG_FLOTSAM_AP_PASSWORD="([A-Za-z0-9_-]{16,63})"$', previous, re.MULTILINE)
        password = match.group(1) if match else secrets.token_urlsafe(18)
        config = ("CONFIG_FLOTSAM_CAMERA_WIFI=y\nCONFIG_FLOTSAM_CAMERA_PINMAP_CONFIRMED=y\n"
                  f'CONFIG_FLOTSAM_AP_PASSWORD="{password}"\n')
        print(f"Hotspot password: {password}", flush=True)
        print("After flashing, join Flotsam-Camera-... and open http://192.168.4.1", flush=True)
    else:
        config = "CONFIG_FLOTSAM_CAMERA_TEST=y\nCONFIG_FLOTSAM_CAMERA_PINMAP_CONFIRMED=y\n"
    config_file.write_text(config)
    config_file.chmod(0o600)
    env = os.environ.copy()
    if idf_path == LOCAL_IDF.resolve():
        env["IDF_TOOLS_PATH"] = str(LOCAL_TOOLS)
    return subprocess.run(command, cwd=FIRMWARE_DIR, env=env, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
