from __future__ import annotations


def estimate_token_count(text: str) -> int:
    compact = " ".join(str(text or "").split())
    if not compact:
        return 0
    return max(1, len(compact) // 4)
