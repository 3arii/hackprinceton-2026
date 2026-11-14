import base64
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from view_camera_image import main, receive_jpeg


JPEG = b"\xff\xd8example-data\xff\xd9"


def transfer(image=JPEG):
    return [b"FLOTSAM_JPEG_BEGIN " + str(len(image)).encode(),
            b"FLOTSAM_JPEG_DATA " + base64.b64encode(image), b"FLOTSAM_JPEG_END"]


class ViewCameraImageTest(unittest.TestCase):
    def test_receives_image_amid_logs(self):
        lines = [b"I (1) camera_test: ready", *transfer(), b"other log"]
        self.assertEqual(receive_jpeg(lines), JPEG)

    def test_rejects_truncated_transfer(self):
        with self.assertRaises(ValueError):
            receive_jpeg(transfer()[:-1])

    def test_rejects_bad_encoding(self):
        with self.assertRaises(ValueError):
            receive_jpeg([transfer()[0], b"FLOTSAM_JPEG_DATA !!!!", transfer()[2]])

    def test_rejects_invalid_image_and_size(self):
        for lines in (transfer(b"not-a-jpeg"),
                      [b"FLOTSAM_JPEG_BEGIN 999999999"],
                      [b"FLOTSAM_JPEG_BEGIN 4", *transfer()[1:]],
                      [b"FLOTSAM_JPEG_BEGIN 100", *transfer()[1:]]):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                receive_jpeg(lines)

    def test_serial_round_trip_saves_image(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)

        def camera():
            os.read(master, 1024)
            payload = b"\n".join(transfer()) + b"\n"
            os.write(master, payload[:17])
            os.write(master, payload[17:])

        thread = threading.Thread(target=camera, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            output = Path(directory) / "image.jpg"
            self.assertEqual(main(["--port", os.ttyname(slave), "--output", str(output)]), 0)
            self.assertEqual(output.read_bytes(), JPEG)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
