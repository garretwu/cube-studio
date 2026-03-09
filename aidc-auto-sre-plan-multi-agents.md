# AIDC Auto-SRE Multi-Agent Implementation Plan

> Scope: **sre_agent only** — `load_simulator/` and `fault_injector/` are complete
> Based on gap analysis between `AIDC-auto-SRE.md` and `aidc-auto-sre-plan.md` v2.6
> Generated: 2026-03-05

---

## 1. Gap Analysis (SRE Agent Scope Only)

### 1.1 Design Doc Features Not Covered in Plan

| ID | Gap | Design Doc Section | Severity |
|----|-----|--------------------|----------|
| GA-1 | **ConfigMemory** (AIDC baselines, thresholds, custom rules) — never scheduled | §9 | Medium |
| GA-2 | **MemoryStorePG** (asyncpg + Qdrant production backend) — no Sprint assignment | §9.4 | Medium |
| GA-3 | **SkillCreator** (auto-generate SKILL.md from traces) — missing from all Sprints | §10.11 | Low |
| GA-4 | **4 builtin skills** (gpu-health, network-diagnosis, storage-diagnosis, platform-health) — only vllm + rdma scheduled | §10.10 | Medium |
| GA-5 | **ConversationalAgent** underweighted — full LangGraph subgraph + chat history + streaming is a bullet, not a Track | §16 | Medium |
| GA-6 | **Alert.silence_alert()** integration into remediation flow — unmentioned | §6.6 | Low |
| GA-7 | **SLO degradation 4-tier logic** — file mentioned, tiered escalation not tested | §20.2 | Low |

### 1.2 Plan Items Without Design Doc Coverage

| ID | Gap | Severity |
|----|-----|----------|
| GB-1 | Helm Chart structure/values — plan Sprint 8, design doc silent | Low |
| GB-2 | Playwright E2E test scenarios — listed in tech stack, no spec | Low |

### 1.3 Key Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| NeMo Guardrails + LangGraph version conflict | High | Implement as optional layer; core ReAct works without NeMo |
| Frontend 7 pages + D3.js topology for single track | Medium | Start with table/tree; D3 as enhancement |
| Channel duplication (lib/ vs sre_agent/) | Medium | SRE channels in lib/; sre_agent imports only |

---

## 2. Assumptions

- `load_simulator/` is complete and produces JSON metrics output
- `fault_injector/` is complete with scenarios RC-1~RC-6, F-1~F-6
- `lib/channels/` exists with `base.py`, `cube_studio.py`, `kubernetes.py`, `prometheus.py`
- All new development targets `sre_agent/` directory + extensions to `lib/channels/`

---

## 3. Multi-Agent Strategy: 5 Agents + Coordinator

### 3.1 Why 5 Agents

With fault_injector removed, the scope narrows to sre_agent subsystems. The decomposition follows **disjoint file ownership** with maximum parallelism:

| Constraint | Implication |
|-----------|-------------|
| Agents share git repo | Use worktrees (`isolation: "worktree"`) for parallel work |
| Context window ~200K | Each agent bounded to ~25-40 files |
| No two agents modify same file | Main agent owns `__init__.py` and shared models |
| Dependencies explicit | Pydantic models defined upfront; agents import, never modify |

### 3.2 Agent Overview

```
┌─────────────────────────────────────────────────────┐
│                 Main Agent (Coordinator)             │
│  shared models, scaffolding, integration, merges     │
├──────────┬──────────┬──────────┬──────────┬─────────┤
│ Agent A  │ Agent B  │ Agent C  │ Agent D  │ Agent E │
│ Channels │ SRE Core │ SRE Infra│ Storage  │Frontend │
│ +Models  │ LangGraph│ Remed/API│ Onto/Mem │ React   │
│          │ Tools    │ Concurr  │ KB/HA/SLO│ 7 pages │
│          │ Skills   │ Auth     │          │         │
│          │ Guards   │          │          │         │
└──────────┴──────────┴──────────┴──────────┴─────────┘
```

---

## 4. Agent Assignments

### Agent A: Shared Channels + Models (Prerequisite)

**Owns**: New files in `lib/channels/`, `sre_agent/models/`

