"""Channel package."""

from fault_injector.channels.base import BaseChannel, ChannelResult
from fault_injector.channels.ssh import SSHChannel
from fault_injector.channels.redfish import RedfishChannel

try:
    from fault_injector.channels.prometheus import PrometheusChannel
except Exception:  # optional dependency import guard
    PrometheusChannel = None  # type: ignore[assignment]

try:
    from fault_injector.channels.switch import SwitchChannel
except Exception:
    SwitchChannel = None  # type: ignore[assignment]

try:
    from fault_injector.channels.kubernetes import K8sChannel
except Exception:
    K8sChannel = None  # type: ignore[assignment]

__all__ = [
    "BaseChannel",
    "ChannelResult",
    "SSHChannel",
    "RedfishChannel",
    "PrometheusChannel",
    "SwitchChannel",
    "K8sChannel",
]
