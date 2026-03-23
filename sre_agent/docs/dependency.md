# SRE LangGraph Readiness Audit

## Summary
- Purpose: assess whether the current `sre_agent` foundation is ready for a first LangGraph slice, without introducing any LangGraph implementation in this stage.
- Scope: current runtime foundations in `sre_agent/models`, `sre_agent/tools`, `sre_agent/skills`, `sre_agent/ontology`, `sre_agent/knowledge`, `sre_agent/memory`, `sre_agent/frontend/src/api/types.ts`, `sre_agent/config.py`, `sre_agent/cli.py`, and `lib/channels/*`.
- Current hard fact: `sre_agent/agent/` does not exist, and there are no actual runtime LangGraph imports/usages in `sre_agent` or `lib`.

## Data Sources
- Static repo search for LangGraph/runtime symbols:
  - `rg -n "langgraph|StateGraph|AsyncSqliteSaver|create_sre_graph|ainvoke|astream" sre_agent lib -S`
  - Result: no runtime matches in `sre_agent` or `lib`
- Runtime inventory:
  - `sre_agent/agent/` is missing
  - `SkillRegistry`, `SkillPolicy`, `SkillExecutor` exist under `sre_agent/skills/`
  - `build_default_registry()` and `ToolExecutionContext` exist in `sre_agent/tools/registry.py`
- Supporting test evidence:
  - `python -m pytest -q sre_agent/tests/test_models.py` -> `23 passed`
  - `python -m pytest -q sre_agent/tests/test_tools.py` -> `17 passed`
  - `python -m pytest -q sre_agent/tests/test_skills.py sre_agent/tests/test_skills_real.py -rs` -> `10 passed`

## Current LangGraph Dependency Reality

### Actual in-repo dependency status
- Current runtime LangGraph consumers: none
- Current test LangGraph consumers: none
- Current runtime graph package: missing (`sre_agent/agent/`)
- Current planning-only references:
  - [plan0319.md](/d:/dev/cube-studio/cube-studio/sre_agent/docs/plan0319.md) explicitly says LangGraph is out of scope for `M0-M3`
  - [aidc-auto-sre-code-tree.md](/d:/dev/cube-studio/cube-studio/aidc-auto-sre-code-tree.md)
  - [aidc-auto-sre-plan-multi-agents.md](/d:/dev/cube-studio/cube-studio/aidc-auto-sre-plan-multi-agents.md)
  - [team-colabration-developement-plan.md](/d:/dev/cube-studio/cube-studio/team-colabration-developement-plan.md)

### LangGraph usage table
| Category | Current Status | Evidence |
|---|---|---|
| Runtime imports | None | `rg` search returned no matches |
| Test imports | None | `rg` search returned no matches |
| Graph runtime package | Missing | `sre_agent/agent/` does not exist |
| Planning/doc references | Present | planning docs reference future LangGraph work |

## Foundation Readiness for LangGraph

### Ready now
| Module Area | Why it is ready |
|---|---|
| `sre_agent/models/*` | Typed event, diagnosis, remediation, memory, and response contracts already exist and are tested. |
| `sre_agent/tools/registry.py` | Execution boundary, safety levels, approval flags, and tool dispatch already exist. |
| `sre_agent/skills/{registry,policy,executor}.py` | Deterministic skill discovery, ranking, and execution primitives already exist. |
| `lib/channels/*` | Concrete IO backends already exist for Kubernetes, Prometheus, SSH, Redfish, switch, and related channel access. |

### Usable with adapter
| Module Area | Current state |
|---|---|
| `sre_agent/ontology/*` | Usable as supporting graph/topology storage and query layer, but not yet wired into an agent runtime contract. |
| `sre_agent/knowledge/*` | Usable as retrieval support, but not yet part of any orchestration loop. |
| `sre_agent/memory/*` | Usable as storage/search support, but not yet integrated into a graph state machine. |
| `sre_agent/frontend/src/api/types.ts` | Contract-ready for future event/result wiring, but there is no backend graph producer today. |
| `sre_agent/config.py`, `sre_agent/cli.py` | Present and testable, but not graph-oriented yet. |

### Not ready or missing
| Module Area | Gap |
|---|---|
| `sre_agent/agent/*` | Entire graph runtime package is missing. |
| Graph state orchestration | No `StateGraph`, no node wiring, no runtime state transitions. |
| Checkpoint/resume | No graph checkpointing or resume path exists. |
| Runtime WebSocket event producer | `WSEvent` contract exists, but nothing emits graph-driven runtime events. |
| API layer invoking a graph | No API/runtime invocation path exists for a LangGraph executor. |