**Scope**:
```
lib/channels/
├── ssh.py              # asyncssh, connection pool, shlex.quote safety
├── redfish.py          # httpx, BMC session management
├── switch.py           # SSH CLI + NETCONF dual mode
├── log.py              # Log aggregation (Loki/ES)
├── alert.py            # Alert channel + silence_alert() [GA-6]
├── ontology.py         # Ontology graph query channel
└── knowledge.py        # Knowledge retrieval channel

sre_agent/
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── alert.py        # Alert, AlertSeverity, AlertStatus
│   ├── diagnosis.py    # DiagnosisResult, DiagnosisSession, ThinkingStep
│   ├── remediation.py  # RemediationPlan, RemediationAction, LoopResult
│   ├── ontology.py     # OntologyNode, OntologyEdge, entity types
│   ├── memory.py       # IncidentRecord, LearnedPattern, ConfigBaseline
│   ├── events.py       # WebSocket event types (WSEvent, EventType enum)
│   └── common.py       # SREResponse[T], ErrorCode, SafetyLevel enum

lib/tests/
├── test_ssh.py
├── test_redfish.py
├── test_switch.py
├── test_log.py
└── test_alert.py

sre_agent/tests/
└── test_models.py      # Serialization round-trip tests
```

**Estimated**: ~15 Python files + tests

**Runs FIRST** — all other agents depend on these models and channels.

---

### Agent B: SRE Agent Core

**Owns**: `sre_agent/agent/`, `sre_agent/guardrails/`, `sre_agent/tools/`, `sre_agent/skills/`, `sre_agent/config.py`, `sre_agent/cli.py`

**Reads**: `sre_agent/models/`, `lib/channels/`

**Scope**:
```
sre_agent/
├── config.py                    # SREConfig Pydantic settings
├── cli.py                       # Click CLI entry point
├── agent/
│   ├── __init__.py
│   ├── state.py                 # SREAgentState TypedDict
│   ├── graph.py                 # LangGraph StateGraph (reason→act→observe→decide)
│   ├── nodes.py                 # Node functions
│   ├── prompts.py               # System prompts (base, diagnosis, remediation)
│   └── conversational.py        # ConversationalAgent [GA-5]
├── guardrails/
│   ├── __init__.py
│   ├── config.py                # NeMo Guardrails config
│   ├── rails/
│   │   ├── input.co
│   │   ├── output.co
│   │   ├── execution.co
│   │   └── dialog.co
│   └── actions.py               # Custom NeMo actions
├── tools/
│   ├── __init__.py
│   ├── registry.py              # ToolRegistry with safety levels
│   ├── readonly/
│   │   ├── k8s.py               # kubectl get/describe/logs
│   │   ├── prometheus.py        # PromQL queries
│   │   ├── gpu.py               # nvidia-smi, DCGM metrics
│   │   ├── network.py           # RDMA, switch port stats
│   │   ├── ontology.py          # Graph traversal tools
│   │   └── memory.py            # Pattern/incident lookup
│   └── write/
│       ├── k8s.py               # kubectl apply/delete/scale
│       ├── remediation.py       # Execute remediation actions
│       └── network.py           # Switch port enable/disable
├── skills/
│   ├── __init__.py
│   ├── registry.py              # SkillRegistry
│   ├── executor.py              # SkillExecutor (4 fixed @tools)
│   ├── policy.py                # Skill selection policy
│   ├── creator.py               # SkillCreator [GA-3]
│   └── builtin/
│       ├── vllm-diagnosis/SKILL.md
│       ├── rdma-diagnosis/SKILL.md
│       ├── gpu-health/SKILL.md         # [GA-4]
│       ├── network-diagnosis/SKILL.md  # [GA-4]
│       ├── storage-diagnosis/SKILL.md  # [GA-4]
│       └── platform-health/SKILL.md    # [GA-4]
└── tests/
    ├── test_graph.py
    ├── test_tools.py
    ├── test_guardrails.py
    └── test_skills.py
```

**Estimated**: ~35 Python files + 4 .co + 6 SKILL.md

**Critical path**: LangGraph skeleton must work before Agent C can integrate remediation.

---

### Agent C: SRE Infrastructure (Remediation + API + Auth)

**Owns**: `sre_agent/remediation/`, `sre_agent/concurrency/`, `sre_agent/auth/`, `sre_agent/safety/`, `sre_agent/server.py`, `sre_agent/api/`, `sre_agent/nat/`

