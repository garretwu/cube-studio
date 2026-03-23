# AIDC Auto-SRE Multi-Agent Implementation Plan

> Scope: **sre_agent only** — `load_simulator/` and `fault_injector/` are complete
> Based on gap analysis between `AIDC-auto-SRE.md` and `aidc-auto-sre-plan.md` v2.7
> Generated: 2026-03-05 | Updated: 2026-03-10

**定位**: 本文是 `aidc-auto-sre-plan.md`（主计划）的 **sre_agent 子计划**，不替代主计划。
仅拆分 `sre_agent` 的并行开发策略、Agent 分工与执行阶段。

**继承关系**: 继承 `aidc-auto-sre-plan.md` 的 Gate、联动契约与验收标准。
主计划中 Gate-1 ~ Gate-4、15 维验收矩阵、Demo Case 定义等均为本文的上游约束。
主计划版本基线: v2.7。

---

## 1. Gap Analysis (SRE Agent Scope Only)

### 1.1 Design Doc Features Not Covered in Plan

| ID | Gap | Design Doc Section | Severity | Phase |
|----|-----|--------------------|----------|-------|
| GA-1 | **ConfigMemory** (AIDC baselines, thresholds, custom rules) — v2.7 已在主计划显式落位，子计划细化到 Agent D / Phase 3 | §9 | Low | Phase 3 (POC) |
| GA-2 | **MemoryStorePG** (asyncpg + Qdrant production backend) — v2.7 已在主计划显式落位，子计划细化为 Prod interface / migration boundary | §9.4 | Low | Prod Phase (Demo only needs interface) |
| GA-3 | **SkillCreator** (auto-generate SKILL.md from traces) — v2.7 已在主计划显式落位，子计划细化到 Agent B2 / Phase 3 | §10.11 | Low | Phase 3 |
| GA-4 | **6 builtin skills** (vllm, rdma, gpu-health, network-diagnosis, storage-diagnosis, platform-health) — v2.7 已与主计划对齐，子计划保持 6 skill 全量交付 | §10.10 | Low | Phase 2 |
| GA-5 | **ConversationalAgent** underweighted — full LangGraph subgraph + chat history + streaming is a bullet, not a Track | §16 | Medium | Phase 2 (covered by Agent B1) |
| GA-6 | **Alert.silence_alert()** integration into remediation flow — v2.7 已在主计划显式落位，子计划细化到 Agent A / Phase 1 | §6.6 | Low | Phase 1 |
| GA-7 | **SLO degradation 4-tier logic** — 主计划已落位到 Prod，加强项仍以子计划给出实现边界与验收接口 | §20.2 | Low | Prod Phase (Demo only needs interface) |

### 1.2 Plan Items Without Design Doc Coverage

> GB-1 (Helm Chart) and GB-2 (Playwright E2E) removed — noise gaps not relevant to sre_agent scope.

*No actionable gaps remain in this category.*

### 1.3 Key Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| NeMo Guardrails + LangGraph version conflict | High | Implement as optional layer; core ReAct works without NeMo |
| Frontend 8 pages + D3.js topology for single track | Medium | Start with table/tree; D3 as enhancement |
| Channel duplication (lib/ vs sre_agent/) | Medium | SRE channels in lib/; sre_agent imports only |
| Agent B scope too large (original single-agent) | High | Split into B1 (core) + B2 (tools/skills/guardrails) for parallel execution |

---

## 2. Assumptions

- `load_simulator/` is complete (219 tests passed) and produces JSON metrics output
- `fault_injector/` is complete (198 tests passed) with scenarios RC-1~RC-6, F-1~F-6
- `lib/channels/` is the established shared channel layer (~2,646 lines) with **8 existing implementations**:

| File | Lines | Description | Created By |
|------|-------|-------------|------------|
| `base.py` | — | BaseChannel ABC + SafetyViolationError | shared |
| `cube_studio.py` | 283 | REST API + auth | load_simulator |
| `kubernetes.py` | 251 | K8s API | load_simulator |
| `prometheus.py` | 172 | PromQL queries | load_simulator |
| `ssh.py` | 324 | asyncssh + connection pool + shlex.quote safety | fault_injector |
| `redfish.py` | 733 | BMC/IPMI session management | fault_injector |
| `ipmi.py` | 263 | IPMI raw commands | fault_injector |
| `switch.py` | 461 | H3C NETCONF dual mode | fault_injector |

- **ssh.py, redfish.py, ipmi.py, switch.py are already implemented** by fault_injector and fully tested
- Agent A only needs to create: **log.py, alert.py, ontology.py, knowledge.py** (4 new channels)
- All new development targets `sre_agent/` directory + extensions to `lib/channels/`

---

## 3. Multi-Agent Strategy: 6 Agents + Coordinator

### 3.1 Why 6 Agents

With fault_injector removed, the scope narrows to sre_agent subsystems. The original Agent B (SRE Core) was too heavy — covering graph/state, tools, skills, guardrails, conversational agent, config, and CLI (~35 files). Splitting into B1 + B2 enables parallel execution and reduces context window pressure.

