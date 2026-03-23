"""Adapter around the fault injector SafetyGuard pattern."""

from __future__ import annotations

from dataclasses import dataclass, field

from fault_injector.config.schema import SafetyConfig
from fault_injector.safety.guard import SafetyGuard


@dataclass
class SRESafetyConfig:
    require_confirmation: bool = True
    auto_recover_timeout: int = 600
    dry_run: bool = False
    max_concurrent_remediations: int = 2
    excluded_nodes: list[str] = field(default_factory=list)

    def to_fault_injector_config(self) -> SafetyConfig:
        return SafetyConfig(
            require_confirmation=self.require_confirmation,
            auto_recover_timeout=self.auto_recover_timeout,
            dry_run=self.dry_run,
            max_concurrent_faults=self.max_concurrent_remediations,
            excluded_nodes=self.excluded_nodes,
        )


class SafetyGuardAdapter:
    """Small adapter that exposes fault-injector safety semantics to Agent C."""

    def __init__(self, config: SRESafetyConfig | None = None) -> None:
        self.config = config or SRESafetyConfig()
        self._guard = SafetyGuard(self.config.to_fault_injector_config())

    def check_command(self, command: str, channel: str = "ssh") -> None:
        self._guard.check_command(command, channel=channel)

    def check_path(self, path: str, allowed_paths: set[str] | None = None) -> None:
        self._guard.check_path(path, allowed_paths=allowed_paths)

    def check_node(self, node: str) -> None:
        self._guard.check_node(node)

    def should_confirm(self, action: str) -> bool:
        return self._guard.should_confirm(action)

    @property
    def allowed_log_paths(self) -> set[str]:
        return set(self._guard.ALLOWED_LOG_PATHS)
