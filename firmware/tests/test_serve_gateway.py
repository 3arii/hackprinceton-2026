from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve_gateway import Viewer

NAME = "2884855a1da8_2884855a1da8-abcd-1.jpg"
JPEG = b"\xff\xd8test\xff\xd9"


class ViewerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Viewer, directory=self.directory))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, path):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_empty_directory_and_page(self):
        self.assertEqual(json.loads(self.get("/api/latest")[2]), None)
        status, headers, page = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"/api/latest", page)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_latest_changes_and_image_matches_saved_bytes(self):
        first = self.directory / NAME
        first.write_bytes(JPEG)
        os.utime(first, (100, 100))
        self.assertEqual(json.loads(self.get("/api/latest")[2])["name"], NAME)
        new_name = NAME.replace("-1.jpg", "-2.jpg")
        (self.directory / new_name).write_bytes(JPEG + b"new")
        latest = json.loads(self.get("/api/latest?fresh=1")[2])
        self.assertEqual(latest["name"], new_name)
        self.assertEqual(latest["bytes"], len(JPEG) + 3)
        status, headers, image = self.get("/images/" + NAME)
        self.assertEqual((status, headers["Content-Type"], image), (200, "image/jpeg", JPEG))

    def test_no_directory_listing_traversal_or_symlink_access(self):
        (self.directory / "secret.txt").write_text("secret")
        (self.directory / NAME).symlink_to(self.directory / "secret.txt")
        for route in ("/images/", "/images/../secret.txt", "/images/%2e%2e/secret.txt",
                      "/secret.txt", "/images/" + NAME):
            with self.subTest(route=route):
                self.assertEqual(self.get(route)[0], 404)
        self.assertIsNone(json.loads(self.get("/api/latest")[2]))


if __name__ == "__main__":
    unittest.main()