**Reads**: `sre_agent/models/`, `sre_agent/agent/` (imports graph for API integration)

**Scope**:
```
sre_agent/
├── server.py                    # FastAPI application factory
├── api/
│   ├── __init__.py
│   ├── routes.py                # REST endpoints (alerts, diagnosis, remediation, ontology)
│   ├── websocket.py             # WebSocket ThinkingStep streaming
│   └── middleware.py            # Auth, CORS, rate limiting
├── remediation/
│   ├── __init__.py
│   ├── engine.py                # RemediationEngine (WAL + canary + approval)
│   ├── loop.py                  # LoopOrchestrator (multi-candidate cycle)
│   ├── incident.py              # IncidentHandler (alert→diagnosis→remediation)
│   ├── wal.py                   # Write-ahead log
│   ├── canary.py                # Canary deployment logic
│   ├── approval.py              # Human approval gates
│   └── validator.py             # PlanValidator (blast radius, safety checks)
├── concurrency/
│   ├── __init__.py
│   ├── resource_lock.py         # ResourceLock (Redis-based distributed)
│   ├── alert_dedup.py           # AlertDeduplicator
│   └── alert_correlator.py      # AlertCorrelator (time-window grouping)
├── auth/
│   ├── __init__.py
│   ├── jwt.py                   # JWT token handling
│   └── rbac.py                  # Role-based access control
├── safety/
│   ├── __init__.py
│   └── blast_radius.py          # Blast radius calculation
├── nat/
│   ├── __init__.py
│   └── wrapper.py               # NeMo Agent Toolkit profiling
└── tests/
    ├── test_remediation.py
    ├── test_loop.py
    ├── test_concurrency.py
    ├── test_api.py
    └── test_auth.py
```

**Estimated**: ~28 Python files

---

### Agent D: Storage & Ontology (Data Layer)

**Owns**: `sre_agent/ontology/`, `sre_agent/knowledge/`, `sre_agent/memory/`, `sre_agent/ha/`, `sre_agent/slo/`, `sre_agent/lifecycle/`

**Reads**: `sre_agent/models/`, `lib/channels/`

**Scope**:
```
sre_agent/
├── ontology/
│   ├── __init__.py
│   ├── graph.py                 # OntologyGraph (NetworkX + aiosqlite)
│   ├── entities.py              # Entity type registration
│   ├── scanner/
│   │   ├── __init__.py
│   │   ├── k8s.py               # K8s topology discovery
│   │   ├── network.py           # Network topology (LLDP/switch)
│   │   ├── gpu.py               # GPU topology (nvidia-smi topo)
│   │   └── redfish.py           # BMC/hardware discovery
│   └── query.py                 # Graph query helpers (neighbors, paths, impact)
├── knowledge/
│   ├── __init__.py
│   ├── store.py                 # KnowledgeStore (ChromaDB)
│   ├── ingestion.py             # Document ingestion pipeline
│   └── retriever.py             # Semantic retrieval with reranking
├── memory/
│   ├── __init__.py
│   ├── store.py                 # MemoryStore (aiosqlite, Demo/POC)
│   ├── store_pg.py              # MemoryStorePG (asyncpg + Qdrant) [GA-2]
│   ├── incident.py              # Incident memory CRUD
│   ├── pattern.py               # LearnedPattern with time-decayed confidence
│   ├── config_memory.py         # ConfigMemory [GA-1]
│   └── factory.py               # create_memory_store() backend selector
├── ha/
│   ├── __init__.py
│   ├── heartbeat.py             # Redis leader election
│   └── replication.py           # State replication (active→standby)
├── slo/
│   ├── __init__.py
│   ├── metrics.py               # SLO metric collection
│   └── degradation.py           # 4-tier degradation policy [GA-7]
├── lifecycle/
│   ├── __init__.py
│   └── data_lifecycle.py        # Retention, archival, cleanup
└── tests/
    ├── test_ontology.py
    ├── test_knowledge.py
    ├── test_memory.py
    ├── test_ha.py
    └── test_slo.py
```

**Estimated**: ~25 Python files

---

### Agent E: Frontend

**Owns**: `sre_agent/frontend/`

**Reads**: Agent C's WebSocket event format, REST API contract (from `sre_agent/models/events.py`)

