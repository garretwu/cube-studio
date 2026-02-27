"""Prompt pool used by inference agent."""
from __future__ import annotations

import random

from load_simulator.load.token_distribution import TokenDistribution


class PromptPool:
    """Pre-generates prompts to avoid generating strings on hot request path."""

    def __init__(
        self,
        size: int = 100,
        *,
        mode: str = "mixed",
        mixed_weights: dict[str, float] | None = None,
        seed: int | None = 7,
    ) -> None:
        if size <= 0:
            raise ValueError("Prompt pool size must be positive")
        self._rng = random.Random(seed)
        self._tokens = TokenDistribution(mode=mode, mixed_weights=mixed_weights, seed=seed)
        self._prompts = [self._build_prompt(i) for i in range(size)]

    def _build_prompt(self, idx: int) -> str:
        token_count = self._tokens.sample()
        word_count = max(8, token_count // 2)
        words = [f"token{(idx + i) % 97}" for i in range(word_count)]
        return (
            "You are a platform assistant. "
            "Analyze system health and summarize risks. "
            + " ".join(words)
        )

    def sample(self) -> str:
        return self._rng.choice(self._prompts)

    def all(self) -> list[str]:
        return list(self._prompts)

    def __len__(self) -> int:
        return len(self._prompts)
