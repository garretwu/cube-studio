from __future__ import annotations


class DiagnosisAgent:
    """Diagnosis data collector/annotator entrypoint (read-only)."""

    def analyze(self) -> dict[str, object]:
        return {}
