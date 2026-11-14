import base64
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import os
from pathlib import Path
import select
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flash_gateway_link import credentials, profile
from receive_gateway import ImageReceiver, main

JPEG = b"\xff\xd8camera-payload\xff\xd9"
NODE = "2884855a1da8"
IMAGE = NODE + "-abcd-1"


def transfer(data=JPEG, image_id=IMAGE):
    header = f"FLOTSAM_BEGIN {NODE} {image_id} {len(data)} {hashlib.sha256(data).hexdigest()}".encode()
    return [header, b"FLOTSAM_DATA " + base64.b64encode(data), b"FLOTSAM_END"]


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.receiver = ImageReceiver(self.directory.name)

    def receive(self, lines):
        result = None
        for line in lines:
            result = self.receiver.feed(line)
        return result

    def test_save_and_deduplicate_before_ack(self):
        path, ack = self.receive(transfer())
        self.assertEqual(path.read_bytes(), JPEG)
        self.assertEqual(ack, f"FLOTSAM_ACK {NODE} {IMAGE}\n".encode())
        self.receive(transfer())
        self.assertEqual(len(list(Path(self.directory.name).iterdir())), 1)

    def test_rejects_same_id_different_content(self):
        path, _ = self.receive(transfer())
        with self.assertRaisesRegex(ValueError, "different content"):
            self.receive(transfer(b"\xff\xd8different\xff\xd9"))
        self.assertEqual(path.read_bytes(), JPEG)

    def test_hash_detects_corruption_with_valid_jpeg_markers(self):
        lines = transfer()
        lines[1] = b"FLOTSAM_DATA " + base64.b64encode(JPEG.replace(b"camera", b"broken"))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.receive(lines)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_invalid_headers_encoding_size_and_jpeg(self):
        cases = [transfer(image_id="../../escape"), transfer(b"bad-data"),
                 [transfer()[0].replace(str(len(JPEG)).encode() + b" ", b"999999 ")],
                 [transfer()[0], b"FLOTSAM_DATA !!!!"],
                 [transfer()[0], transfer()[1], transfer()[1]],
                 [b"FLOTSAM_BEGIN invalid"], [b"x" * 1025]]
        for lines in cases:
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                self.receiver.reset()
                self.receive(lines)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_restart_recovers_truncated_frame_and_ignores_logs(self):
        self.receive(transfer()[:-1])
        self.receiver.feed(b"I (10) wifi: connected")
        path, _ = self.receive(transfer())
        self.assertEqual(path.read_bytes(), JPEG)

    def test_write_failure_never_returns_ack(self):
        output = Path(self.directory.name) / "not-a-directory"
        output.write_text("occupied")
        self.receiver = ImageReceiver(output)
        with self.assertRaises(OSError):
            self.receive(transfer())

    def test_credentials_retained_and_shared_by_both_profiles(self):
        path = Path(self.directory.name) / "credentials.json"
        values = credentials(path)
        self.assertEqual(credentials(path), values)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for role in ("camera", "gateway"):
            config = profile(role, values)
            self.assertIn(values["password"], config)
            self.assertIn(values["token"], config)
        self.assertIn("CONFIG_FLOTSAM_MESH_ROOT=y", profile("camera", values))
        path.write_text('{"password": "injection"}')
        with self.assertRaises(ValueError):
            credentials(path)

    def test_usb_round_trip_ack_after_save(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        replies = []
        def gateway():
            time.sleep(0.1)
            packet = b"\n".join(transfer()) + b"\n"
            os.write(master, packet[:19])
            os.write(master, packet[19:])
            if select.select([master], [], [], 3)[0]:
                replies.append(os.read(master, 1024))
                replies.append(len(list(Path(self.directory.name).glob("*.jpg"))))
        thread = threading.Thread(target=gateway, daemon=True)
        thread.start()
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["--port", os.ttyname(slave), "--output", self.directory.name,
                                   "--count", "1", "--timeout", "3"]), 0)
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(replies, [f"FLOTSAM_ACK {NODE} {IMAGE}\n".encode(), 1])


if __name__ == "__main__":
    unittest.main()