The decomposition follows **disjoint file ownership** with maximum parallelism:

| Constraint | Implication |
|-----------|-------------|
| Agents share git repo | Use worktrees (`isolation: "worktree"`) for parallel work |
| Context window ~200K | Each agent bounded to ~15-30 files |
| No two agents modify same file | Main agent owns `__init__.py` and shared models |
| Dependencies explicit | Pydantic models defined upfront; agents import, never modify |
| B1 and B2 run in parallel | Both depend on Agent A; no cross-dependency between them |

### 3.2 Agent Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    Main Agent (Coordinator)                       │
│     shared models, scaffolding, integration, merges              │
├──────────┬──────────┬──────────┬──────────┬──────────┬──────────┤
│ Agent A  │ Agent B1 │ Agent B2 │ Agent C  │ Agent D  │ Agent E  │
│ Channels │ SRE Core │ Tools &  │ SRE Infra│ Storage  │ Frontend │
│ +Models  │ LangGraph│ Skills   │ Remed/API│ Onto/Mem │ React    │
│          │ State    │ Guards   │ Concurr  │ KB/HA/SLO│ 8 pages  │
│          │ Convers. │ Registry │ Auth     │          │          │
│          │ Config   │          │          │          │          │
│          │ CLI      │          │          │          │          │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
```

---

## 4. Agent Assignments

### Agent A: Shared Channels + Models (Prerequisite)

**Owns**: New files in `lib/channels/`, `sre_agent/models/`

**Scope**:
```
lib/channels/
├── [已有, 复用] base.py, cube_studio.py, kubernetes.py, prometheus.py
├── [已有, 复用] ssh.py, redfish.py, ipmi.py, switch.py
├── log.py              # NEW — Log aggregation (Loki/ES)
├── alert.py            # NEW — Alert channel + silence_alert() [GA-6]
├── ontology.py         # NEW — Ontology graph query channel
└── knowledge.py        # NEW — Knowledge retrieval channel

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
├── test_log.py         # NEW — only new channel tests here
└── test_alert.py       # NEW — only new channel tests here

sre_agent/tests/
└── test_models.py      # Serialization round-trip tests
```

**Channel test strategy** (G-MA-14): Agent A adds only new channel tests to `lib/tests/`. Existing ssh/redfish/switch/ipmi tests remain in `fault_injector/tests/unit/features/channels/` — no duplication.

**Estimated**: ~12 Python files + tests (4 new channels + 7 model files + tests)

**Runs FIRST** — all other agents depend on these models and channels.

---

### Agent B1: SRE Agent Core (Graph + State + Conversational)

**Owns**: `sre_agent/agent/`, `sre_agent/config.py`, `sre_agent/cli.py`

**Reads**: `sre_agent/models/`, `lib/channels/`

**Scope**:
```
sre_agent/
├── config.py                    # SREConfig Pydantic settings
├── cli.py                       # Click CLI entry point (incl. --resume)
├── agent/
│   ├── __init__.py
│   ├── sre_agent.py             # Main agent entry (graph assembly + runtime wiring)
│   ├── state.py                 # SREAgentState TypedDict
│   ├── graph.py                 # LangGraph StateGraph (reason→act→observe→decide)
│   ├── nodes.py                 # Node functions
│   ├── prompts.py               # System prompts (base, diagnosis, remediation)
│   ├── discovery_agent.py       # Deterministic topology discovery agent
│   ├── monitor_agent.py         # Deterministic monitor / threshold agent
│   ├── conversational_agent.py  # ConversationalAgent [GA-5] — LangGraph subgraph
│   ├── thinking_trace.py        # ThinkingStep / observation trace helpers
│   └── checkpoint.py            # LangGraph AsyncSqliteSaver persistence + --resume [G-MA-06]
└── tests/
    ├── test_graph.py
    ├── test_conversational_agent.py
    └── test_checkpoint.py
