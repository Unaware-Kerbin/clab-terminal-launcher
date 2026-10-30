#!/usr/bin/env python3
"""Payload tests for the Late launcher. No daemon required."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import late_launch  # noqa: E402


class LateLaunchTests(unittest.TestCase):
    def test_build_params_keeps_password_out_of_the_method_name(self):
        params = late_launch.build_params(
            host="192.0.2.10",
            ssh_user="lab",
            device_user="admin",
            devices=[{"long": "clab-lab-pe-1", "short": "pe-1", "kind": "linux", "ip": "172.20.20.2"}],
            no_jump=False,
            socks_port=1080,
            control_path="/tmp/clab-control",
            device_password="device-secret",
        )
        rpc = late_launch.build_rpc(params)
        self.assertEqual(rpc["method"], "clab.open")
        encoded = json.dumps(rpc)
        self.assertIn("device-secret", encoded)
        self.assertNotIn("device-secret", rpc["method"])
        self.assertEqual(rpc["params"]["socks_port"], 1080)
        self.assertEqual(rpc["params"]["ssh_user"], "lab")

    def test_password_omitted_when_empty(self):
        params = late_launch.build_params(
            host="192.0.2.10",
            ssh_user="lab",
            device_user="admin",
            devices=[{"long": "n", "short": "n", "kind": "linux", "ip": "172.20.20.2"}],
            no_jump=True,
            socks_port=0,
            control_path="",
            device_password="",
        )
        self.assertNotIn("device_password", params)
        self.assertNotIn("socks_port", params)
        self.assertTrue(params["no_jump"])

    def test_config_dir_honors_override(self):
        old = os.environ.get("LATE_CONFIG")
        os.environ["LATE_CONFIG"] = "/tmp/late-cfg-test"
        try:
            self.assertEqual(late_launch.token_path(), Path("/tmp/late-cfg-test/sidecar.token"))
        finally:
            if old is None:
                os.environ.pop("LATE_CONFIG", None)
            else:
                os.environ["LATE_CONFIG"] = old

    def test_probe_prints_without_raising_when_daemon_is_down(self):
        old = os.environ.get("LATE_DAEMON_URL")
        os.environ["LATE_DAEMON_URL"] = "http://127.0.0.1:1"
        try:
            self.assertFalse(late_launch.daemon_up(timeout=0.2))
        finally:
            if old is None:
                os.environ.pop("LATE_DAEMON_URL", None)
            else:
                os.environ["LATE_DAEMON_URL"] = old


if __name__ == "__main__":
    unittest.main()
