from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatchcase
from typing import Any, Iterable


class SkillDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class SkillPolicyResult:
    decision: SkillDecision
    reason: str = ""


class SkillPolicy:
    """Execution policy for skill scripts."""

    def __init__(
        self,
        *,
        default_decision: SkillDecision | str = SkillDecision.ALLOW,
        deny: Iterable[str] | None = None,
        ask: Iterable[str] | None = None,
    ) -> None:
        self.default_decision = self._normalize(default_decision)
        self.deny_patterns = tuple(str(item).strip() for item in (deny or []) if str(item).strip())
        self.ask_patterns = tuple(str(item).strip() for item in (ask or []) if str(item).strip())

    def evaluate(
        self,
        *,
        skill_id: str,
        script: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> SkillPolicyResult:
        _ = context
        target = f"{skill_id}:{script}" if script else skill_id
        for pattern in self.deny_patterns:
            if fnmatchcase(target, pattern) or fnmatchcase(skill_id, pattern):
                return SkillPolicyResult(
                    decision=SkillDecision.DENY,
                    reason=f"skill execution denied by policy pattern: {pattern}",
                )
        for pattern in self.ask_patterns:
            if fnmatchcase(target, pattern) or fnmatchcase(skill_id, pattern):
                return SkillPolicyResult(
                    decision=SkillDecision.ASK,
                    reason=f"skill execution requires approval due to policy pattern: {pattern}",
                )
        return SkillPolicyResult(decision=self.default_decision, reason="default skill execution policy")

    @staticmethod
    def _normalize(value: SkillDecision | str) -> SkillDecision:
        if isinstance(value, SkillDecision):
            return value
        text = str(value).strip().lower()
        for candidate in SkillDecision:
            if candidate.value == text:
                return candidate
        raise ValueError(f"unknown skill decision: {value!r}")
