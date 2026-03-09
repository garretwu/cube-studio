from __future__ import annotations

import tempfile
import unittest

from load_simulator.orchestrator.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_start_update_complete_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            sid = "abc123"
            store.start(
                session_id=sid,
                mode="stress",
                selected_agents=["inference"],
                preflight={"inference_endpoint": {"ok": True}},
                plan=[{"name": "baseline"}],
                resumed=False,
            )
            store.update_stage(
                sid,
                stage_name="baseline",
                stage_index=0,
                agent_results=[{"name": "inference", "status": "success", "metrics": {"x": 1}}],
                adaptive_event={"stage": "baseline", "action": "CONTINUE"},
                system_metrics={"cpu_util_pct": 50.0},
                breaking_point=None,
            )
            store.complete(sid, status="completed", summary="ok", bottlenecks=[])
            data = store.load(sid)
            self.assertEqual(data["status"], "completed")
            self.assertEqual(data["current_stage_index"], 1)
            self.assertEqual(data["agent_results"][0]["name"], "inference")


if __name__ == "__main__":
    unittest.main()
