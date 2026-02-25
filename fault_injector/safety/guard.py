from __future__ import annotations

from fault_injector.orchestrator.session import FaultStep


class SafetyGuard:
    """Intercept unsafe commands before execution."""

    def validate_step(self, step: FaultStep) -> None:
        if step.inject_command and not step.inject_command.startswith("sudo ip link set dev "):
            raise ValueError(f"unsafe inject command: {step.inject_command}")
        if not step.rollback_command.startswith("sudo ip link set dev "):
            raise ValueError(f"unsafe rollback command: {step.rollback_command}")