**Scope**:
```
sre_agent/frontend/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── src/
│   ├── App.tsx
│   ├── main.tsx
│   ├── routes.tsx
│   ├── api/
│   │   ├── client.ts            # Axios instance + interceptors
│   │   ├── ws.ts                # WebSocket client (reconnect, backpressure)
│   │   └── types.ts             # TypeScript types mirroring Pydantic models
│   ├── store/
│   │   ├── alertStore.ts        # Zustand alert state
│   │   ├── diagnosisStore.ts    # Diagnosis session state
│   │   └── topologyStore.ts     # Ontology graph state
│   ├── pages/
│   │   ├── Topology.tsx         # D3.js force-directed graph
│   │   ├── Alerts.tsx           # Alert dashboard + correlation view
│   │   ├── Diagnosis.tsx        # Real-time ThinkingStep timeline
│   │   ├── Remediation.tsx      # Canary progress, approval gates
│   │   ├── Chat.tsx             # Conversational agent interface
│   │   ├── Knowledge.tsx        # Knowledge base browser
│   │   ├── Memory.tsx           # Incident/pattern/config memory viewer
│   │   └── Skills.tsx           # Skill registry browser
│   ├── components/
│   │   ├── TopologyGraph.tsx    # D3 force simulation
│   │   ├── ThinkingTimeline.tsx # Step-by-step diagnosis visualization
│   │   ├── AlertTable.tsx       # Sortable, filterable alert table
│   │   ├── ApprovalDialog.tsx   # Human approval modal
│   │   ├── CanaryProgress.tsx   # Canary deployment progress bar
│   │   └── ChatMessage.tsx      # Chat bubble with tool call rendering
│   ├── hooks/
│   │   ├── useWebSocket.ts      # WS hook with auto-reconnect
│   │   └── usePolling.ts        # Fallback polling hook
│   └── locales/
│       ├── zh.json
│       └── en.json
└── tests/
    └── *.test.tsx
```

**Estimated**: ~35 TypeScript/TSX files

---

## 5. Dependency Graph and Execution Phases

```
Phase 0 (Day 1-2):    Main Agent
                       ├── Create sre_agent/ directory scaffold
                       ├── Pin dependencies (pyproject.toml / requirements.txt)
                       └── Write interface contract docs

Phase 1 (Day 3-5):    Agent A (Channels + Models)  ← BLOCKS all others
                       Agent E (Frontend scaffold)   ← independent, parallel

Phase 2 (Week 1-2):   ┌── Agent B (SRE Core)        ← needs Agent A models
                       ├── Agent D (Storage/Ontology) ← needs Agent A models
                       └── Agent E (pages)            ← continues independently

Phase 3 (Week 2-3):   Agent C (SRE Infra)           ← needs Agent B graph + Agent D storage

Phase 4 (Week 3-4):   All agents: extend, harden, cross-integration
                       Main Agent: integration tests, E2E demo scenarios

Phase 5 (Week 5-6):   Polish, demo rehearsal, docs
```

### Parallelism Matrix

| Phase | Agents Running | Max Parallel |
|-------|---------------|-------------|
| 0 | Main only | 1 |
| 1 | A + E | 2 |
| 2 | B + D + E | 3 |
| 3 | C (+ B/D extend) | 3 |
| 4 | All | 5 |
| 5 | Main + E (polish) | 2 |

---

## 6. Interface Contracts

### 6.1 External Inputs (from completed components)

**fault_injector → sre_agent** (Alert ingestion):
```json
{
  "scenario_id": "string",
  "root_cause": "RC-A|RC-B|RC-C|RC-D|RC-E|RC-F",
  "injected_at": "ISO8601",
  "affected_entities": ["entity_id"],
  "symptoms": [{"metric": "string", "value": 0.0, "threshold": 0.0}]
}
```

**Prometheus → sre_agent** (Metric alerts):
```json
{
  "status": "firing",
  "labels": {"alertname": "string", "severity": "string", "instance": "string"},
  "annotations": {"summary": "string", "description": "string"},
  "startsAt": "ISO8601"
}
```

### 6.2 Internal Contracts (between agents)

**Agent A → All: `sre_agent/models/`**
```python
# All agents import these; only Agent A creates them
from sre_agent.models.alert import Alert, AlertSeverity
from sre_agent.models.diagnosis import DiagnosisResult, ThinkingStep
from sre_agent.models.remediation import RemediationPlan, RemediationAction
from sre_agent.models.ontology import OntologyNode, OntologyEdge
from sre_agent.models.events import WSEvent, EventType
from sre_agent.models.common import SREResponse, ErrorCode, SafetyLevel
```