### Readiness conclusion
- The current foundation is ready enough for a **minimal LangGraph slice** that orchestrates existing skills and tools.
- The current foundation is **not** ready for a full LangGraph runtime including API, chat, streaming, and resume/checkpoint support without additional implementation.

## Recommended First LangGraph Integration Boundary
- LangGraph should orchestrate existing skills/tools, not replace them.
- The first graph boundary should depend on:
  - `SkillRegistry`
  - `SkillPolicy`
  - `SkillExecutor`
  - `build_default_registry()`
  - `ToolExecutionContext`
- The first graph state should reuse existing typed concepts where practical:
  - diagnosis/result concepts from `sre_agent.models.diagnosis`
  - event/result concepts from `sre_agent.models.events`
- Current tool and skill runtime is the stable substrate for future graph nodes:
  - select a skill
  - execute a skill
  - normalize result/output

## Concrete Runtime Building Blocks Already Available

### Skill runtime
- [registry.py](/d:/dev/cube-studio/cube-studio/sre_agent/skills/registry.py): skill discovery and validation
- [policy.py](/d:/dev/cube-studio/cube-studio/sre_agent/skills/policy.py): deterministic ranking
- [executor.py](/d:/dev/cube-studio/cube-studio/sre_agent/skills/executor.py): sequential execution and fail-fast behavior

### Tool runtime
- [registry.py](/d:/dev/cube-studio/cube-studio/sre_agent/tools/registry.py): tool registration, safety enforcement, execution dispatch
- readonly tools under `sre_agent/tools/readonly/`
- write tools under `sre_agent/tools/write/`

### Typed contracts
- [diagnosis.py](/d:/dev/cube-studio/cube-studio/sre_agent/models/diagnosis.py): `ThinkingStep`, `DiagnosisResult`, `DiagnosisSession`
- [events.py](/d:/dev/cube-studio/cube-studio/sre_agent/models/events.py): `WSEvent`, `EventType`
- [common.py](/d:/dev/cube-studio/cube-studio/sre_agent/models/common.py): `SREResponse`, error/safety contracts

## Parallel Tasks During LangGraph Work

### Safe to run in parallel
| Workstream | Why it can run in parallel |
|---|---|
| Exhaustive skills verification | Extends current skill/runtime confidence without changing graph design. |
| `plan0319.md` correction | Documentation cleanup does not block future graph interfaces. |
| Frontend contract stabilization | `src/api/types.ts`, `diagnosisStore.ts`, `useWebSocket.ts` can align more tightly to existing model contracts. |
| Storage hardening | Ontology/knowledge/memory tests and interface cleanup do not require a graph runtime first. |
| Tool hardening | Readonly/write tool coverage and error normalization improve the graph substrate rather than competing with it. |
| Docs alignment | Code-tree and team-plan reconciliation clarifies implementation status and dependencies. |

### Should not run in parallel
| Workstream | Why it should wait or stay aligned |
|---|---|
| Separate orchestration runtime outside `sre_agent/agent` | Creates competing control flow and interface drift. |
| Backend `/api/skills` runtime contract for this phase | Pulls the design toward API-first work before graph runtime exists. |
| New event shapes outside existing model/event contracts | Risks frontend/backend contract drift before graph runtime is defined. |

## Current Dependency Assessment by Subsystem
| Subsystem | LangGraph Dependency Today | Readiness |
|---|---|---|
| Models | None | Ready now |
| Tools | None | Ready now |
| Skills | None | Ready now |
| Channels | None | Ready now |
| Ontology | None | Usable with adapter |
| Knowledge | None | Usable with adapter |
| Memory | None | Usable with adapter |
| Frontend type contracts | None | Usable with adapter |
| CLI/config | None | Usable with adapter |
| Agent runtime | Missing | Not ready |

## Final Assessment
- No current code in `sre_agent` or `lib` relies on LangGraph.
- The repo already has enough tested foundation to support a small LangGraph orchestration layer centered on the existing skills/tools runtime.
- The main blocker is not contract shape; it is the absence of `sre_agent/agent/` and the first graph runtime package.
- Best parallel work while LangGraph is being built:
  1. extend skill verification to all builtin skills,
  2. refresh `plan0319.md`,
  3. harden tools/storage/frontend contracts,
  4. keep docs aligned with actual repo state.
