from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from sre_agent.skills.registry import SkillDescriptor


class SkillPolicy:
    """Deterministic keyword/tag based skill matching policy."""

    def rank(self, query: str, skills: Iterable[SkillDescriptor], top_k: int = 5) -> list[SkillDescriptor]:
        terms = self._tokenize(query)
        ranked: list[SkillDescriptor] = []
        for skill in skills:
            score = self._score(skill, terms)
            ranked.append(replace(skill, match_score=score))
        # Stable deterministic sort: highest score first, then skill id.
        ranked.sort(key=lambda item: (-item.match_score, item.id))
        return ranked[: max(1, int(top_k))]

    @staticmethod
    def _tokenize(query: str) -> list[str]:
        terms = [part.strip().lower() for part in re.split(r"[^a-zA-Z0-9_:-]+", query or "") if part.strip()]
        return terms

    @staticmethod
    def _score(skill: SkillDescriptor, terms: list[str]) -> float:
        if not terms:
            return 0.0
        name = skill.name.lower()
        summary = skill.summary.lower()
        tags = [tag.lower() for tag in skill.tags]
        haystack = " ".join([name, summary, " ".join(tags)])
        total = 0.0
        matched_tags = 0
        for term in terms:
            if term in tags:
                total += 2.0
                matched_tags += 1
            elif term in name:
                total += 1.5
            elif term in summary:
                total += 1.0
            elif term in haystack:
                total += 0.5
        # Reward skills whose tags align with multiple alert/query concepts,
        # which helps alert-shaped queries prefer reusable diagnosis playbooks.
        if matched_tags >= 2:
            total += 2.0
        if matched_tags >= 3:
            total += 1.0
        joined_terms = " ".join(terms)
        if all(tag in joined_terms for tag in tags[:2]):
            total += 1.0
        max_score = 2.0 * len(terms)
        if max_score <= 0:
            return 0.0
        return round(min(1.0, total / max_score), 4)
