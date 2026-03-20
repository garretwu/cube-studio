from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from sre_agent.skills.registry import SkillDescriptor


class SkillPolicy:
    """Deterministic keyword/tag based skill matching policy."""

    def rank(self, query: str, skills: Iterable[SkillDescriptor], top_k: int = 5) -> list[SkillDescriptor]:
        terms = [part.strip().lower() for part in query.split() if part.strip()]
        ranked: list[SkillDescriptor] = []
        for skill in skills:
            score = self._score(skill, terms)
            ranked.append(replace(skill, match_score=score))
        # Stable deterministic sort: highest score first, then skill id.
        ranked.sort(key=lambda item: (-item.match_score, item.id))
        return ranked[: max(1, int(top_k))]

    @staticmethod
    def _score(skill: SkillDescriptor, terms: list[str]) -> float:
        if not terms:
            return 0.0
        haystack = " ".join([skill.name, skill.summary, " ".join(skill.tags)]).lower()
        total = 0.0
        for term in terms:
            if term in skill.tags:
                total += 2.0
            elif term in skill.name.lower():
                total += 1.5
            elif term in skill.summary.lower():
                total += 1.0
            elif term in haystack:
                total += 0.5
        max_score = 2.0 * len(terms)
        if max_score <= 0:
            return 0.0
        return round(min(1.0, total / max_score), 4)
