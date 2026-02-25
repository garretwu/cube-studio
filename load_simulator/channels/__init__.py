"""Channel package for external-system integrations."""

from load_simulator.channels.base import BaseChannel, ChannelResult, SafetyViolationError
from load_simulator.channels.cube_studio import CubeStudioChannel, build_auth_header
from load_simulator.channels.inference import InferenceChannel, InferenceResult
from load_simulator.channels.kubernetes import K8sChannel
from load_simulator.channels.notebook import NotebookChannel
from load_simulator.channels.prometheus import PrometheusChannel

__all__ = [
    "BaseChannel",
    "ChannelResult",
    "CubeStudioChannel",
    "InferenceChannel",
    "InferenceResult",
    "K8sChannel",
    "NotebookChannel",
    "PrometheusChannel",
    "SafetyViolationError",
    "build_auth_header",
]
