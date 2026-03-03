# Fault Injector & Load Simulator Architecture

Last Updated: 2026-03-02
Status: Active

## 1. Scope

This document describes the runtime architecture of:
- `fault_injector` (fault orchestration and recovery)
- `load_simulator` (synthetic workload and stress profile)
- shared channel layer in `lib/channels`

It also defines the integration contract for `fault_injector` to trigger `load_simulator` during scenario execution.

## 2. Runtime Components

### 2.1 fault_injector

Core modules:
- `fault_injector/cli.py`: CLI entrypoint (`run`, `recover`, `resume`, `validate-config`, `list-scenarios`)
- `fault_injector/config/*`: Pydantic schema + YAML loading
- `fault_injector/orchestrator/*`: session lifecycle, scheduling, watchdog
- `fault_injector/scenarios/*`: concrete fault scenarios (vLLM latency + RDMA anomaly)
- `fault_injector/safety/*`: safety guard + WAL rollback journal
- `fault_injector/agents/*`: layer agents routing scenario execution

Execution lifecycle:
1. Load and validate config
2. Initialize session + channels + guard + rollback journal
3. Preflight checks and optional baseline collection
4. Inject scenario fault
5. Observe and collect evidence
6. Recover and verify
7. Persist session artifacts

### 2.2 load_simulator

Core modules:
- `load_simulator/cli.py`: CLI entrypoint (`run`, `validate-config`, `list-scenarios`)
- `load_simulator/orchestrator/*`: stage execution and adaptive control
- `load_simulator/agents/*`: inference/pipeline/finetune/notebook workload agents
- `load_simulator/config/*`: config schema and defaults

Typical responsibility:
- Generate controlled load patterns (`single`, `mixed`, `stress`, `soak`)
- Produce structured run summary and metrics output

### 2.3 Shared Channel Layer

Shared channels in `lib/channels`:
- `ssh.py` (`asyncssh`)
- `redfish.py` (`httpx`)
- `switch.py` (`ncclient`)
- `kubernetes.py` (`kubernetes` client)
- `prometheus.py` (`httpx`)

`fault_injector` scenarios use these channels for deterministic, recoverable operations.

## 3. Current Integration Status

Current codebase state:
- `fault_injector` and `load_simulator` are both runnable from CLI.
- There is no unified, reusable integration adapter in `fault_injector/scenarios/base.py` yet.
- Scenario code under `fault_injector/scenarios/vllm_latency.py` already uses subprocess-style remote process control patterns through SSH, which can be extended to support `load_simulator` invocation.

Implication:
- Integration is feasible now, but standardization is still required for consistent contract, timeout, parsing, and rollback behavior.

## 4. Integration Contract (Fault Injector -> Load Simulator)

Target contract for scenario-level invocation:

```python
async def _run_load_simulator(config_path: str, only: list[str]) -> dict:
    """Run load simulator and return normalized summary."""
```

Recommended command shape:

```bash
python -m load_simulator run --config <config_path> --output-format json [--only <scenario>]...
```

Required behavior:
1. Execute as async subprocess
2. Capture `stdout/stderr`
3. Parse JSON from `stdout`
4. Validate `exit_code == 0`
5. Return normalized summary payload for scenario logic
6. On failure: raise typed runtime error with bounded stderr excerpt

## 5. Data and Error Boundaries

Input boundary:
- Fault scenario params define whether load simulation is enabled and which LS scenario(s) to run.

Output boundary:
- Keep only fields required by scenario decision logic and report generation.
- Persist raw load simulator payload in session artifacts for audit/debug.

Failure policy:
- `strict` mode: fail scenario injection if load simulator fails
- `best-effort` mode: continue scenario with warning and explicit event marker

## 6. Safety and Recovery Requirements

Any integration must preserve existing invariants:
- WAL-first principle for recoverable operations
- no bypass of `SafetyGuard`
- bounded subprocess timeout and output size
- explicit cleanup/termination for child process on timeout/cancel
- integration failure must not leave fault operations unrecoverable

## 7. Operator Flow (Target)

1. Run `fault_injector` scenario with load-linked params
2. Scenario starts load simulator sub-run
3. Fault injects while load is active
4. Observe impact metrics
5. Stop/collect load run summary
6. Recover fault and verify
7. Output combined report (fault + load evidence)

## 8. Related Docs

- `fault_injector/agent_docs/project_brief.md`
- `fault_injector/docs/load_simulator_integration_plan.md`
- `fault-injector.md`
