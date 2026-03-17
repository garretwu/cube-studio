"""Runbook contracts and matching helpers."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Runbook:
    title: str
    symptom: str
    root_cause: str
    action: str
    tags: list[str] = field(default_factory=list)
    source: str = ""

    def to_document(self) -> str:
        tags = ", ".join(self.tags)
        return (
            f"title: {self.title}\n"
            f"symptom: {self.symptom}\n"
            f"root_cause: {self.root_cause}\n"
            f"action: {self.action}\n"
            f"tags: {tags}"
        )


@dataclass(frozen=True)
class RunbookMatch:
    runbook: Runbook
    score: float