**Agent B → Agent C: Agent graph interface**
```python
# Agent C imports the compiled graph to serve via API
from sre_agent.agent.graph import create_sre_graph
# Agent C calls: graph.ainvoke(state) to run diagnosis
```

**Agent D → Agent B: Storage interface (tools call storage)**
```python
# Agent B tools import storage interfaces
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.knowledge.store import KnowledgeStore
from sre_agent.memory.store import MemoryStore
```

**Agent C → Agent E: WebSocket events**
```json
{
  "type": "thinking_step|diagnosis_result|remediation_progress|alert|error",
  "session_id": "string",
  "timestamp": "ISO8601",
  "data": {}
}
```

---

## 7. Integration Checkpoints

| Checkpoint | When | Validates | Pass Criteria |
|-----------|------|-----------|---------------|
| IC-1 | Phase 1 done | Channels + models | All 7 channel files importable; model serialization round-trips pass; frontend `npm run dev` serves |
| IC-2 | Phase 2 done | Core + storage | SRE graph processes mock alert end-to-end; OntologyGraph CRUD works; ChromaDB stores/retrieves doc |
| IC-3 | Phase 3 done | Full backend | REST API serves; WS pushes ThinkingSteps; remediation dry-run completes with WAL; LoopOrchestrator handles multi-candidate |
| IC-4 | Phase 4 done | E2E demo | Demo Case 1 (vLLM latency) runs: alert→diagnosis→remediation→frontend display; human sees trace in browser |
| IC-5 | Phase 5 done | All demos | 4 demo cases pass; 7 GUI pages functional; docs complete |

---

## 8. Coordination Protocol

### 8.1 Main Agent Responsibilities

```python
# Orchestration pseudocode
def orchestrate():
    # Phase 0
    scaffold_sre_agent_directories()
    write_pyproject_toml()  # pin langgraph, nemoguardrails, chromadb, etc.

    # Phase 1 — blocking prerequisite
    agent_a = launch(AgentA, worktree=True)  # channels + models
    agent_e = launch(AgentE, worktree=True, background=True)  # frontend scaffold
    wait(agent_a)  # MUST complete before Phase 2
    merge(agent_a)
    checkpoint("IC-1")

    # Phase 2 — max parallelism
    agent_b = launch(AgentB, worktree=True)  # SRE core
    agent_d = launch(AgentD, worktree=True)  # storage/ontology
    wait(agent_e)  # frontend scaffold done
    merge(agent_e)
    wait(agent_b, agent_d)
    merge(agent_b, agent_d)  # sequential merge, resolve __init__.py
    checkpoint("IC-2")

    # Phase 3 — dependent on B+D
    agent_c = launch(AgentC, worktree=True)  # infra/remediation/API
    agent_b2 = resume(AgentB, "extend guardrails + conversational")  # background
    agent_d2 = resume(AgentD, "extend memory + HA")  # background
    wait(agent_c, agent_b2, agent_d2)
    merge_all()
    checkpoint("IC-3")

    # Phase 4 — integration
    agent_e2 = resume(AgentE, "connect to real API, all 7 pages")
    write_e2e_tests()
    run_demo_cases()
    checkpoint("IC-4")

    # Phase 5 — polish
    final_integration()
    checkpoint("IC-5")
```

### 8.2 Worktree Strategy

- Each agent gets `isolation: "worktree"` — branches from latest merged state
- Agents work exclusively on owned files (no overlapping modifications)
- Main agent merges worktrees sequentially after each phase
- Conflict zone: only `sre_agent/__init__.py` — owned by main agent

### 8.3 Agent Resumption

Agents B, D, E span multiple phases. Use Claude Code's `resume` parameter to continue with preserved context:
- Agent B: Phase 2 (core) → Phase 3 (guardrails + conversational) → Phase 4 (polish)
- Agent D: Phase 2 (ontology + knowledge) → Phase 3 (memory + HA + SLO) → Phase 4 (production backends)
- Agent E: Phase 1 (scaffold) → Phase 2 (pages) → Phase 4 (real API integration)

---

## 9. Agent Prompt Templates

