# AIDC Auto-SRE — Complete Code Tree

> Legend: `[E]` = Existing, `[N]` = New (to be created), `[X]` = Extend existing file

```
cube-studio/
│
├── CLAUDE.md                                    [E] Project instructions
├── AIDC-auto-SRE.md                             [E] Design doc (§1-§20)
├── aidc-auto-sre-plan.md                        [E] Plan v2.6
├── aidc-auto-sre-plan-multi-agents.md           [E] Multi-agent implementation plan
├── channel.md                                   [E] Channel layer design doc
├── fault-injector.md                            [E] Fault injector design doc
├── load-simulator.md                            [E] Load simulator design doc
├── design.md                                    [E] Overall system design
├── README.md                                    [E] Project readme
│
│
│ ============================================================
│  EXISTING — completed, read-only for sre_agent development
│ ============================================================
│
├── load_simulator/                              [E] COMPLETE — load testing tool
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py                              # BaseAgent ABC
│   │   ├── bottleneck.py
│   │   ├── finetune.py
│   │   ├── inference.py
│   │   ├── monitor.py
│   │   ├── notebook.py
│   │   └── pipeline.py
│   ├── channels/
│   │   ├── __init__.py
│   │   ├── inference.py
│   │   └── notebook.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── defaults.py
│   │   ├── loader.py
│   │   └── schema.py
│   ├── load/
│   │   ├── __init__.py
│   │   ├── profile.py
│   │   ├── prompt_pool.py
│   │   ├── rate_limiter.py
│   │   └── token_distribution.py
│   ├── metrics/
│   │   ├── __init__.py
│   │   ├── aggregator.py
│   │   ├── collector.py
│   │   ├── thresholds.py
│   │   └── time_series.py
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── adaptive.py
│   │   ├── engine.py                            # Reference pattern for orchestrator
│   │   ├── platform_monitor.py
│   │   ├── preflight.py
│   │   ├── scheduler.py
│   │   ├── session.py
│   │   └── session_store.py
│   ├── reporting/
│   │   ├── __init__.py
│   │   ├── bundle.py
│   │   ├── charts.py
│   │   ├── comparison.py
│   │   ├── html_report.py
│   │   └── session_output.py
│   └── tests/
│       ├── __init__.py
│       ├── test_adaptive_rules.py
│       ├── test_adaptive_rules_extended.py
│       ├── test_agents_with_channels.py
│       ├── test_base_agent.py
│       ├── test_channels.py
│       ├── test_charts.py
│       ├── test_config_loader.py
│       ├── test_inference_targets.py
│       ├── test_load_profile.py
│       ├── test_metrics_aggregator.py
│       ├── test_metrics_collector.py
│       ├── test_metrics_thresholds.py
│       ├── test_metrics_time_series.py
│       ├── test_monitor.py
│       ├── test_orchestrator_adaptive_config.py
│       ├── test_orchestrator_channel_wiring.py
│       ├── test_orchestrator_modes.py
│       ├── test_orchestrator_preflight.py
│       ├── test_orchestrator_resume.py
│       ├── test_orchestrator_single_soak.py
│       ├── test_platform_monitor.py
│       ├── test_preflight_policy.py
│       ├── test_prompt_pool.py
│       ├── test_rate_limiter.py
│       ├── test_reporting.py
│       ├── test_reporting_bundle.py
│       ├── test_scheduler.py
│       ├── test_schema_contract.py
│       ├── test_session_output.py
│       ├── test_session_store.py
│       ├── test_session_tracker.py
│       └── test_token_distribution.py
│
│
│ ============================================================
│  SHARED LIBRARY — extend with SRE channels (Agent A)
│ ============================================================
│
├── lib/
│   ├── __init__.py                              [E]
│   ├── channels/
│   │   ├── __init__.py                          [E]
│   │   ├── base.py                              [E] BaseChannel ABC (pattern reference)
│   │   ├── cube_studio.py                       [E] CubeStudio platform channel
│   │   ├── kubernetes.py                        [E] K8s channel (kubectl)
│   │   ├── prometheus.py                        [E] Prometheus channel (PromQL)
│   │   ├── ssh.py                               [N] Agent A — asyncssh, conn pool, shlex safety
│   │   ├── redfish.py                           [N] Agent A — httpx BMC/IPMI session mgmt
│   │   ├── switch.py                            [N] Agent A — SSH CLI + NETCONF dual mode
│   │   ├── log.py                               [N] Agent A — Log aggregation (Loki/ES)
│   │   ├── alert.py                             [N] Agent A — Alert channel + silence_alert()
│   │   ├── ontology.py                          [N] Agent A — Ontology graph query channel
│   │   └── knowledge.py                         [N] Agent A — Knowledge retrieval channel
│   └── tests/
│       ├── __init__.py                          [E]
│       ├── test_channels.py                     [E]
│       ├── test_ssh.py                          [N] Agent A
│       ├── test_redfish.py                      [N] Agent A
│       ├── test_switch.py                       [N] Agent A
│       ├── test_log.py                          [N] Agent A
│       └── test_alert.py                        [N] Agent A
│
│
│ ============================================================
│  SRE AGENT — all new code
│ ============================================================
│
├── sre_agent/
│   ├── __init__.py                              [N] Main Agent
│   ├── config.py                                [N] Agent B — SREConfig Pydantic settings
│   ├── cli.py                                   [N] Agent B — Click CLI entry point
│   ├── server.py                                [N] Agent C — FastAPI application factory
│   ├── py.typed                                 [N] Main Agent — PEP 561 marker
│   │
│   │ ── Models (Agent A) ─────────────────────────────────
│   ├── models/
│   │   ├── __init__.py                          [N] re-exports all models
│   │   ├── common.py                            [N] SREResponse[T], ErrorCode, SafetyLevel enum
│   │   ├── alert.py                             [N] Alert, AlertSeverity, AlertStatus
│   │   ├── diagnosis.py                         [N] DiagnosisResult, DiagnosisSession, ThinkingStep
│   │   ├── remediation.py                       [N] RemediationPlan, RemediationAction, LoopResult
│   │   ├── ontology.py                          [N] OntologyNode, OntologyEdge, entity types
│   │   ├── memory.py                            [N] IncidentRecord, LearnedPattern, ConfigBaseline
│   │   └── events.py                            [N] WSEvent, EventType enum (WS contract)
│   │
│   │ ── Agent Core (Agent B) ─────────────────────────────
│   ├── agent/
│   │   ├── __init__.py                          [N]
│   │   ├── state.py                             [N] SREAgentState TypedDict
│   │   ├── graph.py                             [N] LangGraph StateGraph (reason→act→observe→decide)
│   │   ├── nodes.py                             [N] Node functions (reason, act, observe, decide)
│   │   ├── prompts.py                           [N] System prompts (base, diagnosis, remediation)
│   │   └── conversational.py                    [N] ConversationalAgent (LangGraph subgraph + chat)
│   │
│   │ ── NeMo Guardrails (Agent B) ────────────────────────
│   ├── guardrails/
│   │   ├── __init__.py                          [N]
│   │   ├── config.py                            [N] NeMo Guardrails configuration
│   │   ├── actions.py                           [N] Custom NeMo action handlers
│   │   └── rails/
│   │       ├── input.co                         [N] Input validation rails (Colang DSL)
│   │       ├── output.co                        [N] Output formatting rails
│   │       ├── execution.co                     [N] Tool execution safety rails
│   │       └── dialog.co                        [N] Dialog flow rails
│   │
│   │ ── Tool Registry (Agent B) ──────────────────────────
│   ├── tools/
│   │   ├── __init__.py                          [N]
│   │   ├── registry.py                          [N] ToolRegistry (safety levels, enable/disable)
│   │   ├── readonly/
│   │   │   ├── __init__.py                      [N]
│   │   │   ├── k8s.py                           [N] kubectl get/describe/logs/top
│   │   │   ├── prometheus.py                    [N] PromQL instant/range queries
│   │   │   ├── gpu.py                           [N] nvidia-smi, DCGM metrics
│   │   │   ├── network.py                       [N] RDMA stats, switch port counters
│   │   │   ├── ontology.py                      [N] Graph traversal, neighbor, path queries
│   │   │   └── memory.py                        [N] Pattern/incident/config lookup
│   │   └── write/
│   │       ├── __init__.py                      [N]
│   │       ├── k8s.py                           [N] kubectl apply/delete/scale/cordon/drain
│   │       ├── remediation.py                   [N] Execute remediation actions
│   │       └── network.py                       [N] Switch port enable/disable, route update
│   │
│   │ ── Skills Framework (Agent B) ───────────────────────
│   ├── skills/
│   │   ├── __init__.py                          [N]
│   │   ├── registry.py                          [N] SkillRegistry (discover, load, validate)
│   │   ├── executor.py                          [N] SkillExecutor (4 fixed @tools)
│   │   ├── policy.py                            [N] Skill selection policy (match score)
│   │   ├── creator.py                           [N] SkillCreator (auto-gen from traces)
│   │   └── builtin/
│   │       ├── vllm-diagnosis/
│   │       │   └── SKILL.md                     [N] vLLM latency diagnosis skill
│   │       ├── rdma-diagnosis/
│   │       │   └── SKILL.md                     [N] RDMA anomaly diagnosis skill
│   │       ├── gpu-health/
│   │       │   └── SKILL.md                     [N] GPU health check skill
│   │       ├── network-diagnosis/
│   │       │   └── SKILL.md                     [N] Network diagnosis skill
│   │       ├── storage-diagnosis/
│   │       │   └── SKILL.md                     [N] Storage diagnosis skill
│   │       └── platform-health/
│   │           └── SKILL.md                     [N] Platform health check skill
│   │
│   │ ── Remediation Engine (Agent C) ─────────────────────
│   ├── remediation/
│   │   ├── __init__.py                          [N]
│   │   ├── engine.py                            [N] RemediationEngine (WAL + canary + approval)
│   │   ├── loop.py                              [N] LoopOrchestrator (multi-candidate cycle)
│   │   ├── incident.py                          [N] IncidentHandler (alert→diagnose→remediate)
│   │   ├── wal.py                               [N] Write-ahead log (aiosqlite)
│   │   ├── canary.py                            [N] Canary deployment + metric comparison
│   │   ├── approval.py                          [N] Human approval gates (WS + timeout)
│   │   └── validator.py                         [N] PlanValidator (blast radius, safety)
│   │
│   │ ── Concurrency (Agent C) ────────────────────────────
│   ├── concurrency/
│   │   ├── __init__.py                          [N]
│   │   ├── resource_lock.py                     [N] ResourceLock (Redis SETNX distributed)
│   │   ├── alert_dedup.py                       [N] AlertDeduplicator (fingerprint + window)
│   │   └── alert_correlator.py                  [N] AlertCorrelator (time-window grouping)
│   │
│   │ ── API Layer (Agent C) ──────────────────────────────
│   ├── api/
│   │   ├── __init__.py                          [N]
│   │   ├── routes.py                            [N] REST: alerts, diagnosis, remediation, ontology
│   │   ├── websocket.py                         [N] WS: ThinkingStep streaming, alert push
│   │   └── middleware.py                        [N] Auth middleware, CORS, rate limiting
│   │
│   │ ── Auth (Agent C) ───────────────────────────────────
│   ├── auth/
│   │   ├── __init__.py                          [N]
│   │   ├── jwt.py                               [N] JWT token issue/verify
│   │   └── rbac.py                              [N] Role-based access (admin/operator/viewer)
│   │
│   │ ── Safety (Agent C) ─────────────────────────────────
│   ├── safety/
│   │   ├── __init__.py                          [N]
│   │   └── blast_radius.py                      [N] Blast radius calc (entity count, SLO impact)
│   │
│   │ ── NeMo Agent Toolkit (Agent C) ─────────────────────
│   ├── nat/
│   │   ├── __init__.py                          [N]
│   │   └── wrapper.py                           [N] NAT profiling/evaluation wrapper
│   │
│   │ ── Ontology / Digital Twin (Agent D) ────────────────
│   ├── ontology/
│   │   ├── __init__.py                          [N]
│   │   ├── graph.py                             [N] OntologyGraph (NetworkX + aiosqlite persist)
│   │   ├── entities.py                          [N] Entity type registration + validation
│   │   ├── query.py                             [N] Graph queries (neighbors, paths, impact radius)
│   │   └── scanner/
│   │       ├── __init__.py                      [N]
│   │       ├── k8s.py                           [N] K8s topology discovery (pods, nodes, services)
│   │       ├── network.py                       [N] Network topology (LLDP, switch fabric)
│   │       ├── gpu.py                           [N] GPU topology (nvidia-smi topo -m)
│   │       └── redfish.py                       [N] BMC/hardware inventory discovery
│   │
│   │ ── Knowledge Base (Agent D) ─────────────────────────
│   ├── knowledge/
│   │   ├── __init__.py                          [N]
│   │   ├── store.py                             [N] KnowledgeStore (ChromaDB vector store)
│   │   ├── ingestion.py                         [N] Document ingestion (MD, PDF, runbook → chunks)
│   │   └── retriever.py                         [N] Semantic retrieval + reranking
│   │
│   │ ── Memory System (Agent D) ──────────────────────────
│   ├── memory/
│   │   ├── __init__.py                          [N]
│   │   ├── store.py                             [N] MemoryStore (aiosqlite, Demo/POC backend)
│   │   ├── store_pg.py                          [N] MemoryStorePG (asyncpg + Qdrant, Prod)
│   │   ├── incident.py                          [N] Incident memory CRUD
│   │   ├── pattern.py                           [N] LearnedPattern (time-decayed confidence)
│   │   ├── config_memory.py                     [N] ConfigMemory (AIDC baselines, thresholds)
│   │   └── factory.py                           [N] create_memory_store() backend selector
│   │
│   │ ── High Availability (Agent D) ──────────────────────
│   ├── ha/
│   │   ├── __init__.py                          [N]
│   │   ├── heartbeat.py                         [N] Redis SETNX leader election
│   │   └── replication.py                       [N] State replication (active → standby)
│   │
│   │ ── SLO Management (Agent D) ─────────────────────────
│   ├── slo/
│   │   ├── __init__.py                          [N]
│   │   ├── metrics.py                           [N] SLO metric collection + error budget
│   │   └── degradation.py                       [N] 4-tier policy (full→degraded→minimal→emergency)
│   │
│   │ ── Data Lifecycle (Agent D) ─────────────────────────
│   ├── lifecycle/
│   │   ├── __init__.py                          [N]
│   │   └── data_lifecycle.py                    [N] Retention, archival, cleanup policies
│   │
│   │ ── Frontend (Agent E) ───────────────────────────────
│   ├── frontend/
│   │   ├── package.json                         [N]
│   │   ├── vite.config.ts                       [N]
│   │   ├── tsconfig.json                        [N]
│   │   ├── index.html                           [N]
│   │   ├── public/
│   │   │   └── favicon.ico                      [N]
│   │   └── src/
│   │       ├── main.tsx                         [N] React entry point
│   │       ├── App.tsx                          [N] App shell + layout
│   │       ├── routes.tsx                       [N] Route definitions (7 pages)
│   │       ├── api/
│   │       │   ├── client.ts                    [N] Axios instance + interceptors
│   │       │   ├── ws.ts                        [N] WebSocket client (reconnect, backpressure)
│   │       │   └── types.ts                     [N] TS types mirroring Pydantic models
│   │       ├── store/
│   │       │   ├── alertStore.ts                [N] Zustand — alert state
│   │       │   ├── diagnosisStore.ts            [N] Zustand — diagnosis session state
│   │       │   ├── topologyStore.ts             [N] Zustand — ontology graph state
│   │       │   ├── remediationStore.ts          [N] Zustand — remediation state
│   │       │   └── chatStore.ts                 [N] Zustand — chat history state
│   │       ├── pages/
│   │       │   ├── Topology.tsx                 [N] D3.js force-directed AIDC graph
│   │       │   ├── Alerts.tsx                   [N] Alert dashboard + correlation view
│   │       │   ├── Diagnosis.tsx                [N] Real-time ThinkingStep timeline
│   │       │   ├── Remediation.tsx              [N] Canary progress + approval gates
│   │       │   ├── Chat.tsx                     [N] Conversational agent interface
│   │       │   ├── Knowledge.tsx                [N] Knowledge base browser
│   │       │   ├── Memory.tsx                   [N] Incident/pattern/config memory viewer
│   │       │   └── Skills.tsx                   [N] Skill registry browser
│   │       ├── components/
│   │       │   ├── TopologyGraph.tsx            [N] D3 force simulation component
│   │       │   ├── ThinkingTimeline.tsx         [N] Step-by-step diagnosis viz
│   │       │   ├── AlertTable.tsx               [N] Sortable/filterable alert table
│   │       │   ├── ApprovalDialog.tsx           [N] Human approval modal
│   │       │   ├── CanaryProgress.tsx           [N] Canary deployment progress bar
│   │       │   ├── ChatMessage.tsx              [N] Chat bubble + tool call render
│   │       │   ├── EntityDetail.tsx             [N] Ontology entity detail panel
│   │       │   └── SkillCard.tsx                [N] Skill card with match score
│   │       ├── hooks/
│   │       │   ├── useWebSocket.ts              [N] WS hook with auto-reconnect
│   │       │   └── usePolling.ts                [N] Fallback polling hook
│   │       └── locales/
│   │           ├── zh.json                      [N] Chinese translations
│   │           └── en.json                      [N] English translations
│   │
│   │ ── Tests ────────────────────────────────────────────
│   └── tests/
│       ├── __init__.py                          [N]
│       ├── test_models.py                       [N] Agent A — serialization round-trips
│       ├── test_graph.py                        [N] Agent B — LangGraph state machine
│       ├── test_tools.py                        [N] Agent B — ToolRegistry + tool functions
│       ├── test_guardrails.py                   [N] Agent B — NeMo rails validation
│       ├── test_skills.py                       [N] Agent B — SkillRegistry + executor
│       ├── test_remediation.py                  [N] Agent C — RemediationEngine + WAL
│       ├── test_loop.py                         [N] Agent C — LoopOrchestrator
│       ├── test_concurrency.py                  [N] Agent C — locks, dedup, correlator
│       ├── test_api.py                          [N] Agent C — REST routes + WS
│       ├── test_auth.py                         [N] Agent C — JWT + RBAC
│       ├── test_ontology.py                     [N] Agent D — graph CRUD + queries
│       ├── test_knowledge.py                    [N] Agent D — ChromaDB store/retrieve
│       ├── test_memory.py                       [N] Agent D — incident/pattern/config
│       ├── test_ha.py                           [N] Agent D — leader election
│       └── test_slo.py                          [N] Agent D — degradation tiers
│
│
│ ============================================================
│  INTEGRATION TESTS (Main Agent, Phase 4-5)
│ ============================================================
│
├── tests/
│   ├── __init__.py                              [N]
│   └── integration/
│       ├── __init__.py                          [N]
│       ├── test_alert_to_diagnosis.py           [N] Alert ingestion → diagnosis E2E
│       ├── test_diagnosis_to_remediation.py     [N] Diagnosis → remediation E2E
│       ├── test_ws_events.py                    [N] WebSocket event contract validation
│       ├── test_demo_case_1_vllm.py             [N] Demo: vLLM latency spike (RC-A)
│       ├── test_demo_case_2_rdma.py             [N] Demo: RDMA anomaly (RC-D)
│       ├── test_demo_case_3_ambiguous.py        [N] Demo: Multi-candidate loop
│       └── test_demo_case_4_combined.py         [N] Demo: Combined fault scenario
│
│
│ ============================================================
│  PROJECT CONFIG (Main Agent, Phase 0)
│ ============================================================
│
├── pyproject.toml                               [N] Dependencies, build config
├── docker-compose.sre.yml                       [N] SRE stack (redis, postgres, chromadb, backend, frontend)
│
│
│ ============================================================
│  EXISTING PLATFORM (read-only context)
│ ============================================================
│
├── myapp/                                       [E] Cube Studio backend (Flask)
│   ├── __init__.py
│   ├── config.py
│   ├── security.py
│   ├── models/                                  # SQLAlchemy models
│   ├── views/                                   # Flask views + REST APIs
│   ├── tasks/                                   # Celery async tasks
│   ├── utils/
│   ├── frontend/                                # Main React UI
│   ├── vision/                                  # ML pipeline editor
│   ├── visionPlus/                              # ETL pipeline editor
│   ├── migrations/                              # Alembic DB migrations
│   └── init/                                    # Default data (JSON)
│
├── images/                                      [E] Docker images (GPU, Jupyter, serving)
├── install/                                     [E] Deployment (Docker Compose, K8s)
├── job-template/                                [E] ML pipeline operator templates
└── reports/                                     [E] Load simulator output
```