```

**Key requirements**:
1. LangGraph: 4-node cycle (reason→act→observe→decide) with conditional routing
2. ConversationalAgent: own LangGraph subgraph, chat history, streaming
3. Checkpoint persistence: AsyncSqliteSaver for `sre_agent --resume <session_id>` [G-MA-06]
4. CLI: Click entry point with `--resume` flag

**Estimated**: ~15 Python files

**Critical path**: LangGraph skeleton must work before Agent C can integrate remediation.

---

### Agent B2: Tools, Skills & Guardrails

**Owns**: `sre_agent/tools/`, `sre_agent/skills/`, `sre_agent/guardrails/`

**Reads**: `sre_agent/models/`, `lib/channels/`

**Scope**:
```
sre_agent/
├── tools/
│   ├── __init__.py
│   ├── registry.py              # ToolRegistry with safety levels
│   ├── definitions.py           # Tool definition registry / JSON schema
│   ├── readonly/
│   │   ├── k8s.py               # kubectl get/describe/logs
│   │   ├── prometheus.py        # PromQL queries
│   │   ├── gpu.py               # nvidia-smi, DCGM metrics
│   │   ├── network.py           # RDMA, switch port stats
│   │   ├── logs.py              # Log aggregation / parsing tools
│   │   ├── bmc.py               # Redfish / IPMI readonly tools
│   │   ├── platform.py          # Cube Studio / platform readonly tools
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
│       ├── gpu-health/SKILL.md           # [GA-4]
│       ├── network-diagnosis/SKILL.md    # [GA-4]
│       ├── storage-diagnosis/SKILL.md    # [GA-4]
│       └── platform-health/SKILL.md      # [GA-4]
├── guardrails/
│   ├── __init__.py
│   ├── config.yml               # NeMo Guardrails config (YAML required by NeMo SDK)
│   ├── prompts.yml              # NeMo Guardrails prompt templates
│   ├── rails/
│   │   ├── input.co
│   │   ├── output.co
│   │   ├── execution.co
│   │   └── dialog.co
│   └── actions.py               # Custom NeMo actions
└── tests/
    ├── test_tools.py
    ├── test_guardrails.py
    └── test_skills.py
```

**Key requirements**:
1. ToolRegistry: safety levels (readonly/low/medium/high/critical), LangChain @tool
2. NeMo Guardrails: optional decorator — core ReAct must work WITHOUT guardrails loaded
3. Skills: registry + executor with 4 fixed @tools, **all 6 builtin SKILL.md files** [GA-4 full coverage]
4. SkillCreator: auto-generate SKILL.md from traces [GA-3, Phase 3]

**Estimated**: ~25 Python files + 4 .co + 6 SKILL.md

---

### Agent C: SRE Infrastructure (Remediation + API + Auth)

**Owns**: `sre_agent/remediation/`, `sre_agent/concurrency/`, `sre_agent/auth/`, `sre_agent/safety/`, `sre_agent/server.py`, `sre_agent/api/`, `sre_agent/nat/`

**Reads**: `sre_agent/models/`, `sre_agent/agent/` (imports graph for API integration)

**SafetyGuard inheritance** (G-MA-08): Agent C must inherit the SafetyGuard pattern from `fault_injector/safety/guard.py` and `lib/channels/base.py` SafetyViolationError. Specifically:
- `sre_agent/tools/write/` must **hard-block** BMC VLAN/MTU writes without explicit approval
- `sre_agent/remediation/validator.py` must check blast radius against SafetyGuard rules
- `sre_agent/safety/blast_radius.py` must reuse SafetyViolationError from `lib/channels/base.py`

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
│   ├── planner.py               # Remediation planner / action sequencing
│   ├── loop_orchestrator.py      # LoopOrchestrator (multi-candidate cycle)
│   ├── incident_handler.py      # IncidentHandler (alert→diagnosis→remediation)
│   ├── wal.py                   # Write-ahead log
│   ├── canary.py                # Canary deployment logic
│   ├── approval.py              # Human approval gates
│   └── validator.py             # PlanValidator (blast radius + SafetyGuard rules)
├── concurrency/
│   ├── __init__.py
│   ├── resource_lock.py         # ResourceLock (Demo: asyncio.Lock, Prod: Redis Redlock)
│   ├── alert_dedup.py           # AlertDeduplicator
│   └── alert_correlator.py      # AlertCorrelator (time-window grouping)
├── auth/
│   ├── __init__.py
│   ├── jwt.py                   # JWT token handling
│   ├── rbac.py                  # Role-based access control
│   └── secrets.py               # K8s Secret / Vault credential management + auto-rotation
├── safety/
│   ├── __init__.py
│   ├── guard.py                 # SafetyGuard adapter / guardrail bridge
│   ├── forbidden.py             # Forbidden operations / high-risk writes
│   └── blast_radius.py          # Blast radius calculation (inherits SafetyViolationError)
├── nat/
│   ├── __init__.py
│   ├── wrapper.py               # NeMo Agent Toolkit profiling
│   ├── workflow.yml             # NAT workflow definition
│   └── eval_dataset.jsonl       # NAT eval dataset
└── tests/
    ├── test_remediation.py
    ├── test_loop_orchestrator.py
    ├── test_concurrency.py
    ├── test_api.py
    └── test_auth.py
```

**Estimated**: ~28 Python files

---

### Agent D: Storage & Ontology (Data Layer)

**Owns**: `sre_agent/ontology/`, `sre_agent/knowledge/`, `sre_agent/memory/`, `sre_agent/ha/`, `sre_agent/slo/`, `sre_agent/lifecycle/`

**Reads**: `sre_agent/models/`, `lib/channels/`

**Phase tagging** (G-MA-13):