### 9.1 Agent A: Channels + Models

```
You are building the foundation layer for the AIDC Auto-SRE system.

CONTEXT:
- Read lib/channels/base.py for the existing BaseChannel pattern
- Read AIDC-auto-SRE.md §6 (Channels), §3.1 (State models), §5 (Remediation models),
  §7 (Ontology entities), §8 (Alert models) for specifications
- load_simulator/ and fault_injector/ are complete; you're building sre_agent/

TASK 1 — Shared Channels (lib/channels/):
Create 7 new channel files extending BaseChannel:
- ssh.py, redfish.py, switch.py, log.py, alert.py, ontology.py, knowledge.py
Each must: extend BaseChannel, async connect/disconnect/health_check, unit tested

TASK 2 — Pydantic Models (sre_agent/models/):
Create the shared model layer that ALL other agents will import:
- alert.py, diagnosis.py, remediation.py, ontology.py, memory.py, events.py, common.py
These are the source of truth. Include serialization round-trip tests.

DO NOT create files outside lib/channels/ and sre_agent/models/.
```

### 9.2 Agent B: SRE Core

```
You are implementing the core SRE agent for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §3 (LangGraph), §4 (ToolRegistry), §10 (Skills),
  §16 (ConversationalAgent), §17 (NeMo Guardrails)
- Import models from sre_agent/models/ (DO NOT MODIFY)
- Import channels from lib/channels/ (DO NOT MODIFY)

CREATE these directories:
- sre_agent/agent/ — LangGraph StateGraph with SREAgentState
- sre_agent/tools/ — ToolRegistry + readonly/write tool modules
- sre_agent/skills/ — SkillRegistry, executor, policy, 6 builtin SKILL.md
- sre_agent/guardrails/ — NeMo Guardrails as OPTIONAL layer
- sre_agent/config.py, sre_agent/cli.py

KEY REQUIREMENTS:
1. LangGraph: 4-node cycle (reason→act→observe→decide) with conditional routing
2. ToolRegistry: safety levels (readonly/low/medium/high/critical), LangChain @tool
3. NeMo: optional — core ReAct must work WITHOUT guardrails loaded
4. Skills: registry + executor with 4 fixed @tools, 6 builtin SKILL.md files
5. ConversationalAgent: own LangGraph subgraph, chat history, streaming

Write unit tests. Mock all external deps (K8s, Prometheus, GPU, channels).
```

### 9.3 Agent C: SRE Infrastructure

```
You are implementing the infrastructure layer for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §5 (Remediation), §5.6 (LoopOrchestrator),
  §5.7 (IncidentHandler), §11 (Concurrency), §13 (API), §15 (Auth), §18 (NAT)
- Import from sre_agent/models/ (DO NOT MODIFY)
- Import from sre_agent/agent/graph.py: create_sre_graph (DO NOT MODIFY)
- Import from sre_agent/ontology/, sre_agent/memory/ (DO NOT MODIFY)

CREATE these directories:
- sre_agent/remediation/ — Engine, LoopOrchestrator, IncidentHandler, WAL, canary, approval
- sre_agent/concurrency/ — ResourceLock, AlertDeduplicator, AlertCorrelator
- sre_agent/auth/ — JWT, RBAC
- sre_agent/api/ — FastAPI routes, WebSocket streaming
- sre_agent/server.py — App factory
- sre_agent/safety/, sre_agent/nat/

KEY REQUIREMENTS:
1. RemediationEngine: WAL-backed, canary with rollback, human approval gates
2. LoopOrchestrator: multi-candidate diagnosis-remediation cycle
3. IncidentHandler: alert→correlate→diagnose→remediate full pipeline
4. API: REST + WebSocket, ThinkingStep streaming to frontend
5. Auth: JWT + RBAC with safety-level-based tool access

Write unit tests. Mock storage and agent graph.
```

### 9.4 Agent D: Storage & Ontology

