"""Shared channel abstractions for cross-component reuse."""

from lib.fchannels.base import BaseChannel, ChannelResult, SafetyViolationError
from lib.fchannels.cube_studio import CubeStudioChannel, build_auth_header
from lib.fchannels.ipmi import IPMIChannel
from lib.fchannels.kubernetes import K8sChannel
from lib.fchannels.prometheus import PrometheusChannel
from lib.fchannels.redfish import RedfishChannel
from lib.fchannels.ssh import SSHChannel
from lib.fchannels.switch import SwitchChannel

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
