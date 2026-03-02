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
