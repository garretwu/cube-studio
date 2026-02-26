"""
Compatibility package for legacy imports.

The mock tests were reorganized into:
- fault_injector/tests/channel/
- fault_injector/tests/scenario/
- fault_injector/tests/common/
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
