from __future__ import annotations

import unittest

from load_simulator.orchestrator.preflight import failed_checks, has_blocking_failure


class PreflightPolicyTests(unittest.TestCase):
    def test_failed_checks(self) -> None:
        preflight = {
            "a": {"ok": True},
            "b": {"ok": False},
            "c": {"ok": None},
        }
        self.assertEqual(failed_checks(preflight), ["b"])
        self.assertTrue(has_blocking_failure(preflight))

    def test_empty_preflight(self) -> None:
        self.assertEqual(failed_checks({}), [])
        self.assertFalse(has_blocking_failure({}))


if __name__ == "__main__":
    unittest.main()
