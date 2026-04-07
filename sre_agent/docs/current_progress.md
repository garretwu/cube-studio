# 当前代码完成进度（对照 AIDC-auto-SRE.md）

- 更新日期：2026-04-03
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

## 本轮新增（2026-04-03）

1. 修复页冲突合并完成（前端新设计保留）：`CanaryProgress.tsx`、`Remediation.tsx`、`Remediation.test.tsx`。
2. 修复页补齐 DoD 关键链路：按会话加载、`/remediation/:sessionId` 深链选中、关键修复事件过滤（审批/执行/观察/结果/回滚）。
3. 执行步骤固定展示当前会话最终 `plan.steps`，无计划时显示空态提示，不再回退占位数据。
4. 金丝雀区域按“单次执行”展示单批次进度与终态；失败类状态颜色映射补齐（`failed/timeout/escalated/rejected`）。
5. 前后端契约完成同步：`sync_frontend_contract.py` 与 `sync_contract_doc.py` 已更新并校验通过。
6. 修复前端回归失败项：`App.test.tsx` 中 `alerts-modified` 导航激活断言已恢复通过（去除该路由的重定向覆盖）。
7. 前端全量测试通过：`npm run test`（20 files / 67 tests）；修复页与会话链路相关用例保持通过。
8. 完成前后端彻底重启：清理旧进程后通过 `start_frontend_backend.py` 重新拉起，`/api/runtime/llm/status` 与 `/api/alerts` 鉴权检查均为 `200`。

## 本轮新增（2026-04-03，持续实施）

1. 告警入口收敛完成：导航仅保留 `alertsModified`（显示文案“告警”），并在路由层新增兼容重定向 `/alerts -> /alerts-modified`。
2. 诊断链路改造为“异步启动 + 立即进入诊断页”：新增 `POST /api/diagnose/start`，后端后台执行诊断并通过 `/ws/thinking-trace/{session_id}` 实时发布轨迹；前端 `AlertsModified` 改为优先调用新接口并即时跳转会话页。
3. 诊断流式兼容增强：前端状态仓库支持 `tool_call` 事件直接映射到可视轨迹步骤，避免“有事件不渲染”。
4. 知识库 Dify 接入完成：配置模型新增 `knowledge_base.provider/base_url/api_key/dataset_id/runbook_dataset_id/timeout/retries/api_prefix`，服务启动支持 `provider=dify` 适配并在初始化失败时回退本地 store。
5. 知识 API 扩展完成：新增 `/api/knowledge/dataset`、`/api/knowledge/documents/{document_id}`、`/api/knowledge/documents/{document_id}/segments`、`/api/knowledge/segments/search`，并保留 `/api/knowledge/search` 兼容。
6. 知识页升级为双面板主从布局：左侧知识库与文档检索，右侧文档详情 + 文档片段过滤 + 全库片段检索，完成前后端类型与 API client 对齐。
7. 契约同步完成：`sync_frontend_contract.py` 与 `sync_contract_doc.py` 已重新生成并 `--check` 通过。

## 已知限制 / 后续项

1. 当前诊断追问采用 REST 同步回复，`/ws/chat` 流式追问尚未接入诊断页主链路。
2. Guardrails 仍非默认执行路径（当前默认 `PassthroughGuardrails`）。
3. NAT/HA/PG-Qdrant/Skill 自动生成仍以框架或部分实现为主，待产品化增强。

## DoD 完整实现对照（前后端）

- 判定口径：`主链路可运行`（链路可跑通记为“已实现（可运行）”；工程化缺口在“距离完全实现差异”列体现）

