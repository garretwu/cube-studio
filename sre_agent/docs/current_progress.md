# 当前代码完成进度（对照 AIDC-auto-SRE.md）

- 更新日期：2026-03-30
- 对照基准：`AIDC-auto-SRE.md`
- 判定方法：仅按当前仓库代码与接口实现，不按设计目标推断。

## 1. 总览

| 模块 | 进度判定 | 说明 |
| --- | --- | --- |
| FastAPI + JWT + REST/WS 主链路 | 已实现 | REST 与 `/ws/alerts` `/ws/chat` `/ws/thinking-trace/{session_id}` `/ws/topology` 已落地。 |
| 诊断 Agent（LangGraph） | 已实现（可运行） | `sre_agent/agent/graph.py` + `nodes.py` 已形成 reason/act/observe/decide/finalize 循环。 |
| 对话 Agent（chat） | 已实现（可运行） | `sre_agent/agent/conversational.py` + `/api/chat` `/api/chat/history` + `/ws/chat`。 |
| 拓扑/Ontology 自动发现 | 已实现（可运行） | `TopologyDiscoveryService` + static/live discovery + `/api/topology*` + `/ws/topology`。 |
| 告警驱动与会话链路 | 已实现（可运行） | `AlertPollingService`、`/api/alerts`、`/api/handle`、sessions/loop/remediate 全链路。 |
| 修复引擎（审批/灰度/WAL） | 已实现（可运行） | `RemediationEngine`、`ApprovalGate`、`CanaryExecutor`、`RollbackJournal` 已接入主链路。 |
| ThinkingTrace 能力 | 已实现（但实现位置与设计文档不一致） | 通过 `models/diagnosis.py` 和 trace publisher 实现；未见独立 `agent/thinking_trace.py` 文件。 |
| Guardrails（NeMo） | 部分实现（未接入主运行链路） | `guardrails/runtime.py` 与配置存在，但默认运行仍是 `PassthroughGuardrails`。 |
| NAT（nvidia-nat） | 部分实现（薄封装） | `nat/wrapper.py` 已有包装类型，但未在 server/runner 主流程接入 profiling/eval。 |
| HA（leader election/replication） | 仅框架 | `ha/heartbeat.py`、`ha/replication.py` 为生产阶段 stub。 |
| Memory PG/Qdrant 产品化后端 | 仅框架 | `memory/store_pg.py` 明确 NotImplemented。 |
| Skill 自动生成（SkillCreator） | 仅框架 | `skills/creator.py` 为 placeholder。 |

## 2. 已落地主能力（代码证据）

- 诊断与修复主链路：
- `sre_agent/server.py`
- `sre_agent/api/routes.py`
- `sre_agent/remediation/engine.py`
- `sre_agent/remediation/loop_orchestrator.py`

- 拓扑与 ontology：
- `sre_agent/topology/discovery.py`
- `sre_agent/ontology/discovery/k8s_scanner.py`
- `sre_agent/api/routes.py`（`/api/topology`、`/api/topology/status`、`/api/topology/discover`）
- `sre_agent/api/websocket.py`（`/ws/topology`）

- 前端拓扑与联调链路：
- `sre_agent/frontend/src/store/topologyStore.ts`
- `sre_agent/frontend/src/pages/Topology.tsx`
- `sre_agent/frontend/src/api/client.ts`
- `sre_agent/frontend/src/api/ws.ts`

## 3. 主要 Gap（未完成或仅框架）

1. Guardrails 尚未成为默认执行路径。
- 当前默认是 `PassthroughGuardrails`。
- 代码位置：`sre_agent/agent/graph.py`。

2. Checkpoint 持久化与 resume 仍未达到设计目标。
- 当前 checkpointer 使用 `MemorySaver`，且默认 runner 调用时 `checkpoint_dir=None`。
- 代码位置：`sre_agent/agent/checkpoint.py`、`sre_agent/server.py`。

3. NAT 仅有 wrapper，未接入服务启动或诊断执行流程。
- 代码位置：`sre_agent/nat/wrapper.py`。

4. HA 相关能力为生产阶段 stub。
- 代码位置：`sre_agent/ha/heartbeat.py`、`sre_agent/ha/replication.py`。

5. 产品化记忆后端（PostgreSQL/Qdrant）未实现。
- 代码位置：`sre_agent/memory/store_pg.py`。

6. Skill 自动生成能力未实现。
- 代码位置：`sre_agent/skills/creator.py`。

## 4. 结论

- 对照 `AIDC-auto-SRE.md`，当前项目已具备可联调的核心闭环（告警 -> 诊断 -> 修复 -> 拓扑/WS 展示）。
- 主要缺口集中在“产品化增强层”：Guardrails 默认接入、NAT 生产化、HA、PG/Qdrant、Skill 自动生成。
- 因此当前阶段可判定为：**核心功能可用，产品化能力未完成**。