```
You are implementing the data/storage layer for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §7 (Ontology), §8 (Knowledge), §9 (Memory),
  §19 (HA), §20 (SLO)
- Import from sre_agent/models/ (DO NOT MODIFY)
- Import from lib/channels/ for scanner data sources (DO NOT MODIFY)

CREATE these directories:
- sre_agent/ontology/ — OntologyGraph (NetworkX + aiosqlite), 4 scanners, query helpers
- sre_agent/knowledge/ — KnowledgeStore (ChromaDB), ingestion, retriever
- sre_agent/memory/ — MemoryStore (aiosqlite), incident, pattern, config_memory, factory
- sre_agent/ha/ — Redis leader election, state replication
- sre_agent/slo/ — SLO metrics, 4-tier degradation
- sre_agent/lifecycle/ — Data retention, archival

KEY REQUIREMENTS:
1. OntologyGraph: NetworkX in-memory + aiosqlite persistence, CRUD, neighbor/path queries
2. KnowledgeStore: ChromaDB with document ingestion and semantic retrieval
3. MemoryStore: aiosqlite backend, LearnedPattern with time-decayed confidence
4. HA: Active/Standby via Redis SETNX leader election
5. SLO: 4-tier degradation (full→degraded→minimal→emergency)

Write unit tests. Use aiosqlite :memory: and ChromaDB ephemeral for tests.
```

### 9.5 Agent E: Frontend

```
You are building the SRE Agent frontend for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §14 (GUI Design) for all 7 page specifications
- Read sre_agent/models/events.py for WebSocket event types
- Tech stack: Vite + React 18 + TypeScript + Zustand + Ant Design 5

CREATE: sre_agent/frontend/ — complete React application

PAGES (7):
1. Topology — D3.js force-directed graph of AIDC entities + relationships
2. Alerts — Alert dashboard with severity filtering, correlation grouping
3. Diagnosis — Real-time ThinkingStep timeline (WebSocket-fed)
4. Remediation — Canary progress bars, approval gate dialogs
5. Chat — Conversational agent with tool call rendering
6. Knowledge — Knowledge base document browser with search
7. Memory — Incident/pattern/config memory viewer

KEY REQUIREMENTS:
1. WebSocket client with auto-reconnect and backpressure handling
2. Zustand stores for each domain (alerts, diagnosis, topology)
3. i18n (zh/en) via react-i18next
4. Ant Design 5 components; responsive layout
5. Start with table/tree for topology; D3 force graph as enhancement

Use MSW (Mock Service Worker) for API mocking in development/tests.
```

---

## 10. Risk Mitigation

| Risk | Prob | Impact | Mitigation |
|------|------|--------|------------|
| NeMo Guardrails blocks LangGraph | Med | High | Agent B implements as optional decorator; core works without NeMo |
| D3.js topology complexity | High | Med | Agent E starts with Ant Design Tree; D3 added in Phase 4 |
| LangChain/LangGraph/NeMo version clash | Med | High | Pin versions in Phase 0; test in isolation first |
| Agent merge conflicts on __init__.py | Low | Med | Main agent owns all __init__.py; agents never modify them |
| Context overflow for Agent B | Med | Med | Split into sub-sessions: (a) graph+state, (b) tools, (c) skills, (d) guardrails |
| Frontend blocked on API | High | Med | MSW mocks from Day 1; OpenAPI spec defined in Phase 0 |
| ChromaDB/aiosqlite test flakiness | Med | Low | Use ephemeral/in-memory backends; deterministic test data |

---

## 11. Gap Coverage Summary

| Gap | Agent | File | Phase |
|-----|-------|------|-------|
| GA-1 ConfigMemory | D | `memory/config_memory.py` | 3 |
| GA-2 MemoryStorePG | D | `memory/store_pg.py` | 4 |
| GA-3 SkillCreator | B | `skills/creator.py` | 3 |
| GA-4 4 builtin skills | B | `skills/builtin/{gpu,network,storage,platform}` | 2 |
| GA-5 ConversationalAgent | B | `agent/conversational.py` | 3 |
| GA-6 Alert.silence_alert | A | `lib/channels/alert.py` | 1 |
| GA-7 SLO 4-tier degradation | D | `slo/degradation.py` | 3 |

---

## 12. Estimated Totals

| Agent | Python Files | Other Files | Tests |
|-------|-------------|-------------|-------|
| A: Channels + Models | 14 | — | 6 |
| B: SRE Core | 28 | 4 .co + 6 SKILL.md | 4 |
| C: SRE Infra | 22 | — | 5 |
| D: Storage/Ontology | 22 | — | 5 |
| E: Frontend | — | ~35 TSX | ~8 |
| **Total** | **~86** | **~45** | **~28** |
