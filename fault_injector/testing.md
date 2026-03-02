# Fault Injector Testing Strategy

## Stack
- Unit and integration: `pytest`
- Async support: `pytest-asyncio`
- Coverage: `pytest-cov`

## Test Layout
- Unit: `fault_injector/tests/unit/features/`
- Integration: `fault_injector/tests/integration/`
- E2E placeholders: `fault_injector/tests/e2e/`
- Shared fixtures: `fault_injector/tests/fixtures/`
- Shared mocks/helpers: `fault_injector/tests/mocks/`, `fault_injector/tests/helpers/`

## Naming Conventions
- Files: `test_<feature>.py`
- Async tests: `@pytest.mark.asyncio`
- Test names: `test_<expected_behavior>_<condition>()`

## Minimum Test Expectations For Scenario Changes
- Happy path: inject -> recover -> verify.
- Guard behavior: blocked command path returns failed result.
- Failure path: recovery failures must mark WAL entry as `failed`.
- Dry-run behavior: should remain supported and deterministic.
- Monitor query contract: `monitor_queries()` returns non-empty dict for scenario metrics.

## What Not To Test
- Third-party library internals (`asyncssh`, `httpx`, `ncclient`).
- Private helpers with no behavior impact.
- Trivial passthroughs without branch logic.

## Validation Command Matrix
- Scenario unit tests:
  - `pytest fault_injector/tests/unit/features/scenarios/test_rdma_anomaly.py -q`
  - `pytest fault_injector/tests/unit/features/scenarios/test_scenarios.py -q`
  - `pytest fault_injector/tests/unit/features/scenarios/test_vllm_latency_hardening.py -q`
- Orchestrator unit tests:
  - `pytest fault_injector/tests/unit/features/orchestrator/test_engine.py -q`
- Broad unit gate:
  - `pytest fault_injector/tests/unit -q`
- CLI/config sanity:
  - `python -m fault_injector validate-config fault_injector/fault-injector-test.yaml`
  - `python -m fault_injector list-scenarios`
- Optional dry-run smoke:
  - `python -m fault_injector run --config fault_injector/fault-injector-test.yaml --dry-run`

## Optional Integration Gate
- Switch live integration tests are opt-in and require dedicated lab config:
  - `pytest fault_injector/tests/integration -q`
- Use markers/prerequisites in integration modules (for example `live_netconf`) to avoid accidental live execution.

## Quality Guardrails
- Do not commit `.only`/`.skip` style focused tests.
- Keep assertions specific and behavior-oriented.
- Keep fixture data centralized; avoid duplicating setup in each test.

## RC-6 Real Inject Validation (Thermal Throttling)
- Date: `2026-03-02`
- Config mode: `thermal_throttling.params.fan_control_backend: "ipmi"`
- Test command:
  - `python -m fault_injector run --config fault_injector/fault-injector-test.yaml --scenario thermal_throttling -y`

### Expected pass criteria
- `inject_success: true`
- `recover_success: true`
- `verified: true`
- Session event `bmc_precheck_completed` exists and contains:
  - `fan_injected_backend: "ipmi"`
  - `fan_inject_applied: true`
  - `fan_inject_warning: ""` (empty or absent warning)

### Last verified session
- Session ID: `d022cf8d`
- Result: pass (`inject/recover/verify` all true)
- Evidence file:
  - `fault-reports/sessions/d022cf8d/session.json`

### Common failure patterns and fixes
- If error contains `pyghmi is not installed`:
  - Install dependency: `python -m pip install pyghmi`
- If Redfish post-inject check returns `401 Unauthorized`:
  - Current code already retries with re-auth for RC-6 post-check.
- If IPMI raw command serialization fails with `bytearray is not JSON serializable`:
  - Current `IPMIChannel` already converts `bytes/bytearray` to JSON-safe lists.