| DoD条目 | 后端进展 | 前端进展 | 判定 | 距离完全实现差异 |
| --- | --- | --- | --- | --- |
| 1. 告警/监控自动收集，自动生成基线 | `AlertPollingService` 已支持自动拉取告警并推送到快照/WS；`/api/memory/baseline` 当前仅查询，无自动生成逻辑。 | 告警实时展示已接入；基线当前为只读展示。 | 部分实现 | 缺“监控指标自动采集 -> 基线生成/更新 -> 持久化”的闭环任务与调度。 |
| 2. 告警诊断并生成可执行修复计划 | `/api/diagnose`、计划生成、`/api/remediate/{session_id}/plan/revise` 已打通。 | 诊断页可查看并修改方案，审批前版本控制已接入。 | 已实现（可运行） | 可补 `/ws/chat` 流式 chat 与诊断页主链路一致性（非阻塞）。 |
| 3. 可真正实施修复计划并验证恢复与基线升降 | `approve_and_execute` + `verification` + `alert_cleared/metrics_improved` 观测 + `rollback` 已具备。 | 执行状态、关键修复时间线（审批/执行/观察/结果）与“一次性执行”进度已可见。 | 部分实现 | 缺“修复前后基线对比与趋势（提升/下降）”的持久化与展示。 |
| 4. 修复过程可追溯、可回滚、可对话修改与完善 | `AuditLogger`（hash链）、`/api/sessions/{session_id}/events`、`/api/remediate/{session_id}/rollback`、`plan revise` 已具备。 | 对话改方案与审批可用；修复页可按会话回放关键修复事件。 | 部分实现 | 前端缺显式“手动回滚”操作入口（后端接口已具备）。 |
| 5. 可演示知识库功能（列举、调用） | `/api/knowledge/documents`、`/api/knowledge/search` 与 CLI `knowledge ingest/search` 可用。 | Knowledge 页面支持列表与搜索。 | 已实现（基础演示） | 上传按钮未接入真实入库流程，演示深度有限。 |
| 6. 技能可查看、可编辑 | 当前仅 `GET /api/skills` 列表，无编辑类接口。 | Skills/SkillDetail 为只读展示。 | 部分实现（查看已实现，编辑未实现） | 缺技能编辑 API（create/update/delete/validate）与前端编辑页。 |

## 距离完全实现的关键差异（P0/P1）

- P0：自动基线生成与更新闭环（覆盖 DoD 1/3）。
- P0：技能编辑能力（覆盖 DoD 6）。
- P1：前端手动回滚入口与回滚结果呈现（覆盖 DoD 4）。
- P1：知识库上传入库联动（覆盖 DoD 5）。

## 接口能力边界（本轮文档结论）

- 本次仅更新文档，不修改代码接口。
- 已有接口：`/api/diagnose`、`/api/remediate/*`、`/api/knowledge/*`、`/api/skills`（GET）。
- 缺失接口：技能编辑类接口（POST/PUT/PATCH/DELETE）、基线自动生成写入接口/任务说明。
- 前端路由增强：`/remediation/:sessionId`（深链会话回放）。

## 验收证据索引（当前已有）

- `sre_agent/scripts/wave5_real_drill.py`
- `sre_agent/scripts/wave5_acceptance_report.py`
- `sre_agent/tests/test_api.py`
  - `test_e2e_alert_poller_syncs_alertmanager_alerts_to_snapshot_and_ws`
  - `test_e2e_rollback_route_returns_success_and_emits_progress`
  - `/api/memory/baseline` 相关接口用例
- `npm run test -- src/App.test.tsx`
- `npm run test -- src/pages/Remediation.test.tsx src/store/remediationStore.test.ts`
- `npm run test -- src/pages/Diagnosis.test.tsx`
- `npm run test`（前端全量通过：20 files / 67 tests）
- `python -m pytest sre_agent/tests/test_api.py -q`（后端功能用例通过：58 passed）
- `python -m pytest sre_agent/tests/test_start_frontend_backend.py -q`（启动脚本用例通过：5 passed）
- `python sre_agent/scripts/sync_frontend_contract.py --check`
- `python sre_agent/scripts/sync_contract_doc.py --check`
- `python sre_agent/scripts/wave5_real_drill.py --runtime-info sre_agent/temp/dev_runtime_info.json`
  - 证据目录：`sre_agent/docs/evidence/20260403-1324-wave5-real`
- `python sre_agent/scripts/wave5_acceptance_report.py --run-id 20260403-1324-wave5-real`
  - 报告文件：`sre_agent/docs/evidence/20260403-1324-wave5-real/metrics/wave5_acceptance_report.json`
- 重启与健康检查：
  - `python sre_agent/scripts/start_frontend_backend.py --backend-port 8000 --frontend-port 8080 --runtime-info sre_agent/temp/dev_runtime_info.json`
  - `GET /openapi.json` = 200，`GET /api/runtime/llm/status` = 200，`GET /api/alerts` = 200（Bearer token）
