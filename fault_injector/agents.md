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

最后更新: 2026-03-09

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
4. Agent-centric execution with scenario adapters

---

## Current Code Structure

### Workspace tree

```text
cube-studio/
├── fault_injector/
├── lib/
│   └── fchannels/
├── load_simulator/
├── myapp/
├── install/
├── images/
└── job-template/
```

### fault_injector tree

```text
fault_injector/
├── agents.md
├── cli.py
├── pytest.ini
├── testing.md
├── agent_docs/
├── docs/
├── agents/
├── channels/
├── config/
├── orchestrator/
├── reporting/
├── safety/
├── scenarios/
├── tests/
│   ├── channel/
│   ├── common/
│   ├── config/
│   ├── e2e/
│   ├── fixtures/
│   ├── helpers/
│   ├── integration/
│   ├── mocks/
│   ├── mocktest/
│   ├── scenario/
│   └── unit/features/
├── fault_reports/
└── tools/
```

### Runtime channel modules

```text
lib/fchannels/
├── base.py
├── kubernetes.py
├── prometheus.py
├── redfish.py
├── ipmi.py
├── ssh.py
├── switch.py
└── __init__.py
```

---

## 已完成项目（截至 2026-03-06）

1. 完成 CLI + Orchestrator 主链路（run/recover/resume/validate/list）。
2. 完成 12 个场景注册（vLLM RC-1~RC-6，RDMA F-1~F-6）。
3. 完成场景级 load_simulator 联动（`network_jitter`、`platform_cascade`）。
4. 完成 load_simulator strict/best-effort 策略和事件落盘（`load_simulator_run`、`load_simulator_warning`）。
5. 完成 monitor baseline 配置化（`monitor.baseline_queries`）。
6. 完成 baseline 质量门禁（最小样本、错误率阈值、非零要求、strict 模式）。
7. 完成 Prometheus baseline 采样统计字段（`sample_count/error_count/error_ratio/zero_ratio/last_error`）。
8. 完成 observe 窗口时长修复（使用完整 `observe_duration`）。
9. 完成关键单测覆盖（LS 联动、baseline 质量告警、query render 告警、observe duration 行为）。

---

## Progress Snapshot

- P0 核心可运行能力: Done
- P1 监控可信度改造: In Progress（核心实现完成，报告聚合展示待补）
- P2 联动统一编排层: Todo

---

## Next Plan

1. 扩展 load_simulator 接入范围，优先补 `gpu_contention`。
2. 报告增加 baseline 质量摘要与联动结果聚合字段。
3. 增加 watchdog 与 load_simulator 超时关系的前置校验。
4. 收敛联动 warning 事件重复记录。
5. 增补 combined 模式与多节点联动失败路径回归测试。

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
| `docs/architecture.md` | Runtime architecture + progress | Syncing implementation status |

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

## Vibe Coding Guidance

- Keep changes small and reversible.
- Prefer editing existing modules; create new files only if required.
- Use `rg` to locate references before editing.
- When intent is unclear, ask one targeted question.
- Keep docs/tests aligned with changes, especially `docs/architecture.md`, `docs/techdesign.md`, and `agents.md`.
- Avoid inventing APIs or paths not present in the tree.

---

## Common Tasks

### Adding a New Agent

1. Add file under `fault_injector/agents/`
2. Inherit `BaseAgent`
3. Implement `inject()`, `recover()`, `verify()`, `status()`
4. Keep execution channel-driven
5. Add tests in `fault_injector/tests/unit/features/agents/`

### Adding a New Scenario

1. Add scenario class under `fault_injector/scenarios/`
2. Register in `fault_injector/scenarios/registry.py`
3. Keep scenario logic recoverable and guard-checked
4. Add/adjust tests in `fault_injector/tests/unit/features/scenarios/`
5. Update docs (`docs/architecture.md` and this file) for status changes

### Adding a New Channel

1. Add file under `lib/fchannels/`
2. Inherit `BaseChannel` from `lib/fchannels/base.py`
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
pytest fault_injector/tests/unit/features/scenarios/

# channel unit tests
pytest fault_injector/tests/unit/features/channels/

# with coverage
pytest fault_injector/tests/ --cov=fault_injector --cov-report=html
```

See `fault_injector/testing.md` for detailed strategy.

---

## Safety Notes

- All injections must have rollback/recovery behavior.
- Recovery info should be written before risky operations.
- Do not bypass guardrails for destructive operations.

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
| 2026-03-09 | 1.5 | Moved channels package to `lib/fchannels`, moved pytest config to `fault_injector/pytest.ini`, and moved reports to `fault_injector/fault_reports` |
| 2026-03-06 | 1.4 | Updated code structure, completion status, progress snapshot, and next plan |
| 2026-02-28 | 1.3 | Clarified agent-centric ownership and scenario-layer scope (RC/F families only) |
| 2026-02-28 | 1.2 | Added agent-centric workflow guidance and new test tree entries |
| 2026-02-27 | 1.1 | Updated project trees and moved channel module location to `lib/fchannels` |
