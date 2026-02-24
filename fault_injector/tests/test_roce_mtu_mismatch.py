from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fault_injector.config import load_config
from fault_injector.injector import FaultInjector


class RoceMtuMismatchTests(unittest.TestCase):
    def test_inject_and_rollback_commands_and_simulated_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wal_path = Path(td) / "rollback.wal"
            conf_path = Path(td) / "injector.conf.json"
            conf_path.write_text(
                """{
  "injector": {
    "mode": "simulate",
    "wal_path": "%s",
    "timeout": 20
  },
  "servers": [
    {
      "name": "node-a",
      "host": "127.0.0.1",
      "user": "root",
      "port": 22,
      "interface": "eth0",
      "original_mtu": 4200,
      "fault_mtu": 1500
    },
    {
      "name": "node-b",
      "host": "127.0.0.2",
      "user": "root",
      "port": 22,
      "interface": "eth0",
      "original_mtu": 4200,
      "fault_mtu": 9000
    }
  ]
}""" % wal_path,
                encoding="utf-8",
            )


            injector = FaultInjector(load_config(str(conf_path)), session_id="t1")
            reports = injector.inject_roce_mtu_mismatch()

            self.assertEqual(2, len(reports))
            self.assertIn("mtu 1500", reports[0].inject_command)
            self.assertIn("mtu 9000", reports[1].inject_command)
            self.assertEqual(1500, injector.channel.get_simulated_mtu("node-a", "eth0"))
            self.assertEqual(9000, injector.channel.get_simulated_mtu("node-b", "eth0"))
            self.assertTrue(wal_path.exists())

            rollback_reports = injector.rollback()
            self.assertEqual(2, len(rollback_reports))
            self.assertEqual(4200, injector.channel.get_simulated_mtu("node-a", "eth0"))
            self.assertEqual(4200, injector.channel.get_simulated_mtu("node-b", "eth0"))


if __name__ == "__main__":
    unittest.main()
