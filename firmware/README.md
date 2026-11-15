# Firmware and shore scripts

```text
Buoy (ESP32-S3 CAM)  --Wi-Fi mesh, HTTP-->  Gateway (ESP32-C3)  --USB serial-->  Raspberry Pi  -->  S3
```

The buoy posts one sensor reading every 5 seconds and one 640x480 JPEG every 30 seconds to the gateway. The gateway is a Wi-Fi access point with a small HTTP server; it forwards everything to the Pi over USB. The Pi saves it, shows a local page, runs the event detector and uploads to S3.

## Hardware

- Buoy: ESP32-S3 CAM V1.2 (OV3660, 8 MB PSRAM, 8 MB flash), powered with regulated 5.0 V on 5V and GND
- Gateway: ESP32-C3 Super Mini, USB powered from the Pi
- Both antennas stay above water

Do not power the buoy board from USB and the buoy supply at the same time. Disconnect the battery and solar before flashing.

| Signal | GPIO | Part |
|---|---|---|
| Temperature | 0 | DS18B20, 1-Wire |
| Light (turbidity) | 1 | TEMT6000, analog |
| Conductivity | 14 | two electrodes on a divider, analog |
| Sample line | 47 | switches the pump, turbidity LED and conductivity excitation together |

Camera pins are in `main/camera_pins.h`. All pins and intervals can be changed in `main/Kconfig.projbuild`.

## Build and flash

Needs ESP-IDF 5.4 with the `esp32c3` and `esp32s3` tools. Run from the repository root:

```sh
python3 firmware/flash_gateway_link.py gateway --port <gateway port>
python3 firmware/flash_gateway_link.py camera --confirm-pins --port <buoy port>
```

The first command generates the Wi-Fi, upload token and mesh credentials in `firmware/gateway-credentials.local.json` and the second reuses them. That file is ignored by Git. Keep it: deleting it means reflashing both boards. Built firmware contains the credentials, so treat it as private.

A second buoy that reaches the gateway through the first one is flashed with `camera --relay --slot 1`. Give each buoy its own `--slot` so their photos do not collide. `--build-only` compiles without touching hardware.

## Run on the Pi

```sh
python3 firmware/start_gateway.py --port <gateway port> --output ~/flotsam-images --host 0.0.0.0
```

This starts the receiver and a page on port 8000 with the latest readings, events and photo. `receive_gateway.py` and `serve_gateway.py` are the two halves if you want to run them separately.

```sh
FLOTSAM_BUCKET=<bucket> FLOTSAM_READINGS_PREFIX=readings/ python3 firmware/upload_s3.py
```

uploads photos as they arrive and readings and events once a minute. `pi/` has systemd units for both and an example environment file. The gateway unit names the USB port by serial id, so change it for your board.

## Delivery

The buoy posts each JPEG to the gateway with a bearer token, node id and image id. The gateway checks the token, the JPEG markers and a 128 KiB limit, then forwards it:

```text
FLOTSAM_BEGIN <node-id> <image-id> <byte-length> <sha256>
FLOTSAM_DATA <base64 chunk>
...
FLOTSAM_END
```

The Pi checks length and SHA-256, saves the file, then answers `FLOTSAM_ACK <node-id> <image-id>`. Only then does the gateway return HTTP 201 to the buoy; without the ACK it returns 503 and the buoy retries the same image up to three times. Readings travel as single `FLOTSAM_READING <node-id> <json>` lines.

## Events

`events.py` keeps a rolling baseline per buoy and signal (median of the last 60 readings) and a noise estimate. A reading further than max(floor, 6 x noise) from the baseline, twice in a row, opens an event; three readings back inside half the threshold close it. The baseline is frozen while an event is open. Temperature is on by default; pass `--events temperature,conductivity,turbidity` to `start_gateway.py` for the others.
