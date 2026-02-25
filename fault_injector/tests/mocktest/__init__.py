"""
Mock-based unit tests for Fault Injector.

This package contains unit tests that use mocks to test components
without requiring actual SSH connections or hardware.

Structure:
- conftest.py: Shared pytest fixtures
- test_base.py: Base classes for tests (extensible)
- test_ssh_channel.py: SSH Channel unit tests
- test_scenarios.py: Scenario unit tests

To run tests:
    pytest fault_injector/tests/mocktest/ -v
    
To run with coverage:
    pytest fault_injector/tests/mocktest/ -v --cov=fault_injector
"""
from fault_injector.tests.mocktest.conftest import (
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