| Sub-module | Phase | Notes |
|-----------|-------|-------|
| `ontology/` (graph + scanners + query) | Demo | Full implementation |
| `knowledge/` (store + ingestion + retriever) | Demo | Full implementation |
| `memory/` (store + incident + pattern + factory) | Demo | Full implementation (aiosqlite backend) |
| `memory/config_memory.py` [GA-1] | POC | ConfigMemory with AIDC baselines |
| `memory/store_pg.py` [GA-2] | **Prod** | Interface only in Phase 2-3; full asyncpg + Qdrant implementation deferred |
| `ha/` (heartbeat + replication) | **Prod** | Interface only; full Redis leader election deferred |
| `slo/` (metrics + degradation) [GA-7] | **Prod** | Interface only; 4-tier degradation deferred |
| `lifecycle/` (data_lifecycle) | **Prod** | Interface only; retention/archival deferred |

**Scope**:
```
sre_agent/
├── ontology/                    # [Demo]
│   ├── __init__.py
│   ├── models.py                # Ontology entities / edge models
│   ├── graph.py                 # OntologyGraph (NetworkX + aiosqlite)
│   ├── store.py                 # Persistence adapter / graph store
│   ├── entities.py              # Entity type registration
│   ├── discovery/
│   │   ├── __init__.py
│   │   ├── k8s_scanner.py       # K8s topology discovery
│   │   ├── switch_scanner.py    # Network topology (LLDP/switch)
│   │   ├── bmc_scanner.py       # BMC / hardware discovery
│   │   └── prometheus_scanner.py# Prometheus target discovery
│   └── query.py                 # Graph query helpers (neighbors, paths, impact)
├── knowledge/                   # [Demo]
│   ├── __init__.py
│   ├── store.py                 # KnowledgeStore (ChromaDB)
│   ├── ingest.py                # Document ingestion pipeline
│   ├── chunker.py               # Chunking / splitting
│   ├── runbook.py               # Runbook parsing / loading
│   └── retriever.py             # Semantic retrieval with reranking
├── memory/
│   ├── __init__.py
│   ├── store.py                 # MemoryStore (aiosqlite) [Demo]
│   ├── store_pg.py              # MemoryStorePG (asyncpg + Qdrant) [Prod — interface only]
│   ├── incident.py              # Incident memory CRUD [Demo]
│   ├── pattern.py               # LearnedPattern with time-decayed confidence [Demo]
│   ├── config_memory.py         # ConfigMemory [GA-1, POC]
│   └── factory.py               # create_memory_store() backend selector [Demo]
├── ha/                          # [Prod — interface only]
│   ├── __init__.py
│   ├── heartbeat.py             # Heartbeat + health check
│   ├── leader.py                # Redis SETNX leader election
│   └── replication.py           # State replication (active→standby)
├── slo/                         # [Prod — interface only]
│   ├── __init__.py
│   ├── metrics.py               # SLO metric collection
│   └── degradation.py           # 4-tier degradation policy [GA-7]
├── lifecycle/                   # [Prod — interface only]
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
│   │   └── Skills.tsx           # Skill registry browser [G-MA-10b]
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

**Pages (8)**: Topology, Alerts, Diagnosis, Remediation, Chat, Knowledge, Memory, Skills

**Key requirements**:
1. WebSocket client with auto-reconnect and backpressure handling
2. Zustand stores for each domain (alerts, diagnosis, topology)
3. i18n (zh/en) via react-i18next
4. Ant Design 5 components; responsive layout
5. Start with table/tree for topology; D3 force graph as enhancement

**Estimated**: ~35 TypeScript/TSX files

Use MSW (Mock Service Worker) for API mocking in development/tests.

---

## 5. Dependency Graph and Execution Phases

```
Phase 0 (Day 1-2):    Main Agent
                       ├── Create sre_agent/ directory scaffold
                       ├── Pin dependencies (pyproject.toml / requirements.txt)
                       └── Write interface contract docs

Phase 1 (Day 3-5):    Agent A (Channels + Models)  ← BLOCKS all others
                       Agent E (Frontend scaffold)   ← independent, parallel

Phase 2 (Week 1-2):   ┌── Agent B1 (SRE Core)        ← needs Agent A models
                       ├── Agent B2 (Tools/Skills)     ← needs Agent A models, parallel with B1
                       ├── Agent D (Storage/Ontology)  ← needs Agent A models
                       └── Agent E (pages)             ← continues independently

Phase 3 (Week 2-3):   Agent C (SRE Infra)            ← needs Agent B1 graph + Agent D storage

Phase 4 (Week 3-4):   All agents: extend, harden, cross-integration
                       Main Agent: integration tests, E2E demo scenarios

