<!--
=============================================================================
FILE: agents.md
PURPOSE: Universal AI instructions for working with this project
GUIDANCE FOR AI AGENTS:
- This is the FIRST file you should read when working on this project
- Follow these instructions for all interactions with this codebase
- These instructions apply to all AI agents (Claude, GPT, etc.)
=============================================================================
-->

# AI Agent Instructions: Fault Injector

## Quick Start for AI Agents

1. Read this file (`agents.md`)
2. Read `agent_docs/project_brief.md`
3. Read `agent_docs/tech_stack.md`
4. Read `agent_docs/code_patterns.md`

---

## Project Context

Fault Injector is a multi-dimensional fault injection system for Cube Studio resilience testing across hardware, OS, platform, and service layers.

Key principles:

1. Safety first (recoverable operations only)
2. Async by default for I/O
3. Channel-based communication abstraction
4. Scenario-driven fault logic

---

## Important Files

| File | Purpose | Read When |
|------|---------|-----------|
| `agent_docs/project_brief.md` | Project overview and status | Starting work |
| `agent_docs/tech_stack.md` | Tech constraints | Adding dependencies |
| `agent_docs/code_patterns.md` | Coding conventions | Writing code |
| `agent_docs/product_requirements.md` | Requirements | Implementing features |
| `docs/PRD.md` | Product requirements | Understanding scope |
| `docs/techdesign.md` | Technical design | Architecture decisions |

---

## Project Trees

### cube-studio (workspace)

```text
cube-studio/
├── fault_injector/
├── lib/
│   └── channels/
├── load_simulator/
├── myapp/
├── install/
├── images/
└── job-template/
```

### fault_injector

```text
fault_injector/
├── agents.md
├── cli.py
├── testing.md
├── agent_docs/
├── docs/
├── agents/
├── config/
├── orchestrator/
├── reporting/
├── safety/
├── scenarios/
└── tests/
    ├── unit/features/channels/
    ├── unit/features/scenarios/
    ├── integration/
    ├── e2e/
    ├── fixtures/
    ├── mocks/
    └── helpers/
```

### Public Channel Modules

```text
lib/channels/
├── base.py
├── kubernetes.py
├── prometheus.py
├── redfish.py
├── ssh.py
├── switch.py
└── __init__.py
```

---

## Coding Guidelines

### Must Do

- Use type hints on public functions
- Write docstrings for public APIs
- Follow async patterns for I/O
- Use Pydantic models for config/data contracts
- Write tests for new behavior
- Follow `agent_docs/code_patterns.md`

### Must NOT Do

- Use bare `except:`
- Use mutable default arguments
- Hardcode credentials/secrets
- Skip safety checks
- Add dependencies without updating docs

---

## Common Tasks

### Adding a New Scenario

1. Add file under `fault_injector/scenarios/`
2. Inherit `BaseScenario`
3. Implement `inject()`, `recover()`, `verify()`
4. Register in `fault_injector/scenarios/registry.py`
5. Add tests in `fault_injector/tests/unit/features/scenarios/`

### Adding a New Channel

1. Add file under `lib/channels/`
2. Inherit `BaseChannel` from `lib/channels/base.py`
3. Implement `_execute_impl()`
4. Add safety checks for dangerous operations
5. Add tests in `fault_injector/tests/unit/features/channels/`

### Fixing a Bug

1. Write a failing test first
2. Implement fix
3. Re-run affected tests
4. Re-run relevant suite for regressions

---

## Testing

```bash
# all fault injector tests
pytest fault_injector/tests/

# scenario unit tests
pytest fault_injector/tests/unit/features/scenarios/test_scenarios.py

# channel unit tests
pytest fault_injector/tests/unit/features/channels/

# with coverage
pytest fault_injector/tests/ --cov=fault_injector --cov-report=html
```

See `fault_injector/testing.md` for detailed strategy.

---

## Safety Notes

- All injections must have rollback/recovery behavior
- Recovery info should be written before risky operations
- Do not bypass guardrails for destructive operations

---

## Checklist

- [ ] Read project context docs
- [ ] Follow code patterns
- [ ] Added/updated tests
- [ ] Updated docs when behavior changed
- [ ] No safety regressions introduced

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-27 | 1.1 | Updated project trees and moved channel module location to `lib/channels` |
