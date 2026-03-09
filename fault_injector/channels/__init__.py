"""Compatibility imports for channels now hosted under lib.channels."""

from lib.channels import (
    BaseChannel,
    ChannelResult,
    IPMIChannel,
    K8sChannel,
    PrometheusChannel,
    RedfishChannel,
    SSHChannel,
    SwitchChannel,
    SafetyViolationError,
)

__all__ = [
    "BaseChannel",
    "ChannelResult",
    "K8sChannel",
    "IPMIChannel",
    "PrometheusChannel",
    "RedfishChannel",
    "SSHChannel",
    "SwitchChannel",
    "SafetyViolationError",
]
