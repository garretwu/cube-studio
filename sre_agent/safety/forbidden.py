"""Forbidden operation patterns inherited from fault injector design."""

from __future__ import annotations

import re

FORBIDDEN_OPERATIONS = [
    r"rm\s+-rf\s+/",
    r"kubectl\s+delete\s+namespace",
    r"factory_reset",
    r"delete_etcd",
    r"format_disk",
    r"reboot\b",
    r"reload\b",
]


def contains_forbidden_operation(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in FORBIDDEN_OPERATIONS)
