"""Compatibility imports for channels now hosted under lib.channels."""

from lib.channels import (
    BaseChannel,
    K8sChannel,
    PrometheusChannel,
    RedfishChannel,
    SSHChannel,
    SwitchChannel,
)
from fault_injector.config.schema import ChannelResult
from fault_injector.safety.guard import SafetyViolationError

__all__ = [
    "BaseChannel",
    "ChannelResult",
    "K8sChannel",
    "PrometheusChannel",
    "RedfishChannel",
    "SSHChannel",
    "SwitchChannel",
    "SafetyViolationError",
]
