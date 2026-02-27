<!--
=============================================================================
FILE: project_brief.md
PURPOSE: Provide a high-level overview of the project for AI context
GUIDANCE FOR AI AGENTS:
- Read this file first to understand the project context
- Use this as the primary reference for "what is this project?"
- Update when project scope or direction changes
- Keep this document concise but comprehensive
=============================================================================
-->

# Project Brief: Fault Injector

## Quick Summary

**What:** A multi-dimensional fault injection system for testing system resilience  
**Why:** To validate that Cube Studio platform can handle various failure scenarios  
**Who:** Platform SRE team and developers  
**Status:** In Development

---

## Project Overview

### Purpose

The Fault Injector is a tool designed to inject controlled faults into the Cube Studio platform to test its resilience. It supports multiple fault types across four layers: hardware, OS, platform, and service.

### Core Value Proposition

1. **Resilience Testing**: Validate that systems can handle failures gracefully
2. **Chaos Engineering**: Discover weaknesses before they cause production issues
3. **Automated Recovery Testing**: Verify that recovery mechanisms work correctly

---

## Key Concepts

### Fault Layers

| Layer | Description | Examples |
|-------|-------------|----------|
| Hardware | Physical infrastructure faults | GPU contention, BMC faults, RDMA issues |
| OS | Operating system level faults | Network latency, CPU pressure, disk I/O |
| Platform | Platform component faults | K8s pod failures, database issues |
| Service | Application level faults | API latency, service crashes |

### Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                      Fault Injector                          │
├─────────────────────────────────────────────────────────────┤
│  CLI (cli.py)           ← User interface                    │
│  Orchestrator           ← Session & flow management         │
│  Scenarios              ← Fault injection logic             │
│  Channels               ← Communication with targets        │
│  Safety                 ← Guards and rollback               │
│  Reporting              ← Results and analysis              │
└─────────────────────────────────────────────────────────────┘
```

### Key Workflows

1. **Inject**: Deploy a fault to a target
2. **Observe**: Monitor system behavior during fault
3. **Recover**: Remove the fault and restore normal state
4. **Verify**: Confirm system has recovered

---

## Technical Context

### Architecture Pattern

- **Async-first**: All I/O operations are async
- **Channel-based**: Communication through abstracted channels
- **Safety-first**: WAL (Write-Ahead Log) for recovery guarantees
- **Scenario-driven**: Faults defined as reusable scenarios

### Integration Points

| System | Purpose | Channel |
|--------|---------|---------|
| SSH | Remote command execution | SSHChannel |
| Kubernetes | Pod/Deployment operations | K8sChannel |
| Redfish/BMC | Hardware management | RedfishChannel |
| H3C Switch | Network configuration | SwitchChannel |
| Prometheus | Metrics collection | PrometheusChannel |

---

## Project Constraints

### Must Do

- Ensure all faults are recoverable
- Log all operations for audit trail
- Support dry-run mode for testing
- Respect safety guards (no dangerous commands)

### Must NOT Do

- Inject faults that cannot be recovered
- Modify production systems without confirmation
- Execute dangerous commands (rm -rf /, etc.)
- Skip safety checks

---

## Current State

### Implemented

- ✅ CLI framework
- ✅ Configuration management
- ✅ SSH Channel
- ✅ Safety guards and rollback
- ✅ Base scenario framework
- ✅ Network jitter scenario

### In Progress

- 🔄 Orchestrator engine
- 🔄 Additional channels (K8s, Redfish, Switch)
- 🔄 vLLM latency scenarios
- 🔄 RDMA anomaly scenarios

### Planned

- 📋 Reporting system
- 📋 Dashboard integration
- 📋 Advanced scenarios

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Scenario coverage | 12+ scenarios |
| Recovery success rate | 100% |
| Documentation coverage | All public APIs |
| Test coverage | 80%+ |

---

## Stakeholders

| Role | Responsibility |
|------|---------------|
| SRE Team | Primary users, define scenarios |
| Platform Team | Maintain integration points |
| Development Team | Implement and maintain code |

---

## Related Documents

- `FAULT_INJECTOR_IMPLEMENTATION_GUIDE.md` - Implementation details
- `TEST_GUIDE.md` - Testing instructions
- `testing.md` - Testing strategy
- `AIGuider/H3C_NETCONF_GUIDE.md` - Switch integration guide

---

## Quick Start

```bash
# Run a scenario in dry-run mode
python -m fault_injector run --config fault-injector.yaml --dry-run

# Run with actual injection
python -m fault_injector run --config fault-injector.yaml

# Recover from a session
python -m fault_injector recover --session-id <session_id>
```

---

## Notes

<!-- Additional context for AI agents -->