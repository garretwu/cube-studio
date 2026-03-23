"""Optional NeMo guardrails layer for the SRE agent."""

from sre_agent.guardrails.actions import sanitize_tool_output, validate_tool_input
from sre_agent.guardrails.runtime import (
    GuardrailsDependencyMissingError,
    GuardrailsRuntime,
    load_guardrails_runtime,
)

__all__ = [
    "GuardrailsDependencyMissingError",
    "GuardrailsRuntime",
    "load_guardrails_runtime",
    "validate_tool_input",
    "sanitize_tool_output",
]
