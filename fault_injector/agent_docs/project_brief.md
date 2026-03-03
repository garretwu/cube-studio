<!--
=============================================================================
FILE: project_brief.md
PURPOSE: High-level project context for AI agents and contributors
=============================================================================
-->

# Project Brief: Fault Injector

## Quick Summary

What:
- A deterministic multi-layer fault injection system for Cube Studio resilience validation.

Why:
- Reproduce failures safely, validate rollback paths, and measure impact under real or synthetic load.

Who:
- Platform SRE and engineering teams.

Status (2026-03-02):
- Core architecture is implemented and runnable.
- Required vLLM/RDMA scenario families are implemented.
- Fault Injector to Load Simulator integration is partially present by pattern, but not yet standardized as a unified adapter.

## Scope

Fault Injector executes controlled experiments across:
- hardware
- OS
- platform
- service

Design principles:
1. Deterministic orchestration (no LLM in execution-critical path)
2. Safety-first guardrails
3. WAL-backed recovery-first operations
4. Composable scenario/channel abstractions

## Architecture Snapshot

Core modules under `fault_injector/`:
- `cli.py`, `__main__.py`: command entry and orchestration trigger
- `config/`: schema/defaults/loader
- `orchestrator/`: engine, session, scheduler, watchdog
- `scenarios/`: scenario implementations and registry
- `agents/`: layer agents
- `safety/`: safety guard and rollback journal
- `reporting/`: reporting package scaffold

Shared channel integrations in `lib/channels/`:
- SSH, Redfish, Switch(NETCONF), Kubernetes, Prometheus

## Integration with load_simulator

Current state:
- Both CLIs are independently operational.
- fault scenarios can be extended to invoke load simulator runs during fault windows.
- A standardized integration adapter and output contract should be finalized before broad adoption.

Planned contract:
- `fault_injector` scenario invokes:
  - `python -m load_simulator run --config <path> --output-format json [--only ...]`
- parse structured result
- attach summary and artifacts to fault session report
- enforce timeout and failure policy (`strict` / `best-effort`)

See detailed plan:
- `fault_injector/docs/load_simulator_integration_plan.md`

## Current Capabilities

Implemented and in active use:
- scenario execution via orchestrator
- rollback journal + recovery flows
- safety guard checks
- dry-run support
- unit/integration test suites under `fault_injector/tests`

## Constraints

Must:
- keep every injection recoverable
- write rollback intent before risky mutation
- preserve dry-run behavior
- enforce safety guard policy

Must not:
- execute forbidden destructive commands
- bypass guardrails
- perform irreversible actions without explicit safeguards

## Quick Commands

```bash
python -m fault_injector list-scenarios
python -m fault_injector validate-config fault_injector/fault-injector-test.yaml
python -m fault_injector run --config fault_injector/fault-injector-test.yaml --dry-run
python -m fault_injector recover --session-id <session_id>
```

```bash
python -m load_simulator list-scenarios
python -m load_simulator run --config load_simulator/config/notebook-soak-only.yaml --output-format json
```
