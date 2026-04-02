# 当前代码完成进度（对照 AIDC-auto-SRE.md）

- 更新日期：2026-04-01
- 对照基准：`AIDC-auto-SRE.md`

## 核心能力状态

| 模块 | 进度判定 | 说明 |
| --- | --- | --- |
| FastAPI + JWT + REST/WS 主链路 | 已实现（可运行） | `/api/*` + `/ws/alerts` `/ws/thinking-trace/{session_id}` `/ws/topology` 可联调。 |
| 诊断 Agent（LangGraph） | 已实现（可运行） | `sre_agent/agent/graph.py` 已形成 reason/act/observe/decide/finalize 闭环。 |
| 对话 Agent（chat） | 已实现（会话上下文增强） | `sre_agent/agent/conversational.py` + `/api/chat` `/api/chat/history` + `/ws/chat`；追问已注入 session 上下文（diagnosis result + blast/impact 摘要 + 最近 trace），不再仅依赖聊天历史。 |
| 拓扑/Ontology 自动发现 | 已实现（可运行） | `TopologyDiscoveryService` + `/api/topology` `/api/topology/status` `/api/topology/discover` + `/ws/topology`。 |
| 告警驱动与会话链路 | 已实现（可运行） | `/api/alerts`、`/api/handle`、`/api/sessions*` 已打通。 |
| 修复引擎（审批/灰度/WAL） | 已实现（可运行） | `RemediationEngine`、`ApprovalGate`、`CanaryExecutor`、`RollbackJournal` 已接入主链路。 |
| ThinkingTrace | 已实现 | trace 已在模型层与 WS 事件链路中使用，并在诊断页展示。 |

## 本轮新增（2026-04-01）

1. `/api/chat` 增加 session 上下文注入，默认拼装：诊断结论、影响范围摘要、最近 12 条 trace。
2. `/api/chat` 响应新增可选 `meta`：`context_applied`、`session_id`、`trace_steps_used`、`context_tokens_estimate`。
3. `session_id` 无效时，`/api/chat` 返回明确 404（避免“静默空回复”）。
4. 诊断页 `diagnosis-chat-workspace` 增加“上下文已加载/待加载”状态提示。
5. 诊断追问改为“强会话锚定”上下文策略：仅使用当前 session 最近窗口历史（默认 6 条）+ 诊断事实块，降低串台概率。
6. `/api/chat` 与 `/api/chat/history` 新增可选 `display` 字段（`answer`/`thinking_raw`），前端默认展示答案并可展开原始 `<think>`。

## 已知限制 / 后续项

1. 当前诊断追问采用 REST 同步回复，`/ws/chat` 流式追问尚未接入诊断页主链路。
2. Guardrails 仍非默认执行路径（当前默认 `PassthroughGuardrails`）。
3. NAT/HA/PG-Qdrant/Skill 自动生成仍以框架或部分实现为主，待产品化增强。
