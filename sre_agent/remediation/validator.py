"""Validation of remediation plans before execution."""

from __future__ import annotations

from typing import Any

from sre_agent.models.remediation import RemediationPlan
from sre_agent.safety.blast_radius import BlastRadiusPolicy, ensure_within_blast_radius
from sre_agent.safety.forbidden import contains_forbidden_operation
from sre_agent.tools import ToolRegistry


class PlanValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class PlanValidator:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        ontology: Any | None = None,
        blast_radius_policy: BlastRadiusPolicy | None = None,
    ) -> None:
        self.tools = tool_registry
        self.ontology = ontology
        self.blast_radius_policy = blast_radius_policy or BlastRadiusPolicy()

    def validate(self, plan: RemediationPlan) -> list[str]:
        errors: list[str] = []
        read_tool_names = {tool.name for tool in self.tools.get_tools_by_level("read_only")}
        write_tool_names = {
            tool.name
            for tool in self.tools.list_tools()
            if tool.safety_level.value != "read_only"
        }

        entity_ids: list[str] = []
        for step in plan.steps:
            if step.tool not in write_tool_names:
                errors.append(f"step {step.step_id}: tool {step.tool!r} not found in write registry")
                continue
            tool_def = self.tools.get_tool(step.tool)
            required = list(tool_def.params_schema.get("required", []))
            missing = [field for field in required if field not in step.params]
            if missing:
                errors.append(f"step {step.step_id}: missing params {missing}")
            if step.rollback_tool and step.rollback_tool not in write_tool_names:
                errors.append(f"step {step.step_id}: rollback_tool {step.rollback_tool!r} not found")
            if step.verification.tool and step.verification.tool not in read_tool_names:
                errors.append(f"step {step.step_id}: verification tool {step.verification.tool!r} not found")
            if contains_forbidden_operation(step.description):
                errors.append(f"step {step.step_id}: forbidden operation in description")
            for key in ("node", "entity_id", "service_id", "switch", "bmc_host"):
                value = step.params.get(key)
                if isinstance(value, str) and value.strip():
                    entity_ids.append(value.strip())

        if self.ontology is not None and entity_ids:
            try:
                ensure_within_blast_radius(self.ontology, sorted(set(entity_ids)), self.blast_radius_policy)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        return errors
