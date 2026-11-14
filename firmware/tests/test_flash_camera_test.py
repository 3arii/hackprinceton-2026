from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flash_camera_test as flash

REAL_RUN = subprocess.run


class FlashCameraTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sdk = self.root / "sdk with spaces"
        (self.sdk / "tools/cmake").mkdir(parents=True)
        (self.sdk / "export.sh").write_text("")
        self.version = self.sdk / "tools/cmake/version.cmake"
        self.version.write_text("set(IDF_VERSION_MAJOR 5)\nset(IDF_VERSION_MINOR 4)\n")
        self.port = self.root / "serial port"
        self.port.touch()
        self.config = self.root / "sdkconfig.flash-test.local"
        self.run = self.enterContext(patch.object(flash.subprocess, "run"))
        self.run.return_value.returncode = 7
        self.enterContext(patch.object(flash, "FIRMWARE_DIR", self.root))
        self.enterContext(patch.dict(os.environ, {"IDF_PATH": str(self.sdk)}))
        self.enterContext(redirect_stdout(StringIO()))
        self.enterContext(redirect_stderr(StringIO()))

    def test_missing_confirmation_does_not_write_or_execute(self):
        with self.assertRaises(SystemExit) as result:
            flash.main(["--port", str(self.port)])
        self.assertEqual(result.exception.code, 2)
        self.assertFalse(self.config.exists())
        self.run.assert_not_called()

    def test_ambiguous_ports_stop_before_execution(self):
        with patch.object(flash, "glob", return_value=["/dev/cu.usbmodem1", "/dev/cu.usbmodem2"]):
            with self.assertRaises(SystemExit):
                flash.main(["--confirm-pins"])
        self.assertFalse(self.config.exists())
        self.run.assert_not_called()

    def test_dry_run_does_not_write_or_execute(self):
        self.assertEqual(flash.main(["--dry-run", "--port", str(self.port)]), 0)
        self.assertFalse(self.config.exists())
        self.run.assert_not_called()

    def test_wrong_sdk_stops_before_execution(self):
        self.version.write_text("set(IDF_VERSION_MAJOR 6)\nset(IDF_VERSION_MINOR 1)\n")
        with self.assertRaises(SystemExit):
            flash.main(["--confirm-pins", "--port", str(self.port)])
        self.assertFalse(self.config.exists())
        self.run.assert_not_called()

    def test_camera_only_config_and_failure_exit_code(self):
        self.assertEqual(flash.main(["--confirm-pins", "--port", str(self.port)]), 7)
        self.assertEqual(self.config.read_text(),
                         "CONFIG_FLOTSAM_CAMERA_TEST=y\nCONFIG_FLOTSAM_CAMERA_PINMAP_CONFIRMED=y\n")
        command = self.run.call_args.args[0]
        self.assertEqual(command[-2:], [str(self.sdk.resolve()), str(self.port)])
        self.assertIn("build &&", command[2])
        self.assertTrue(command[2].endswith('"$2" flash monitor'))

    def test_wifi_mode_keeps_password_and_uses_own_config(self):
        config = self.root / "sdkconfig.camera-wifi.local"
        args = ["--wifi", "--confirm-pins", "--port", str(self.port)]
        self.assertEqual(flash.main(args), 7)
        first = config.read_text()
        self.assertIn("CONFIG_FLOTSAM_CAMERA_WIFI=y", first)
        self.assertNotIn("CONFIG_FLOTSAM_CAMERA_TEST=y", first)
        self.assertRegex(first, r'CONFIG_FLOTSAM_AP_PASSWORD="[A-Za-z0-9_-]{24}"')
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(flash.main(args), 7)
        self.assertEqual(config.read_text(), first)
        self.assertIn("build-camera-wifi", self.run.call_args.args[0][2])
        self.assertFalse(self.config.exists())

    def test_wifi_dry_run_does_not_create_credentials(self):
        self.assertEqual(flash.main(["--wifi", "--dry-run", "--port", str(self.port)]), 0)
        self.assertFalse((self.root / "sdkconfig.camera-wifi.local").exists())
        self.run.assert_not_called()

    def test_shell_flashes_only_after_successful_build(self):
        self.sdk.joinpath("export.sh").write_text(
            'idf.py() { printf "%s\\n" "$*" >> "$FLASH_TEST_CALLS"; return "$FLASH_TEST_RESULT"; }\n'
        )
        flash.main(["--confirm-pins", "--port", str(self.port)])
        command = self.run.call_args.args[0]
        for build_result in (0, 9):
            with self.subTest(build_result=build_result):
                log = self.root / f"calls-{build_result}"
                env = dict(os.environ, FLASH_TEST_CALLS=str(log), FLASH_TEST_RESULT=str(build_result))
                result = REAL_RUN(command, cwd=self.root, env=env, capture_output=True)
                self.assertEqual(result.returncode, build_result)
                calls = log.read_text().splitlines()
                self.assertTrue(calls[0].endswith(" build"))
                self.assertEqual(len(calls), 2 if build_result == 0 else 1)
                if build_result == 0:
                    self.assertTrue(calls[1].endswith(" flash monitor"))


if __name__ == "__main__":
    unittest.main()
