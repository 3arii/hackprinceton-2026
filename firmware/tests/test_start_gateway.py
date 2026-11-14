from contextlib import redirect_stdout, redirect_stderr
import fcntl
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import start_gateway
from receive_gateway import main as receive_main


class StartGatewayTest(unittest.TestCase):
    def test_starts_viewer_with_same_image_directory_and_cleans_up_on_interrupt(self):
        servers = []
        def create_server(*args):
            server = ThreadingHTTPServer(*args)
            servers.append(server)
            return server
        with tempfile.TemporaryDirectory() as directory:
            name = "2884855a1da8_2884855a1da8-abcd-1.jpg"
            (Path(directory) / name).write_bytes(b"\xff\xd8test\xff\xd9")
            def receive(args):
                self.assertEqual(args, ["--output", str(Path(directory).resolve()), "--port", "/dev/test-gateway"])
                connection = HTTPConnection("127.0.0.1", servers[0].server_port, timeout=2)
                try:
                    connection.request("GET", "/api/latest")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read())["name"], name)
                finally:
                    connection.close()
                raise KeyboardInterrupt
            with patch.object(start_gateway, "ThreadingHTTPServer", side_effect=create_server), \
                    patch.object(start_gateway, "receive_images", side_effect=receive), redirect_stdout(StringIO()):
                result = start_gateway.main(["--port", "/dev/test-gateway", "--output", directory, "--http-port", "0"])
            self.assertEqual(result, 130)
            self.assertEqual(servers[0].socket.fileno(), -1)

    def test_busy_web_port_does_not_start_second_usb_receiver(self):
        with patch.object(start_gateway, "ThreadingHTTPServer", side_effect=OSError("Address in use")), \
                patch.object(start_gateway, "receive_images") as receiver, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                start_gateway.main([])
            self.assertEqual(stopped.exception.code, 1)
            receiver.assert_not_called()

    def test_second_usb_receiver_is_rejected(self):
        master, slave = os.openpty()
        try:
            fcntl.flock(slave, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit):
                receive_main(["--port", os.ttyname(slave), "--timeout", "1"])
            self.assertIn("already running", error.getvalue())
        finally:
            os.close(master)
            os.close(slave)
