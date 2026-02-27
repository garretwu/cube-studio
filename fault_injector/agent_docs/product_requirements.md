<!--
=============================================================================
FILE: product_requirements.md
PURPOSE: Define feature requirements and acceptance criteria for AI reference
GUIDANCE FOR AI AGENTS:
- Reference this file when implementing features
- Each requirement has acceptance criteria that must be met
- Update when requirements change or new features are added
- Link implementation code to requirements where possible
=============================================================================
-->

# Product Requirements: Fault Injector

## Overview
This document captures the functional requirements and acceptance criteria for the fault_injector project. AI agents should verify implementations against these requirements.

---

## Core Requirements

### REQ-001: Fault Injection Framework

**Priority:** P0  
**Status:** In Progress  
**Layer:** All

**Description:**
The system must provide a framework for defining and executing fault injection scenarios across multiple layers (hardware, OS, platform, service).

**Acceptance Criteria:**
- [ ] AC1: Scenarios can be defined in YAML configuration
- [ ] AC2: Scenarios support inject/recover/verify lifecycle
- [ ] AC3: Scenarios can target specific nodes
- [ ] AC4: Scenarios can be chained in sequences

**Technical Notes:**
- Use `BaseScenario` class as base
- Register scenarios in `SCENARIO_REGISTRY`

---

### REQ-002: Safety Guards

**Priority:** P0  
**Status:** Complete  
**Layer:** All

**Description:**
The system must prevent dangerous operations that could cause irreversible damage.

**Acceptance Criteria:**
- [x] AC1: Block `rm -rf /` and variants
- [x] AC2: Block `dd if=/dev/zero` to disks
- [x] AC3: Block fork bombs
- [x] AC4: Block BMC network reconfiguration
- [x] AC5: Log all blocked attempts

**Technical Notes:**
- Implemented in `safety/guard.py`
- Pattern matching with regex

---

### REQ-003: Rollback Mechanism

**Priority:** P0  
**Status:** Complete  
**Layer:** All

**Description:**
The system must guarantee recovery from any injected fault using Write-Ahead Logging.

**Acceptance Criteria:**
- [x] AC1: Recovery command written before injection
- [x] AC2: WAL persisted to disk with fsync
- [x] AC3: Crash recovery supported via `--resume`
- [x] AC4: All active faults tracked in session

**Technical Notes:**
- Implemented in `safety/rollback.py`
- JSONL format for log entries

---

### REQ-004: SSH Channel

**Priority:** P0  
**Status:** Complete  
**Layer:** OS

**Description:**
The system must execute commands on remote nodes via SSH.

**Acceptance Criteria:**
- [x] AC1: Support password and key-based auth
- [x] AC2: Support sudo escalation
- [x] AC3: Connection pooling for performance
- [x] AC4: Proper error handling and timeouts

**Technical Notes:**
- Implemented in `channels/ssh.py`
- Uses asyncssh library

---

### REQ-005: Dry Run Mode

**Priority:** P0  
**Status:** Complete  
**Layer:** All

**Description:**
The system must support a dry-run mode that simulates operations without actual execution.

**Acceptance Criteria:**
- [x] AC1: CLI flag `--dry-run` available
- [x] AC2: Commands logged but not executed
- [x] AC3: All validations still performed
- [x] AC4: Clear indication in output that it's dry-run

**Technical Notes:**
- Handled at Channel level
- Check `dry_run` flag before execution

---

## Scenario Requirements

### REQ-SCN-001: Network Jitter (RC-2)

**Priority:** P0  
**Status:** Complete  
**Layer:** OS

**Description:**
Inject network latency and jitter using `tc netem`.

**Acceptance Criteria:**
- [x] AC1: Configurable delay and jitter values
- [x] AC2: Support distribution modes (normal, pareto, uniform)
- [x] AC3: Support packet loss simulation
- [x] AC4: Clean recovery (remove qdisc)

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| delay_ms | int | 50 | Base delay in milliseconds |
| jitter_ms | int | 100 | Jitter range in milliseconds |
| distribution | str | "normal" | Distribution mode |
| loss_pct | float | 0 | Packet loss percentage |
| interface | str | "eth0" | Target network interface |

---

### REQ-SCN-002: GPU Contention (RC-1)

**Priority:** P0  
**Status:** Planned  
**Layer:** Hardware

**Description:**
Create GPU resource contention using gpu-burn.

**Acceptance Criteria:**
- [ ] AC1: Configurable duration and GPU ID
- [ ] AC2: Run gpu-burn in background
- [ ] AC3: Clean recovery (kill process)
- [ ] AC4: Verify process terminated

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| duration | int | 300 | Duration in seconds |
| gpu_id | int | 0 | Target GPU device ID |

---

### REQ-SCN-003: Storage I/O Interference (RC-3)

**Priority:** P0  
**Status:** Planned  
**Layer:** OS

**Description:**
Create disk I/O pressure using fio.

