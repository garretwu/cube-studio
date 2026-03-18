# SRE Agent Models Contract (`sre_agent/models`)

## 1. Function of `sre_agent/models`

`sre_agent/models` is the shared source-of-truth contract layer for the Auto-SRE system.

- It defines stable typed schemas exchanged across diagnosis, remediation, API/WebSocket, ontology, and memory subsystems.
- It enforces strict validation (`extra="forbid"`) and frozen model instances (`frozen=True`) to reduce accidental contract drift.
- It preserves compatibility for known naming mismatches via alias support (for example `startsAt/endsAt`, `actions -> steps`, `payload -> data`, and legacy safety-level strings).

This module is intended to be reused by all downstream agents without modification in normal development.

## 2. Code Architecture

### 2.1 Contract base and common envelope

- `common.py`: `StrictFrozenModel`, `ErrorCode`, `SafetyLevel` (with legacy mapping), `SREError`, `SREResponse[T]`.
- Purpose: one consistent validation/freeze policy and one response/error envelope for all domains.

### 2.2 Domain model modules

- `alert.py`: `Alert`, `AlertSeverity`, `AlertStatus` with Alertmanager-compatible aliases.
- `diagnosis.py`: thinking trace and diagnosis lifecycle (`ThinkingStep`, `Observation`, `ThinkingTrace`, `DiagnosisResult`, `DiagnosisSession`).
- `remediation.py`: structured remediation planning/execution (`RemediationAction`, `RemediationPlan`, `RemediationResult`, `LoopResult`) plus compatibility alias `RemediationStep`.
- `ontology.py`: graph entities/edges (`OntologyNode`, `OntologyEdge`, `EntityType`, `RelationType`) plus compatibility alias `Relationship`.
- `memory.py`: persisted incident/pattern/baseline models (`IncidentRecord`, `LearnedPattern`, `ConfigBaseline`).
- `events.py`: WebSocket contract (`WSEvent`, `EventType`) with `payload -> data` compatibility.

### 2.3 Package export and freeze surface

- `models/__init__.py` explicitly re-exports all public contracts via `__all__`.
- Downstream code should import from `sre_agent.models` or stable submodule symbols only.
- Backward compatibility is handled by aliases rather than breaking field/type changes.

## 3. Test Result Summary

Execution date: **2026-03-17**  
Branch: **`yu/sre-a-foundation`**  
Primary test file: **`sre_agent/tests/test_models.py`**

### 3.1 Commands and outcomes

1. `python -m pytest sre_agent/tests/test_models.py -q`  
   Result: **PASS** (`22 passed in 0.22s`)

2. `python -m pytest sre_agent/tests/test_models.py -k "unit or integration or e2e" -q`  
   Result: **PASS** (`22 passed in 0.14s`)

### 3.2 Coverage focus in this file

- Unit: enum validity, strict/forbid-extra behavior, frozen immutability, validator invariants, compatibility alias parsing, per-model round-trip serialization.
- Integration: cross-model composition chain (`Alert -> Diagnosis -> Remediation -> LoopResult -> SREResponse -> WSEvent`) and compatibility alias interoperability.
- E2E (model-only): confirmed success flow, ambiguous/re-diagnosis loop flow, and structured error flow using `SREError + ErrorCode`.

## 4. Change Policy

- Default policy: **no breaking changes** to existing public fields, enum values, or response envelopes.
- Allowed without version bump:
  - Additive optional fields with safe defaults.
  - Alias-based compatibility additions.
  - Non-breaking validator hardening that does not reject previously valid payloads.
- Requires schema version bump and migration note:
  - Field rename/removal.
  - Enum value removal/rename.
  - Response envelope shape change.
  - Backward-incompatible validator constraints.
