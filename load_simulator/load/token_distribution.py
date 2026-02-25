"""Token-length sampling strategies for inference load."""
from __future__ import annotations

import random
from dataclasses import dataclass


_RANGES = {
    "short": (10, 50),
    "medium": (100, 500),
    "long": (500, 2000),
}


@dataclass
class TokenDistribution:
    mode: str = "mixed"
    mixed_weights: dict[str, float] | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        if self.mode not in {"short", "medium", "long", "mixed"}:
            raise ValueError(f"Unsupported token distribution mode: {self.mode}")
        if self.mode == "mixed":
            base = {"short": 0.3, "medium": 0.5, "long": 0.2}
            user = self.mixed_weights or {}
            weights = {
                "short": float(user.get("short", base["short"])),
                "medium": float(user.get("medium", base["medium"])),
                "long": float(user.get("long", base["long"])),
            }
            total = sum(max(v, 0.0) for v in weights.values())
            if total <= 0:
                raise ValueError("Mixed distribution weights must sum to a positive number")
            self._weights = {k: max(v, 0.0) / total for k, v in weights.items()}
        else:
            self._weights = None

    def sample(self) -> int:
        if self.mode in _RANGES:
            low, high = _RANGES[self.mode]
            return self._rng.randint(low, high)

        assert self._weights is not None
        bucket = self._rng.choices(
            population=["short", "medium", "long"],
            weights=[self._weights["short"], self._weights["medium"], self._weights["long"]],
            k=1,
        )[0]
        low, high = _RANGES[bucket]
        return self._rng.randint(low, high)

    def sample_many(self, count: int) -> list[int]:
        return [self.sample() for _ in range(max(0, count))]