**Acceptance Criteria:**
- [ ] AC1: Configurable target file and duration
- [ ] AC2: Multiple job types (read, write, randread, randwrite)
- [ ] AC3: Clean recovery (kill fio)
- [ ] AC4: Verify I/O stopped

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| duration | int | 300 | Duration in seconds |
| filename | str | "/data/testfile" | Target file path |
| rw | str | "randwrite" | I/O pattern |

---

### REQ-SCN-004: OS Resource Pressure (RC-5)

**Priority:** P0  
**Status:** Planned  
**Layer:** OS

**Description:**
Create CPU and memory pressure using stress-ng.

**Acceptance Criteria:**
- [ ] AC1: Configurable CPU workers and load percentage
- [ ] AC2: Configurable memory allocation percentage
- [ ] AC3: Clean recovery (kill stress-ng)
- [ ] AC4: Verify processes terminated

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| duration | int | 300 | Duration in seconds |
| cpu_workers | int | 64 | Number of CPU workers |
| cpu_load | int | 90 | CPU load percentage |
| vm_bytes_percent | int | 80 | Memory allocation percentage |

---

### REQ-SCN-005: ECN Misconfiguration (F-2)

**Priority:** P1  
**Status:** Planned  
**Layer:** Hardware

**Description:**
Modify ECN thresholds on H3C switch to cause RDMA performance issues.

**Acceptance Criteria:**
- [ ] AC1: NETCONF connection to switch
- [ ] AC2: Configurable ECN threshold values
- [ ] AC3: Clean recovery (restore original values)
- [ ] AC4: Verify configuration restored

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| switch | str | "sw-200g" | Target switch hostname |
| interface | str | "HundredGigE 1/0/1" | Target interface |

---

### REQ-SCN-006: RDMA Link Flap (F-4)

**Priority:** P1  
**Status:** Planned  
**Layer:** Hardware

**Description:**
Cause intermittent RDMA link interruptions.

**Acceptance Criteria:**
- [ ] AC1: Configurable flap interval and duration
- [ ] AC2: Script-based continuous flap
- [ ] AC3: Clean recovery (stop script, ensure link up)
- [ ] AC4: Verify link restored

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| switch | str | "sw-200g" | Target switch hostname |
| interface | str | "HundredGigE 1/0/1" | Target interface |
| flap_interval | int | 30 | Seconds between flaps |
| flap_duration | int | 5 | Seconds link is down |

---

## Channel Requirements

### REQ-CH-001: Kubernetes Channel

**Priority:** P0  
**Status:** In Progress  
**Layer:** Platform

**Description:**
Interact with Kubernetes for pod and deployment operations.

**Acceptance Criteria:**
- [ ] AC1: Delete pods by name or label selector
- [ ] AC2: Scale deployments
- [ ] AC3: Get pod status
- [ ] AC4: Block deletion of namespaces

**Operations:**
| Action | Description |
|--------|-------------|
| delete_pod | Delete a specific pod |
| scale_deployment | Change replica count |
| get_pods | List pods with optional filter |

---

### REQ-CH-002: Redfish Channel

**Priority:** P1  
**Status:** Planned  
**Layer:** Hardware

**Description:**
Interact with BMC via Redfish API for hardware operations.

**Acceptance Criteria:**
- [ ] AC1: Power on/off servers
- [ ] AC2: Get hardware sensor data
- [ ] AC3: Configure boot settings
- [ ] AC4: Block network configuration changes

---

### REQ-CH-003: Switch Channel (H3C)

**Priority:** P1  
**Status:** In Progress  
**Layer:** Hardware

**Description:**
Interact with H3C switches via NETCONF.

**Acceptance Criteria:**
- [ ] AC1: NETCONF connection with proper auth
- [ ] AC2: Execute configuration commands
- [ ] AC3: Query interface status
- [ ] AC4: Rollback configuration changes

---

### REQ-CH-004: Prometheus Channel

**Priority:** P0  
**Status:** Planned  
**Layer:** Observability

**Description:**
Query Prometheus for metrics collection.

**Acceptance Criteria:**
- [ ] AC1: Instant query support
- [ ] AC2: Range query support
- [ ] AC3: Baseline collection helper
- [ ] AC4: Error handling for query failures

---

## Non-Functional Requirements

### NFR-001: Performance

**Criteria:**
- Command execution latency < 5s for SSH
- Configuration load time < 1s
- Support 10+ concurrent fault injections

### NFR-002: Reliability

**Criteria:**
- 100% recovery rate for supported scenarios
- No orphaned processes after recovery
- Crash recovery within 30 seconds

### NFR-003: Usability

**Criteria:**
- Clear CLI help text
- Informative error messages
- Dry-run mode for testing

### NFR-004: Security

**Criteria:**
- No credentials in logs
- Safety guards for dangerous commands
- Audit trail for all operations

---

## Requirement Traceability

| Requirement | Component | Status |
|-------------|-----------|--------|
| REQ-001 | scenarios/base.py | In Progress |
| REQ-002 | safety/guard.py | Complete |
| REQ-003 | safety/rollback.py | Complete |
| REQ-004 | channels/ssh.py | Complete |
| REQ-005 | cli.py | Complete |

---

## Notes

<!-- Additional requirement notes -->