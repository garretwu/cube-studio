<!--
=============================================================================
FILE: agents.md
PURPOSE: Universal AI instructions for working with this project
GUIDANCE FOR AI AGENTS:
- This is the FIRST file you should read when working on this project
- Follow these instructions for all interactions with this codebase
- These instructions apply to all AI agents (Claude, GPT, etc.)
- Update this file when adding new AI-relevant conventions
=============================================================================
-->

# AI Agent Instructions: Fault Injector

## Quick Start for AI Agents

Welcome to the **Fault Injector** project. This file contains universal instructions for AI agents working on this codebase.

### First Steps

1. **Read this file** (`agents.md`) - You are here
2. **Read project brief** (`agent_docs/project_brief.md`) - Understand what this project is
3. **Check tech stack** (`agent_docs/tech_stack.md`) - Know what technologies are used
4. **Review code patterns** (`agent_docs/code_patterns.md`) - Follow coding conventions

---

## Project Context

### What is this project?

Fault Injector is a multi-dimensional fault injection system for testing the resilience of the Cube Studio platform. It injects controlled faults across hardware, OS, platform, and service layers.

### Key Principles

1. **Safety First**: All operations must be recoverable
2. **Async by Default**: All I/O is async
3. **Channel-based**: Communication through abstracted channels
4. **Scenario-driven**: Faults are reusable, configurable scenarios

### Important Files

| File | Purpose | Read When |
|------|---------|-----------|
| `agent_docs/project_brief.md` | Project overview | Starting work |
| `agent_docs/tech_stack.md` | Technologies used | Adding dependencies |
| `agent_docs/code_patterns.md` | Coding conventions | Writing code |
| `agent_docs/product_requirements.md` | Requirements | Implementing features |
| `FAULT_INJECTOR_IMPLEMENTATION_GUIDE.md` | Implementation details | Deep dive |
| `docs/PRD.md` | Product requirements | Understanding scope |
| `docs/techdesign.md` | Technical design | Architecture decisions |

---

## Coding Guidelines

### Must Do

- ✅ Use type hints on all public functions
- ✅ Write docstrings for all public APIs
- ✅ Follow async patterns for I/O operations
- ✅ Use Pydantic for data validation
- ✅ Write tests for new functionality
- ✅ Follow the code patterns in `agent_docs/code_patterns.md`

### Must NOT Do

- ❌ Use bare `except:` clauses
- ❌ Use mutable default arguments
- ❌ Hardcode credentials or secrets
- ❌ Skip safety checks
- ❌ Introduce new dependencies without updating tech_stack.md
- ❌ Execute dangerous commands (see safety guards)

---

## Project Structure

```
fault_injector/
├── agents.md              ← You are here (AI instructions)
├── agent_docs/            ← AI-specific documentation
│   ├── project_brief.md
│   ├── tech_stack.md
│   ├── code_patterns.md
│   └── product_requirements.md
├── docs/                  ← Human-facing documentation
│   ├── PRD.md
│   ├── techdesign.md
│   └── research-fault-injector.txt
├── config/                ← Configuration management
├── orchestrator/          ← Session orchestration
├── agents/                ← Fault injection agents
├── channels/              ← Communication channels
├── scenarios/             ← Fault injection scenarios
├── safety/                ← Safety guards and rollback
├── reporting/             ← Report generation
└── tests/                 ← Test suite
```

---

## Common Tasks

### Adding a New Scenario

1. Create a new file in `scenarios/` (e.g., `scenarios/my_scenario.py`)
2. Inherit from `BaseScenario` in `scenarios/base.py`
3. Implement `inject()`, `recover()`, and `verify()` methods
4. Register in `scenarios/registry.py`
5. Add tests in `tests/scenario/`
6. Update `agent_docs/product_requirements.md`

### Adding a New Channel

1. Create a new file in `channels/` (e.g., `channels/my_channel.py`)
2. Inherit from `BaseChannel` in `channels/base.py`
3. Implement `_execute_impl()` method
4. Add safety checks for dangerous operations
5. Add tests in `tests/channel/`
6. Update `agent_docs/tech_stack.md`

### Fixing a Bug

1. Write a test that reproduces the bug
2. Fix the bug
3. Verify the test passes
4. Run the full test suite to ensure no regressions

---

## Testing

### Running Tests

```bash
# Run all tests
pytest fault_injector/tests/

# Run specific test file
pytest fault_injector/tests/scenario/test_scenarios.py

# Run with coverage
pytest fault_injector/tests/ --cov=fault_injector --cov-report=html

# Run in dry-run mode
python -m fault_injector run --config fault-injector-test.yaml --dry-run
```

### Test Guidelines

- Use `pytest` and `pytest-asyncio` for async tests
- Mirror source structure in `tests/` directory
- Use fixtures from `tests/conftest.py`
- Mock external services (SSH, K8s, etc.)
- See `testing.md` for detailed testing strategy

---

## Safety Guidelines

### Dangerous Commands (BLOCKED)

These commands are blocked by safety guards:

- `rm -rf /`
- `dd if=/dev/zero`
- `:(){ :|:& };:` (fork bomb)
- BMC network configuration changes
- Kubernetes namespace deletion

### Recovery Guarantees

- All faults must have a recovery command
- Recovery commands are written to WAL before injection
- Crash recovery is supported via `--resume`

---

## Communication Style

### When Working on This Project

1. **Be explicit**: Reference specific files, functions, and requirements
2. **Be safe**: Always consider recovery and rollback
3. **Be thorough**: Write tests, update documentation
4. **Be consistent**: Follow existing patterns

### When Suggesting Changes

1. Explain the reasoning
2. Reference relevant requirements (REQ-XXX)
3. Consider safety implications
4. Update relevant documentation

---

## Checklist for AI Agents

Before completing a task, verify:

- [ ] Read `agent_docs/project_brief.md` for context
- [ ] Followed code patterns in `agent_docs/code_patterns.md`
- [ ] All new functions have type hints
- [ ] All public APIs have docstrings
- [ ] Tests written for new functionality
- [ ] Documentation updated if needed
- [ ] No safety violations introduced
- [ ] No hardcoded credentials

---

## Questions?

If you need more context:

1. Check `FAULT_INJECTOR_IMPLEMENTATION_GUIDE.md` for implementation details
2. Check `docs/techdesign.md` for architecture decisions
3. Check existing code for patterns and examples
4. Ask the user for clarification

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-27 | 1.0 | Initial AI instructions |

---

*This file is maintained for AI agent context. Update when project conventions change.*