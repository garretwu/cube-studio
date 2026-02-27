"""Shared channel abstractions for cross-component reuse."""

from lib.channels.base import BaseChannel, ChannelResult, SafetyViolationError
from lib.channels.cube_studio import CubeStudioChannel, build_auth_header
from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel

__all__ = [
    "BaseChannel",
    "ChannelResult",
    "CubeStudioChannel",
    "K8sChannel",
    "PrometheusChannel",
    "SafetyViolationError",
    "build_auth_header",
]
