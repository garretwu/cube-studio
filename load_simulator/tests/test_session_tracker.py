"""Tests for load_simulator.orchestrator.session."""
from __future__ import annotations

import unittest

from load_simulator.orchestrator.session import SessionEvent, SessionState, SessionTracker


class SessionStateTests(unittest.TestCase):
    def test_enum_values(self):
        self.assertEqual(SessionState.CREATED.value, "created")
        self.assertEqual(SessionState.RUNNING.value, "running")
        self.assertEqual(SessionState.COMPLETED.value, "completed")
        self.assertEqual(SessionState.FAILED.value, "failed")
        self.assertEqual(SessionState.CANCELLED.value, "cancelled")


class SessionTrackerTests(unittest.TestCase):
    def test_initial_state(self):
        tracker = SessionTracker()
        self.assertEqual(tracker.state, SessionState.CREATED)
        self.assertIsNone(tracker.start_time)
        self.assertIsNone(tracker.end_time)
        self.assertFalse(tracker.is_terminal)
        self.assertEqual(tracker.elapsed_seconds, 0.0)

    def test_start(self):
        tracker = SessionTracker()
        tracker.start()
        self.assertEqual(tracker.state, SessionState.RUNNING)
        self.assertIsNotNone(tracker.start_time)
        self.assertFalse(tracker.is_terminal)
        self.assertGreater(tracker.elapsed_seconds, 0.0)

    def test_complete(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.complete()
        self.assertEqual(tracker.state, SessionState.COMPLETED)
        self.assertIsNotNone(tracker.end_time)
        self.assertTrue(tracker.is_terminal)

    def test_fail(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.fail("something broke")
        self.assertEqual(tracker.state, SessionState.FAILED)
        self.assertTrue(tracker.is_terminal)
        self.assertTrue(any("something broke" in e.detail for e in tracker.events))

    def test_cancel(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.cancel()
        self.assertEqual(tracker.state, SessionState.CANCELLED)
        self.assertTrue(tracker.is_terminal)

    def test_agent_lifecycle(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.agent_started("inference")
        self.assertEqual(tracker.agent_states["inference"], "running")
        tracker.agent_finished("inference", "success")
        self.assertEqual(tracker.agent_states["inference"], "success")

    def test_events_recorded(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.agent_started("pipeline")
        tracker.agent_finished("pipeline", "error")
        tracker.complete()
        event_types = [e.event_type for e in tracker.events]
        self.assertIn("session_started", event_types)
        self.assertIn("agent_started", event_types)
        self.assertIn("agent_finished", event_types)
        self.assertIn("session_completed", event_types)

    def test_to_dict(self):
        tracker = SessionTracker(session_id="test-123")
        tracker.start()
        tracker.agent_started("inference")
        tracker.complete()
        d = tracker.to_dict()
        self.assertEqual(d["session_id"], "test-123")
        self.assertEqual(d["state"], "completed")
        self.assertIn("elapsed_seconds", d)
        self.assertIn("agent_states", d)
        self.assertIn("event_count", d)


if __name__ == "__main__":
    unittest.main()
