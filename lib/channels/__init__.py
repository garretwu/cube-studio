"""Shared channel abstractions for cross-component reuse."""

from lib.channels.base import BaseChannel, ChannelResult, SafetyViolationError
from lib.channels.cube_studio import CubeStudioChannel, build_auth_header
from lib.channels.ipmi import IPMIChannel
from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel
from lib.channels.redfish import RedfishChannel
from lib.channels.ssh import SSHChannel
from lib.channels.switch import SwitchChannel

__all__ = [
    "BaseChannel",
    "ChannelResult",
    "CubeStudioChannel",
    "IPMIChannel",
    "K8sChannel",
    "PrometheusChannel",
    "RedfishChannel",
    "SSHChannel",
    "SwitchChannel",
    "SafetyViolationError",
    "build_auth_header",
]
