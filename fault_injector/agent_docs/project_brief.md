<!--
=============================================================================
FILE: project_brief.md
PURPOSE: High-level project context for AI agents and contributors
GUIDANCE:
- Read this file first to understand scope, architecture, and current state
- Treat this as the source of truth for "what exists now"
- Keep this document concise and current as implementation evolves
=============================================================================
-->

# Project Brief: Fault Injector

## Quick Summary

**What:** A multi-layer fault injection system for Cube Studio resilience validation  
**Why:** Validate failure handling, recovery, and observability before production incidents  
**Who:** Platform SRE and engineering teams  
**Status:** Stage 1 implemented (core system + required scenarios complete)

---

## Purpose and Scope

Fault Injector executes controlled chaos experiments across four layers:

- Hardware
- OS
- Platform
- Service

Design goals:

1. Deterministic orchestration (no LLM dependency for fault execution flow)
2. Safety-first execution with hard guards
3. Recovery-first operations with WAL-backed rollback
4. Reusable channel/scenario abstraction for rapid extension

---

## Architecture

Core modules in `fault_injector/`:

- `cli.py`, `__main__.py`: command entry and execution interface
- `config/`: schema, defaults, and loader
- `channels/`: target integrations (SSH, K8s, Redfish, Switch, Prometheus)
- `scenarios/`: fault definitions and registry
- `safety/`: safety guard + rollback journal
- `orchestrator/`: engine, session, watchdog
- `agents/`: agent base layer scaffold
- `reporting/`: reporting layer scaffold
- `tests/`: unit/integration/e2e-oriented test structure

Execution flow:

1. Select scenario + target
2. Pre-check safety constraints
3. Write rollback intent to WAL
4. Inject fault through channel
5. Observe and verify
6. Recover and mark WAL entry recovered

---

## Integration Points

| System | Channel | Purpose |
|---|---|---|
| SSH targets | `SSHChannel` | command execution and OS-level fault injection |
| Kubernetes | `K8sChannel` | pod/deployment operations |
| Redfish/BMC | `RedfishChannel` | hardware/BMC management |
| H3C Switch | `SwitchChannel` | switch/network fault operations |
| Prometheus | `PrometheusChannel` | metric queries for observation and verification |

---

## Current Implementation State

### Completed

- Channel layer implemented:
  - `BaseChannel`, `SSHChannel`, `PrometheusChannel`, `RedfishChannel`, `SwitchChannel`, `K8sChannel`
- Required scenario sets implemented and registered:
  - vLLM latency scenarios `RC-1` to `RC-6`
  - RDMA anomaly scenarios `F-1` to `F-6`
- Extended scenario packs implemented across all layers
- `safety/guard.py` and `safety/rollback.py` implemented
- Orchestrator framework (`engine.py`, `session.py`, `watchdog.py`) present
- Agent base framework (`agents/base.py`) present
- Reporting framework package present
- Tests reorganized under:
  - `tests/unit/features/...`
  - `tests/integration/...`
  - `tests/e2e/...`
  - shared `tests/fixtures`, `tests/mocks`, `tests/helpers`

### Not Yet Complete

- Concrete agent implementations beyond base class
- Full reporting implementation details (beyond framework scaffolding)
- Broader scenario test coverage and end-to-end automation depth

---

## Scenario Coverage Snapshot

Implemented scenario families include:

- Required vLLM latency scenarios: 6
- Required RDMA anomaly scenarios: 6
- Additional hardware scenarios: 4
- Additional OS scenarios: 3
- Additional platform scenarios: 4
- Additional service scenarios: 3

Total implemented scenarios: **26**

---

## Safety and Constraints

Must:

- keep every injection recoverable
- write rollback metadata before risky operations
- support and preserve `dry_run` behavior
- enforce safety guard checks

Must not:

- execute forbidden dangerous commands
- bypass safety checks
- run irreversible fault operations

---

## Testing and Docs

- Test guide: `fault_injector/TEST_GUIDE.md`
- Testing policy placeholder: `fault_injector/testing.md`
- Technical design and product docs: `fault_injector/docs/`

---

## Quick Commands

```bash
# dry-run execution
python -m fault_injector run --config fault-injector.yaml --dry-run

# real execution
python -m fault_injector run --config fault-injector.yaml

# recovery by session
python -m fault_injector recover --session-id <session_id>
```