Phase 5 (Week 5-6):   Polish, demo rehearsal, docs
```

### Parallelism Matrix

| Phase | Agents Running | Max Parallel |
|-------|---------------|-------------|
| 0 | Main only | 1 |
| 1 | A + E | 2 |
| 2 | B1 + B2 + D + E | 4 |
| 3 | C (+ B1/B2/D extend) | 4 |
| 4 | All | 6 |
| 5 | Main + E (polish) | 2 |

### 5.1 Phase → Main Plan Mapping Table

| Multi-Agent Phase | Main Plan Phase | Main Plan Sprint/Track | Gate |
|---|---|---|---|
| Phase 0 (Day 1-2) | Demo Sprint 1 | Track E (scaffolding) | — |
| Phase 1 (Day 3-5) | Demo Sprint 1-2 | Track C (channels) | — |
| Phase 2 (Week 1-2) | Demo Sprint 2-3 | Track C (core) + Track D (pages) + Track E (storage) | Gate-1/Gate-2 (inherited, already passed) |
| Phase 3 (Week 2-3) | Demo Sprint 3 | Track C (remediation/API) | — |
| Phase 4 (Week 3-4) | Demo Sprint 3-4 | Track C+D+E (integration) | Gate-3 |
| Phase 5 (Week 5-6) | Demo Sprint 4 (acceptance) | Track D+E (polish) | Gate-4 (Demo) |

---

## 6. Interface Contracts

### 6.1 External Inputs (from completed components)

**fault_injector → sre_agent** (Alert ingestion):
```json
{
  "schema_version": "1.0",
  "scenario_id": "string",
  "root_cause": "RC-A|RC-B|RC-C|RC-D|RC-E|RC-F",
  "injected_at": "ISO8601",
  "affected_entities": ["entity_id"],
  "symptoms": [{"metric": "string", "value": 0.0, "threshold": 0.0}]
}
```

**Transport mechanism** (G-MA-12):
- **Primary**: Prometheus Alertmanager webhook → sre_agent `/api/v1/alerts` POST endpoint
- **Fallback**: File-based polling (for offline/demo scenarios — watch `data/alerts/*.json`)
- **Retry strategy**: Exponential backoff (1s, 2s, 4s, max 30s), dead-letter to `data/alerts/dlq/`

**Prometheus → sre_agent** (Metric alerts):
```json
{
  "schema_version": "1.0",
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

**Agent B1 → Agent C: Agent graph interface**
```python
# Agent C imports the compiled graph to serve via API
from sre_agent.agent.graph import create_sre_graph
# Agent C calls: graph.ainvoke(state) to run diagnosis
```

**Agent B1 → Agent C: Checkpoint contract** (G-MA-06)
```python
# sre_agent checkpoint persistence via LangGraph AsyncSqliteSaver
from sre_agent.agent.checkpoint import create_checkpointer
# Checkpoint DB: data/checkpoints/sre_agent.db
# Resume: sre_agent --resume <session_id> → loads checkpoint → continues graph
```

**Agent D → Agent B1/B2: Storage interface (tools call storage)**
```python
# Agent B1/B2 tools import storage interfaces
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.knowledge.store import KnowledgeStore
from sre_agent.memory.store import MemoryStore
```

**Agent C → Agent E: WebSocket events**
```json
{
  "schema_version": "1.0",
  "type": "thinking_step|tool_call|tool_result|diagnosis_result|approval_required|loop_start|loop_progress|remediation_progress|alert|error|done",
  "session_id": "string",
  "timestamp": "ISO8601",
  "data": {}
}
```

---

## 7. Integration Checkpoints

| Checkpoint | When | Validates | Pass Criteria | Gate Mapping |
|-----------|------|-----------|---------------|-------------|
| IC-1 | Phase 1 done | Channels + models | All 4 new channel files importable; 8 existing channels verified; model serialization round-trips pass; frontend `npm run dev` serves | — |
| IC-2 | Phase 2 done | Core + storage | SRE graph processes mock alert end-to-end; OntologyGraph CRUD works; ChromaDB stores/retrieves doc; 6 builtin SKILL.md validated | — |
| IC-3 | Phase 3 done | Full backend | REST API serves; WS pushes ThinkingSteps; remediation dry-run completes with WAL; LoopOrchestrator handles multi-candidate; `--resume` checkpoint round-trip; BMC write guard blocks dangerous ops | **Gate-3** |
| IC-4 | Phase 4 done | E2E demo | Case 1 RC-A (vLLM GPU争抢) E2E: alert→diagnosis→remediation→frontend display; human sees trace in browser | — |
| IC-5 | Phase 5 done | All demos | Case 1 (RC-A + RC-B) + Case 2 (RC-A + RC-B) **必做**; Case 3 (LoopOrchestrator) **stretch**; **8 GUI pages functional**; Skills/知识库/记忆库 **三页硬性验收单独过线**; docs complete | **Gate-4** |

### 7.1 15-Dimension Acceptance Matrix Mapping

Maps each of the 15 acceptance dimensions (from `AIDC-auto-SRE.md` Appendix C) to responsible Agent and Phase.

| 维度 | 责任 Agent | Phase | 验证方式 |
|------|-----------|-------|----------|
| **安全性** (无动态代码执行/shell注入) | B2 (tools) + C (API) | 3-4 | bandit/semgrep 扫描 |
| **契约一致性** (schema contract test) | A (models) + C (API) | 1-3 | Pydantic SREResponse CI 测试 |
| **权限有效性** (越权拦截) | C (auth/rbac) | 3 | require_role() 单测 + 渗透测试 |
| **并发安全性** (重复告警无冲突) | C (concurrency) | 3 | ResourceLock + AlertDeduplicator 压测 |
| **正确性** (影响面识别 ≥90%) | B1 (graph) + D (ontology) | 4 | Demo 1/2/3 + blast_radius 单测 |
| **故障注入验收** (诊断准确率 ≥80%) | B1 (graph) + B2 (skills) | 4 | fault_injector 注入 → Agent 诊断 → 比对 |
| **稳定性** (10 并发无阻塞) | B1 (graph) + C (concurrency) | 4 | asyncio 监控 + max_concurrent 压测 |
| **可执行性** (修复计划通过率 ≥95%) | C (remediation/validator) | 3-4 | PlanValidator 单测 + LLM 回归 |
| **可回滚性** (WAL 回滚演练) | C (remediation/wal) | 3 | WAL recover_all 集成测试 |
| **成本可控** (单次诊断 ≤100K token) | B1 (graph) + C (nat) | 4 | NeMo Agent Toolkit profiling |
| **可用性** (RTO<30s RPO=0) | D (ha) | **Prod** | HA 故障切换演练 |
| **可追责性** (审计可检索/验签) | C (API/middleware) | 4 | audit.jsonl hash chain 验证 |
| **降级验收** (LLM 断连 30s 切规则引擎) | B1 (graph) + B2 (guardrails) | 4 | LLM mock 断连 → 验证降级 |
| **风险可控** (SLO 越界自动降级) | D (slo) | **Prod** | SLI mock 注入 → 验证降级 |
| **回归测试** (Demo 场景 CI 自动化) | Main Agent | 5 | pytest + NeMo eval_dataset CI |

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

    # Phase 2 — max parallelism (4 agents)
    agent_b1 = launch(AgentB1, worktree=True)  # SRE core (graph/state/conversational)
    agent_b2 = launch(AgentB2, worktree=True)  # tools/skills/guardrails
    agent_d = launch(AgentD, worktree=True)    # storage/ontology
    wait(agent_e)  # frontend scaffold done
    merge(agent_e)
    wait(agent_b1, agent_b2, agent_d)
    merge(agent_b1, agent_b2, agent_d)  # sequential merge, resolve __init__.py
    checkpoint("IC-2")

    # Phase 3 — dependent on B1+D
    agent_c = launch(AgentC, worktree=True)  # infra/remediation/API
    agent_b1_ext = resume(AgentB1, "extend conversational + checkpoint")  # background
    agent_b2_ext = resume(AgentB2, "extend guardrails + skill creator")  # background
    agent_d2 = resume(AgentD, "extend memory + config_memory")  # background
    wait(agent_c, agent_b1_ext, agent_b2_ext, agent_d2)
    merge_all()
    checkpoint("IC-3")

    # Phase 4 — integration
    agent_e2 = resume(AgentE, "connect to real API, all 8 pages")
    write_e2e_tests()
    run_demo_cases()
    checkpoint("IC-4")

    # Phase 5 — polish
    final_integration()
    checkpoint("IC-5")
```

### 8.2 Worktree Strategy

- Each of the 6 agents gets `isolation: "worktree"` — branches from latest merged state
- Agents work exclusively on owned files (no overlapping modifications)
- Main agent merges worktrees sequentially after each phase
- Conflict zone: only `sre_agent/__init__.py` — owned by main agent
- B1/B2 file ownership is fully disjoint: B1 owns `agent/`, `config.py`, `cli.py`; B2 owns `tools/`, `skills/`, `guardrails/`

### 8.3 Agent Resumption

Agents B1, B2, D, E span multiple phases. Use Claude Code's `resume` parameter to continue with preserved context:
- Agent B1: Phase 2 (core graph) → Phase 3 (conversational + checkpoint) → Phase 4 (polish)
- Agent B2: Phase 2 (tools + skills + guardrails) → Phase 3 (skill creator + guardrail refinement) → Phase 4 (polish)
- Agent D: Phase 2 (ontology + knowledge) → Phase 3 (memory + config_memory) → Phase 4 (production interfaces)
- Agent E: Phase 1 (scaffold) → Phase 2 (pages) → Phase 4 (real API integration, 8 pages)

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
- lib/channels/ already has 8 implementations: base, cube_studio, kubernetes, prometheus,
  ssh, redfish, ipmi, switch — DO NOT recreate these

TASK 1 — New Shared Channels (lib/channels/):
Create 4 new channel files extending BaseChannel:
- log.py, alert.py (with silence_alert()), ontology.py, knowledge.py
Each must: extend BaseChannel, async connect/disconnect/health_check, unit tested
Existing channels (ssh, redfish, ipmi, switch) are already complete — reuse, do not modify.

TASK 2 — Pydantic Models (sre_agent/models/):
Create the shared model layer that ALL other agents will import:
- alert.py, diagnosis.py, remediation.py, ontology.py, memory.py, events.py, common.py
These are the source of truth. Include serialization round-trip tests.

DO NOT create files outside lib/channels/ and sre_agent/models/.
Only add new channel tests to lib/tests/ — do not duplicate fault_injector tests.
```

### 9.2 Agent B1: SRE Core (Graph + State + Conversational)

```
You are implementing the core SRE agent graph for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §3 (LangGraph), §16 (ConversationalAgent)
- Import models from sre_agent/models/ (DO NOT MODIFY)
- Import channels from lib/channels/ (DO NOT MODIFY)

CREATE these files:
- sre_agent/agent/ — `sre_agent.py`, `graph.py`, `state.py`, `nodes.py`,
  `discovery_agent.py`, `monitor_agent.py`, `conversational_agent.py`, `thinking_trace.py`
- sre_agent/agent/checkpoint.py — AsyncSqliteSaver persistence + --resume support
- sre_agent/config.py — SREConfig Pydantic settings
- sre_agent/cli.py — Click CLI entry point with --resume flag

KEY REQUIREMENTS:
1. LangGraph: 4-node cycle (reason→act→observe→decide) with conditional routing
2. ConversationalAgent: own LangGraph subgraph, chat history, streaming
3. Checkpoint: AsyncSqliteSaver for sre_agent --resume <session_id>
4. State: SREAgentState TypedDict with all fields needed by tools and remediation

Write unit tests. Mock all external deps (K8s, Prometheus, GPU, channels).
DO NOT create files in tools/, skills/, or guardrails/ — those belong to Agent B2.
```

### 9.3 Agent B2: Tools, Skills & Guardrails

```
You are implementing the tools, skills, and guardrails for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §4 (ToolRegistry), §10 (Skills), §17 (NeMo Guardrails)
- Import models from sre_agent/models/ (DO NOT MODIFY)
- Import channels from lib/channels/ (DO NOT MODIFY)

CREATE these directories:
- sre_agent/tools/ — ToolRegistry + readonly/write tool modules
- sre_agent/skills/ — SkillRegistry, executor, policy, creator, 6 builtin SKILL.md
- sre_agent/guardrails/ — NeMo Guardrails as OPTIONAL layer

KEY REQUIREMENTS:
1. ToolRegistry: safety levels (readonly/low/medium/high/critical), LangChain @tool
2. NeMo: optional — core ReAct must work WITHOUT guardrails loaded
3. Skills: registry + executor with 4 fixed @tools
4. ALL 6 builtin SKILL.md: vllm-diagnosis, rdma-diagnosis, gpu-health,
   network-diagnosis, storage-diagnosis, platform-health
5. SkillCreator: auto-generate SKILL.md from traces (Phase 3)

Write unit tests. Mock all external deps.
DO NOT create files in agent/, config.py, or cli.py — those belong to Agent B1.
```

### 9.4 Agent C: SRE Infrastructure

```
You are implementing the infrastructure layer for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §5 (Remediation), §5.6 (LoopOrchestrator),
  §5.7 (IncidentHandler), §11 (Concurrency), §13 (API), §15 (Auth), §18 (NAT)
- Import from sre_agent/models/ (DO NOT MODIFY)
- Import from sre_agent/agent/graph.py: create_sre_graph (DO NOT MODIFY)
- Import from sre_agent/ontology/, sre_agent/memory/ (DO NOT MODIFY)
- INHERIT SafetyGuard pattern from fault_injector/safety/guard.py
  and lib/channels/base.py SafetyViolationError

CREATE these directories:
- sre_agent/remediation/ — Engine, LoopOrchestrator, IncidentHandler, WAL, canary, approval
- sre_agent/concurrency/ — ResourceLock, AlertDeduplicator, AlertCorrelator
- sre_agent/auth/ — JWT, RBAC
- sre_agent/api/ — FastAPI routes, WebSocket streaming
- sre_agent/server.py — App factory
- sre_agent/safety/ — guard.py, forbidden.py, blast_radius.py
- sre_agent/nat/ — wrapper.py, workflow.yml, eval_dataset.jsonl

KEY REQUIREMENTS:
1. RemediationEngine: WAL-backed, canary with rollback, human approval gates
2. LoopOrchestrator: multi-candidate diagnosis-remediation cycle
3. IncidentHandler: alert→correlate→diagnose→remediate full pipeline
4. API: REST + WebSocket, ThinkingStep streaming to frontend
5. Auth: JWT + RBAC with safety-level-based tool access
6. validator.py: check blast radius against SafetyGuard rules
7. tools/write/ must HARD-BLOCK BMC VLAN/MTU writes without explicit approval
8. ResourceLock phasing: Demo uses `asyncio.Lock` (single process); Prod Phase 1 migrates to Redis Redlock (multi-replica HA)

Write unit tests. Mock storage and agent graph.
```

### 9.5 Agent D: Storage & Ontology

```
You are implementing the data/storage layer for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §7 (Ontology), §8 (Knowledge), §9 (Memory),
  §19 (HA), §20 (SLO)
- Import from sre_agent/models/ (DO NOT MODIFY)
- Import from lib/channels/ for scanner data sources (DO NOT MODIFY)

CREATE these directories:
- sre_agent/ontology/ — models.py, graph.py, store.py, discovery/*_scanner.py, query helpers
- sre_agent/knowledge/ — KnowledgeStore (ChromaDB), ingestion, chunker, runbook, retriever
- sre_agent/memory/ — MemoryStore (aiosqlite), store_pg interface, incident, pattern, config_memory, factory
- sre_agent/ha/ — Redis leader election, state replication
- sre_agent/slo/ — SLO metrics, 4-tier degradation
- sre_agent/lifecycle/ — Data retention, archival

PHASE TAGGING (respect these boundaries):
- Demo (full implementation): ontology/, knowledge/, memory/ (store, incident, pattern, factory)
- POC: memory/config_memory.py [GA-1]
- Prod (interface only, defer full implementation): memory/store_pg.py [GA-2], ha/, slo/, lifecycle/

KEY REQUIREMENTS:
1. OntologyGraph: NetworkX in-memory + aiosqlite persistence, CRUD, neighbor/path queries
2. KnowledgeStore: ChromaDB with document ingestion and semantic retrieval
3. MemoryStore: aiosqlite backend, LearnedPattern with time-decayed confidence
4. HA: Active/Standby via Redis SETNX leader election (Prod — interface + stub only)
5. SLO: 4-tier degradation (full→degraded→minimal→emergency) (Prod — interface only)

Write unit tests. Use aiosqlite :memory: and ChromaDB ephemeral for tests.
```

### 9.6 Agent E: Frontend

```
You are building the SRE Agent frontend for the AIDC Auto-SRE system.

CONTEXT:
- Read AIDC-auto-SRE.md §14 (GUI Design) for page specifications
- Read sre_agent/models/events.py for WebSocket event types
- Tech stack: Vite + React 18 + TypeScript + Zustand + Ant Design 5

CREATE: sre_agent/frontend/ — complete React application

PAGES (8):
1. Topology — D3.js force-directed graph of AIDC entities + relationships
2. Alerts — Alert dashboard with severity filtering, correlation grouping
3. Diagnosis — Real-time ThinkingStep timeline (WebSocket-fed)
4. Remediation — Canary progress bars, approval gate dialogs
5. Chat — Conversational agent with tool call rendering
6. Knowledge — Knowledge base document browser with search
7. Memory — Incident/pattern/config memory viewer
8. Skills — Skill registry browser (builtin + custom skills)

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
| NeMo Guardrails blocks LangGraph | Med | High | Agent B2 implements as optional decorator; core works without NeMo |
| D3.js topology complexity | High | Med | Agent E starts with Ant Design Tree; D3 added in Phase 4 |
| LangChain/LangGraph/NeMo version clash | Med | High | Pin versions in Phase 0; test in isolation first |
| Agent merge conflicts on __init__.py | Low | Med | Main agent owns all __init__.py; agents never modify them |
| **Agent B1/B2 interface mismatch** | Med | Med | Define ToolRegistry interface in Phase 0; B1 graph imports tool list from B2's registry.py |
| Frontend blocked on API | High | Med | MSW mocks from Day 1; OpenAPI spec defined in Phase 0 |
| ChromaDB/aiosqlite test flakiness | Med | Low | Use ephemeral/in-memory backends; deterministic test data |

---

## 11. Gap Coverage Summary

| Gap | Agent | File | Phase | Notes |
|-----|-------|------|-------|-------|
| GA-1 ConfigMemory | D | `memory/config_memory.py` | 3 (POC) | AIDC baselines + thresholds |
| GA-2 MemoryStorePG | D | `memory/store_pg.py` | **Prod** | Interface only in Demo/POC; asyncpg + Qdrant deferred |
| GA-3 SkillCreator | B2 | `skills/creator.py` | 3 | Auto-generate SKILL.md from traces |
| GA-4 6 builtin skills | B2 | `skills/builtin/{vllm,rdma,gpu,network,storage,platform}` | 2 | All 6 skills scheduled (not just 2) |
| GA-5 ConversationalAgent | B1 | `agent/conversational_agent.py` | 2-3 | LangGraph subgraph + chat history |
| GA-6 Alert.silence_alert | A | `lib/channels/alert.py` | 1 | Integrated into remediation flow |
| GA-7 SLO 4-tier degradation | D | `slo/degradation.py` | **Prod** | Interface only in Demo/POC |

---

## 12. Estimated Totals

| Agent | Python Files | Other Files | Tests |
|-------|-------------|-------------|-------|
| A: Channels + Models | 11 | — | 3 |
| B1: SRE Core | 10 | — | 3 |
| B2: Tools/Skills/Guards | 18 | 4 .co + 6 SKILL.md | 3 |
| C: SRE Infra | 22 | — | 5 |
| D: Storage/Ontology | 22 | — | 5 |
| E: Frontend | — | ~35 TSX | ~8 |
| **Total** | **~83** | **~45** | **~27** |
