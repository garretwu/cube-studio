"""
Compatibility package for legacy imports.

The mock tests were reorganized into:
- fault_injector/tests/unit/features/channels/
- fault_injector/tests/unit/features/scenarios/
- fault_injector/tests/helpers/
"""
from fault_injector.tests.conftest import (
    mock_ssh_config,
    mock_target_node,
    mock_inventory,
    temp_dir,
    rollback_journal,
    safety_guard,
    mock_ssh_channel,
    mock_fault_context,
)

__all__ = [
    "mock_ssh_config",
    "mock_target_node",
    "mock_inventory",
    "temp_dir",
    "rollback_journal",
    "safety_guard",
    "mock_ssh_channel",
    "mock_fault_context",
]
