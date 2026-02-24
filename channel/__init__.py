"""Channel abstractions for fault injector."""

from .base import CommandResult
from .ssh import SSHChannel

__all__ = ["CommandResult", "SSHChannel"]
