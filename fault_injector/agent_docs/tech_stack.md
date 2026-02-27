<!--
=============================================================================
FILE: tech_stack.md
PURPOSE: Document the technology stack, frameworks, and dependencies
GUIDANCE FOR AI AGENTS:
- Reference this file to understand what technologies are available/used
- When adding new dependencies, update this file
- Follow version constraints specified here
- Use the libraries listed here rather than introducing new ones
- Check this file before suggesting new technologies
=============================================================================
-->

# Technology Stack: Fault Injector

## Overview
This document defines the technology stack for the fault_injector project. AI agents should use this as a reference for all implementation decisions.

---

## Core Technologies

### Language & Runtime

| Technology | Version | Purpose |
|------------|---------|---------|
| Python | 3.10+ | Primary language |
| asyncio | Built-in | Async runtime |

### Package Management

| Tool | Purpose |
|------|---------|
| pip | Package installer |
| requirements.txt | Dependency specification |

---

## Frameworks & Libraries

### CLI Framework

| Library | Version | Purpose |
|---------|---------|---------|
| Click | ^8.0 | CLI framework |

### Configuration

| Library | Version | Purpose |
|---------|---------|---------|
| Pydantic | ^2.0 | Data validation & settings |
| PyYAML | ^6.0 | YAML parsing |

### Async & Networking

| Library | Version | Purpose |
|---------|---------|---------|
| asyncio | Built-in | Async programming |
| asyncssh | ^2.14 | Async SSH client |
| httpx | ^0.25 | Async HTTP client |
| ncclient | ^0.6 | NETCONF client (for switch) |

### Infrastructure

| Library | Version | Purpose |
|---------|---------|---------|
| kubernetes | ^28.0 | Kubernetes Python client |
| redfish | ^3.2 | Redfish/BMC API client |

### Observability

| Library | Version | Purpose |
|---------|---------|---------|
| prometheus-client | ^0.18 | Prometheus metrics |

### Testing

| Library | Version | Purpose |
|---------|---------|---------|
| pytest | ^7.0 | Test framework |
| pytest-asyncio | ^0.21 | Async test support |
| pytest-cov | ^4.0 | Coverage reporting |

---

## External Systems

### Infrastructure

| System | Purpose | Integration |
|--------|---------|-------------|
| Kubernetes | Container orchestration | kubernetes library |
| SSH | Remote command execution | asyncssh |
| Redfish/BMC | Hardware management | redfish library |
| H3C Switch | Network configuration | ncclient (NETCONF) |
| Prometheus | Metrics collection | httpx (HTTP API) |

---

## Code Style & Quality

| Tool | Purpose |
|------|---------|
| ruff | Linting & formatting |
| mypy | Type checking |
| black | Code formatting (alternative) |

### Type Hints
- All public functions must have type hints
- Use `typing` module for complex types
- Use Pydantic models for structured data

### Async Patterns
- Use `async/await` for all I/O operations
- Prefer `asyncio` over threading
- Use `asyncio.gather()` for concurrent operations

---

## Project Structure

```
fault_injector/
├── __init__.py
├── __main__.py          # Entry point
├── cli.py               # Click CLI commands
├── config/              # Configuration management
├── orchestrator/        # Fault injection orchestration
├── agents/              # Fault injection agents
├── channels/            # Communication channels
├── scenarios/           # Fault injection scenarios
├── safety/              # Safety guards and rollback
├── reporting/           # Report generation
└── tests/               # Test suite
```

---

## Dependency Guidelines

### Adding New Dependencies

1. **Evaluate necessity**: Is this dependency essential?
2. **Check alternatives**: Is there already a library that does this?
3. **Check maintenance**: Is the library actively maintained?
4. **Document**: Add to this file with version and purpose

### Version Constraints

- Use `^` (caret) for compatible versions
- Pin exact versions for critical dependencies
- Review and update versions quarterly

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `FAULT_INJECTOR_CONFIG` | Config file path | `./fault-injector.yaml` |
| `FAULT_INJECTOR_LOG_LEVEL` | Log level | `INFO` |
| `FAULT_INJECTOR_DRY_RUN` | Dry run mode | `false` |

---

## Notes

<!-- Additional notes about the tech stack -->