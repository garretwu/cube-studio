from __future__ import annotations

import re

TTFT_STRICT_PROCESS_FIND_PATTERN = (
    r"load_simulator|stress-ng|stress|wrk|locust|jmeter|fio|iperf|apachebench|\bhey\b"
)

_TTFT_SUSPECT_ALLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bload_simulator\b", re.IGNORECASE),
    re.compile(r"\bstress-ng\b", re.IGNORECASE),
    re.compile(r"\bstress\b", re.IGNORECASE),
    re.compile(r"\bwrk\b", re.IGNORECASE),
    re.compile(r"\blocust\b", re.IGNORECASE),
    re.compile(r"\bjmeter\b", re.IGNORECASE),
    re.compile(r"\bfio\b", re.IGNORECASE),
    re.compile(r"\biperf(?:3)?\b", re.IGNORECASE),
    re.compile(r"\bapachebench\b", re.IGNORECASE),
    re.compile(r"\bab\b", re.IGNORECASE),
    re.compile(r"\bhey\b", re.IGNORECASE),
)

_TTFT_SUSPECT_DENY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"prometheus[-_]?config[-_]?reloader", re.IGNORECASE),
    re.compile(r"\breload(?:er)?\b", re.IGNORECASE),
    re.compile(r"\buvicorn\b", re.IGNORECASE),
    re.compile(r"\bbackend\b", re.IGNORECASE),
    re.compile(r"\bcontainerd-shim\b", re.IGNORECASE),
    re.compile(r"\bkube-proxy\b", re.IGNORECASE),
    re.compile(r"\bloki-canary\b", re.IGNORECASE),
)

_TTFT_VERIFICATION_TOKEN_ALLOWLIST: tuple[str, ...] = (
    "load_simulator",
    "stress-ng",
    "stress",
    "wrk",
    "locust",
    "jmeter",
    "fio",
    "iperf",
    "apachebench",
    "hey",
)


def is_ttft_suspect_process(process_text: str) -> bool:
    normalized = str(process_text or "").strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(pattern.search(lowered) for pattern in _TTFT_SUSPECT_DENY_PATTERNS):
        return False
    return any(pattern.search(lowered) for pattern in _TTFT_SUSPECT_ALLOW_PATTERNS)


def extract_ttft_verification_pattern(process_text: str) -> str | None:
    lowered = str(process_text or "").strip().lower()
    if not lowered:
        return None
    for token in _TTFT_VERIFICATION_TOKEN_ALLOWLIST:
        if token in lowered:
            return token
    return None

