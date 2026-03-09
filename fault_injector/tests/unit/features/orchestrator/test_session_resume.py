from __future__ import annotations

from datetime import datetime

import pytest

from fault_injector.orchestrator.engine import FaultOrchestrator
from fault_injector.orchestrator.session import ActiveFault, Session, SessionPhase
from fault_injector.safety.rollback import RollbackJournal


@pytest.mark.asyncio
async def test_resume_recover_only_marks_session_recovered(tmp_path):
    session = Session.create(config_hash="abc", session_dir=str(tmp_path))
    session.set_phase(SessionPhase.OBSERVE)
    session.add_active_fault(
        ActiveFault(
            fault_id="f-1",
            scenario_name="network_jitter",
            target_node="node-1",
            injected_at=datetime.now(),
            params={},
        )
    )
    session.save(str(tmp_path))

    rollback = RollbackJournal(tmp_path / session.session_id / "rollback.jsonl")
    rollback.record(
        fault_id="f-1",
        channel="ssh",
        target="node-1",
        inject_action="tc_add_delay",
        inject_params={},
        recover_action="tc_del_qdisc",
        recover_params={},
    )

    resumed = await FaultOrchestrator.resume(session.session_id, session_dir=str(tmp_path))

    assert resumed.status.value == "recovered"
    assert resumed.active_faults == []