## File Count Summary

| Component | Existing | New Python | New TS/TSX | New Other | Total New |
|-----------|----------|-----------|-----------|-----------|-----------|
| lib/channels/ | 5 | 7 | — | — | 7 |
| lib/tests/ | 2 | 5 | — | — | 5 |
| sre_agent/models/ | — | 8 | — | — | 8 |
| sre_agent/agent/ | — | 6 | — | — | 6 |
| sre_agent/guardrails/ | — | 3 | — | 4 .co | 7 |
| sre_agent/tools/ | — | 12 | — | — | 12 |
| sre_agent/skills/ | — | 5 | — | 6 SKILL.md | 11 |
| sre_agent/remediation/ | — | 8 | — | — | 8 |
| sre_agent/concurrency/ | — | 4 | — | — | 4 |
| sre_agent/api/ | — | 4 | — | — | 4 |
| sre_agent/auth/ | — | 3 | — | — | 3 |
| sre_agent/safety/ | — | 2 | — | — | 2 |
| sre_agent/nat/ | — | 2 | — | — | 2 |
| sre_agent/ontology/ | — | 9 | — | — | 9 |
| sre_agent/knowledge/ | — | 4 | — | — | 4 |
| sre_agent/memory/ | — | 7 | — | — | 7 |
| sre_agent/ha/ | — | 3 | — | — | 3 |
| sre_agent/slo/ | — | 3 | — | — | 3 |
| sre_agent/lifecycle/ | — | 2 | — | — | 2 |
| sre_agent/frontend/ | — | — | ~35 | 5 config | ~40 |
| sre_agent/tests/ | — | 16 | — | — | 16 |
| tests/integration/ | — | 8 | — | — | 8 |
| root config | — | — | — | 2 | 2 |
| **Totals** | **~82** | **~114** | **~35** | **~17** | **~166** |

## Agent → File Ownership Map

| Agent | Directories Owned | File Count |
|-------|------------------|-----------|
| **Main** | `sre_agent/__init__.py`, `pyproject.toml`, `docker-compose.sre.yml`, `tests/integration/` | ~12 |
| **A: Channels+Models** | `lib/channels/` (new), `lib/tests/` (new), `sre_agent/models/` | ~20 |
| **B: SRE Core** | `sre_agent/agent/`, `guardrails/`, `tools/`, `skills/`, `config.py`, `cli.py` | ~45 |
| **C: SRE Infra** | `sre_agent/remediation/`, `concurrency/`, `api/`, `auth/`, `safety/`, `nat/`, `server.py` | ~28 |
| **D: Storage** | `sre_agent/ontology/`, `knowledge/`, `memory/`, `ha/`, `slo/`, `lifecycle/` | ~25 |
| **E: Frontend** | `sre_agent/frontend/` | ~40 |
