# SRE Agent Gap 与实施计划（2026-03-25）

## 1. 背景与目标

### 1.1 目标

在当前仓库基础上，补齐 `sre_agent` 的运行闭环，使其达到以下最终效果：

1. 前端可看到实时告警列表与聚合状态。
2. Agent 可对告警触发诊断，前端可观察诊断过程（thinking/tool/结论）。
3. 修复流程采用人工审批门控。
4. 修复执行后，前端可看到进度和结果（含失败/回滚）。

### 1.2 边界

1. 本文仅记录 Gap 与实施计划，不实施代码。
2. 默认 `load_simulator` 与 `fault_injector` 已可用，不改其内部实现。
3. 策略固定：
   - 告警来源：Alertmanager 拉取。
   - 契约对齐：以后端现有主契约为准，前端适配。
   - 修复策略：人工审批。

---

## 2. 现状评估

### 2.1 已具备能力

1. 后端基础骨架已存在：
   - FastAPI 应用工厂、鉴权中间件、REST 路由、WebSocket 路由。
   - 诊断-修复核心模块（`IncidentHandler`、`LoopOrchestrator`、`RemediationEngine`）。
2. Agent 诊断能力已存在可复用实现：
   - `run_diagnosis`（LangGraph + tool calling）可单独运行并有测试覆盖。
3. 工具与执行安全框架已具备：
   - `ToolRegistry`、PlanValidator、ApprovalGate、WAL 回滚。
4. 前端页面框架齐全：
   - Topology / Alerts / Diagnosis / Remediation / Chat / Memory / Knowledge / Skills 页面已存在。

### 2.2 当前核心问题

虽然模块齐全，但“端到端可运行闭环”尚未打通，主要是装配、契约、事件流和启动入口存在断点，导致真实联调无法稳定跑通。

---

## 3. Gap 清单（P0/P1）

## P0（阻断闭环）

### P0-1 生产级 `diagnosis_runner` 默认装配缺失

1. 现状：
   - `create_app` 默认使用 `MissingDiagnosisRunner`，若外部未注入，会在诊断时失败。
2. 影响：
   - `/api/diagnose` 与 `/api/handle` 无法用于真实运行。
3. 建议改造：
   - 在服务启动阶段装配默认 `diagnosis_runner`（基于 `agent.run_diagnosis`）。
   - 同步装配 `re_diagnose_runner`，用于 Loop 二次诊断。
4. 验收标准：
   - 未手工注入 runner 时，`/api/handle` 可返回有效 loop 结果而非配置错误。

### P0-2 缺少后端服务启动命令（CLI 无 `serve`）

1. 现状：
   - `sre_agent/cli.py` 目前只有 topology/discover/memory/knowledge 相关命令。
2. 影响：
   - 缺少统一启动入口，前后端联调依赖临时脚本或手工拼命令。
3. 建议改造：
   - 新增 `sre-agent serve --config ...`，统一加载配置与依赖并启动 uvicorn。
4. 验收标准：
   - 单命令可启动 API+WS 服务，日志中可见关键依赖装配状态。

### P0-3 前后端 API 契约不一致

1. 现状：
   - 前端调用含 `/api/diagnosis/session/current`、`/api/remediation/overview`、`/api/remediation/{id}/approve` 等。
   - 后端主路由为 `/api/sessions/{id}`、`/api/sessions/{id}/loop`、`/api/remediate/{id}/approve`。
2. 影响：
   - 前端真实联调出现 404 或数据结构不匹配。
3. 建议改造：
   - 以后端为准，前端客户端与状态管理统一迁移到现有后端主契约。
4. 验收标准：
   - 前端在关闭 MSW 后，所有核心页面接口无 404，且类型对齐。

### P0-4 WebSocket 鉴权参数缺失

1. 现状：
   - 后端 `ws_authenticate` 强制 token。
   - 前端 WS URL 当前未保证附带 token。
2. 影响：
   - thinking trace / alerts 流无法连接，前端无实时过程。
3. 建议改造：
   - 前端 WS 连接统一拼接 token（与 REST Bearer 来源一致）。
4. 验收标准：
   - `/ws/thinking-trace/{id}`、`/ws/alerts` 在有效 token 下稳定收流。

### P0-5 事件发布链路不完整

1. 现状：
   - 当前事件发布主要集中在 loop 进度，缺少完整诊断与修复阶段事件。
2. 影响：
   - 前端无法观察 Agent 全流程，仅能看到局部状态。
3. 建议改造：
   - 标准化发布事件：`alert`、`thinking_step`、`tool_call`、`tool_result`、`diagnosis_result`、`approval_required`、`remediation_progress`、`done/error`。
4. 验收标准：
   - 单次告警处理会话可在前端完整重放关键事件序列。

### P0-6 告警实时输入链路未完整打通

1. 现状：
   - 有 AlertChannel 和相关脚本，但服务层未形成持续拉取 + 快照接口 + WebSocket 广播一体化。
2. 影响：
   - 告警页面无法稳定基于真实 Alertmanager 数据工作。
3. 建议改造：
   - 新增 Alert 拉取服务（后台轮询），统一产出 `/api/alerts` 快照并推送 `/ws/alerts`。
4. 验收标准：
   - Alertmanager 新告警可在一个轮询周期内出现在 UI。

## P1（质量与可运维）

### P1-1 前端默认 MSW 容易掩盖真实问题

1. 现状：
   - Dev 默认启用 mock worker（除非显式关闭）。
2. 影响：
   - 页面“看似可用”但真实后端链路未验证。
3. 建议改造：
   - 改为显式开启 mock（仅 `VITE_USE_MSW=true` 时启用）。
4. 验收标准：
   - 默认开发模式连接真实后端；可按需开启 mock。

### P1-2 环境依赖装配可观测性不足

1. 现状：
   - Channel/runner/knowledge 等依赖缺失时，定位成本较高。
2. 影响：
   - 联调效率低，故障定位慢。
3. 建议改造：
   - 启动日志输出依赖装配摘要与降级信息；关键缺失项显式告警。
4. 验收标准：
   - 启动后可快速识别哪些能力是“真实模式”或“降级模式”。

---

## 4. 前后端契约对账表（当前状态）

| 前端当前调用 | 后端当前主路由 | 结论 | 处理建议 |
|---|---|---|---|
| `GET /api/topology` | `GET /api/topology` | 一致 | 保持 |
| `GET /api/alerts` | 无 | 缺失 | 新增后端 `/api/alerts` |
| `GET /api/diagnosis/session/current` | `GET /api/sessions/{session_id}` | 不一致 | 前端改为基于 session_id 拉取 |
| `GET /api/remediation/overview` | `GET /api/sessions/{session_id}/loop` | 不一致 | 前端改为 loop 结果接口 |
| `POST /api/remediation/{id}/approve` | `POST /api/remediate/{id}/approve` | 路径不一致 | 前端改后端路径 |
| `POST /api/chat` | `POST /api/chat` | 基本一致 | 统一 envelope 解析 |
| `GET /api/knowledge/search` | `GET /api/knowledge/search` | 一致 | 保持 |
| `GET /api/knowledge/documents` | 无 | 缺失 | 前端改为已有能力或新增后端接口 |
| `GET /api/memory/incidents` | `GET /api/memory/incidents` | 一致 | 统一 envelope 解析 |
| `GET /api/memory/patterns` | `GET /api/memory/patterns` | 一致 | 统一 envelope 解析 |
| `GET /api/memory/baseline` | 无 | 缺失 | 前端降级或新增后端接口 |
| `GET /api/skills` | 无 | 缺失 | 前端降级或新增后端接口 |

---

## 5. 事件链路设计（目标）

### 5.1 标准事件类型

1. `alert`
2. `thinking_step`
3. `tool_call`
4. `tool_result`
5. `diagnosis_result`
6. `approval_required`
7. `loop_progress`
8. `remediation_progress`
9. `done`
10. `error`

### 5.2 建议时序

1. 拉取到告警 -> 发布 `alert`（session 预分配或关联）。
2. 进入诊断 -> 连续发布 `thinking_step`、`tool_call`、`tool_result`。
3. 诊断完成 -> 发布 `diagnosis_result`。
4. 进入审批 -> 发布 `approval_required`。
5. 审批通过后执行修复 -> 发布 `remediation_progress`。
6. 结束 -> `done`；失败 -> `error`（含失败点与回滚信息）。

### 5.3 WS 通道

1. 会话级：`/ws/thinking-trace/{session_id}`
2. 全局告警：`/ws/alerts`
3. 鉴权：必须带 token，并支持断线重连后的 last_event_id 衔接。

---

## 6. 人工审批流程说明（会话/审批/回滚）

### 6.1 流程

1. `/api/handle` 触发会话，生成 session_id。
2. 诊断后生成 plan 并注册到 RemediationEngine。
3. 状态进入待审批，前端展示审批弹窗与 plan。
4. 操作员调用 `/api/remediate/{session_id}/approve`。
5. 通过：执行 plan；拒绝：会话进入 rejected/escalated。

### 6.2 回滚

1. 执行过程中失败，按 WAL 自动回滚。
2. 支持人工触发 `rollback` 接口补偿。
3. 回滚结果通过事件与接口对外可见（包含恢复动作列表）。

---

## 7. 分阶段实施里程碑与交付件

## 阶段 P0（先打通闭环）

### 交付件

1. 默认 diagnosis_runner / re_diagnose_runner 装配。
2. `serve` 启动命令。
3. `/api/alerts` + Alert 拉取后台服务。
4. 前端按后端主契约迁移（含审批接口路径调整）。
5. WS token 接入 + 关键事件流打通。

### 完成判定

1. 关闭 MSW 后，前端可完整走通“告警->诊断->审批->修复->结果”。

## 阶段 P1（提升稳定性与可维护性）

### 交付件

1. 启动依赖可观测日志。
2. Mock 开关治理（默认真实后端）。
3. 增补前端缺失接口策略（降级显示或后端补齐）。

### 完成判定

1. 联调过程可快速定位问题来源，减少“假成功”。

---

## 8. 风险与回滚策略

### 8.1 主要风险

1. 新旧 API 同步切换期间前端页面可能出现阶段性不可用。
2. 真实告警流量较大时，事件推送可能出现背压。
3. 审批与会话状态关联不严时，可能发生审批错配。

### 8.2 控制策略

1. 以 feature flag 分阶段切换前端数据源。
2. WS 采用 backpressure 和重连续传策略。
3. 审批请求强制校验 session_id 与当前待审批状态。

### 8.3 回滚

1. 前端保留一版可回退构建配置。
2. 后端新增接口保持向后兼容窗口。
3. 修复执行层仍以 WAL 保障操作回退。

---

## 9. 验证计划（实施后验收标准）

## 9.1 API 合约验收

必须通过：

1. `GET /api/alerts`
2. `POST /api/handle`
3. `GET /api/sessions/{id}`
4. `GET /api/sessions/{id}/loop`
5. `POST /api/remediate/{id}/approve`

检查项：

1. HTTP 状态正确。
2. `SREResponse` envelope 一致。
3. 关键字段与前端类型对齐。

## 9.2 WebSocket 验收

1. `GET ws://.../ws/alerts?token=...`
2. `GET ws://.../ws/thinking-trace/{id}?token=...`

检查项：

1. 无 token 拒绝；有 token 可连接。
2. 事件序列完整、字段合法。
3. 重连后可续传（基于 last_event_id）。

## 9.3 前端联调验收

1. 告警进入页面可见。
2. 触发处理后自动进入诊断会话。
3. 时间线可见思考和工具调用过程。
4. 审批通过后可见修复进度与结果回显。

## 9.4 E2E 演示验收

1. 关闭 MSW。
2. 启动真实后端与前端。
3. 使用真实告警输入（Alertmanager）完成一次闭环演示。
4. 产出演示记录（请求日志、事件日志、结果截图）。

---

## 10. 结论

当前 `sre_agent` 不是“缺算法”，而是“缺闭环装配与契约统一”。优先完成 P0 即可形成可演示、可联调、可逐步生产化的最小闭环；P1 聚焦可运维性和稳定性，降低后续迭代成本。

---

## Wave 1 实施状态更新（2026-03-26）

### P0-1 生产级 `diagnosis_runner` 默认装配缺失
- 状态：已落地（DONE）
- 实施：
  - `create_app` 默认装配 `DefaultDiagnosisRunner`，显式注入优先。
  - 默认装配失败时启动即失败（fail-fast），避免运行期 `/api/handle` 才暴露 runner 缺失。
- 验证：
  - `pytest sre_agent/tests/test_server_default_runner.py -q`（覆盖默认 runner 的 `/api/diagnose` 与 `/api/handle`）。
- 证据：
  - `sre_agent/docs/evidence/wave1_20260326_01/logs/pytest_wave1_additional.log`

### P0-2 缺少后端服务启动命令（CLI 无 `serve`）
- 状态：已落地（DONE）
- 实施：
  - 新增 `sre-agent serve --config ...`（`python -m sre_agent serve --config ...` 等价）。
  - 统一执行 config 加载、app 装配、uvicorn 启动。
- 验证：
  - `pytest sre_agent/tests/test_cli_serve.py -q`
  - `python -m sre_agent serve --config config.yaml --host 127.0.0.1 --port 18091`
- 证据：
  - `sre_agent/docs/evidence/wave1_20260326_01/logs/pytest_wave1_additional.log`
  - `sre_agent/docs/evidence/wave1_20260326_01/logs/serve_netstat_18091.log`

### P0 其余项
- P0-3 ~ P0-6：本次未实施，维持原计划状态。

### 回归与副作用检查
- Agent demo 回归：通过
  - `python sre_agent/scripts/Agent_demo.py --config-json sre_agent/scripts/agent_demo_test.json`
  - 结果归档：`sre_agent/docs/evidence/wave1_20260326_01/agent_demo/agent_demo_test_result.json`
- 结论：Wave 1 改动未观察到 `Agent_demo.py` 回归失败或关键字段缺失。

## Wave 2 状态更新（2026-03-26）
### P0-3 前后端 API 契约不一致
- 状态：已落地（DONE，Wave 2 范围内）。
- 实现方式：
  - 前端 API client 迁移到后端主契约：
    - `POST /api/handle`
    - `GET /api/sessions/{session_id}`
    - `GET /api/sessions/{session_id}/loop`
    - `POST /api/remediate/{session_id}/approve`
  - `alerts` 页面改为：选中告警后先调用 `handle` 获取 `session_id`，再跳转诊断页。
  - 诊断页/修复页改为基于 URL `session_id` 拉取数据。

### Envelope 统一
- 状态：已落地（DONE）。
- 实现方式：
  - 前端增加统一解析函数，对 `SREResponse` envelope 做统一解包。
  - 对仍为旧 mock 结构的接口保留兼容读取，避免测试与联调中断。

### P1-1 前端默认 MSW 容易掩盖真实问题
- 状态：已落地（DONE）。
- 实现方式：
  - 仅在 `import.meta.env.DEV && VITE_USE_MSW === "true"` 时启用 MSW。

### 验证证据
- `npm --prefix sre_agent/frontend run test`：通过（4 passed）。
- `npm --prefix sre_agent/frontend run build`：通过。
- `pytest sre_agent/tests/test_api.py -q`：通过（13 passed）。
- `pytest sre_agent/tests/test_loop_orchestrator.py -q`：通过（6 passed）。
- Agent 回归：
  - `python sre_agent/scripts/Agent_demo.py --config-json sre_agent/scripts/agent_demo_test.json`
  - 结果基线：`sre_agent/scripts/agent_demo_test_result.json`

### 残留风险（保留到 Wave 3）
- `/api/alerts` 后端快照接口与完整告警实时链路仍属于 Wave 3 实施项。

## Wave 3 状态更新（2026-03-26）
### P0-5 事件发布链路不完整
- 状态：已落地（DONE，Wave 3 范围）。
- 实现方式：
  - `IncidentHandler` 增加关键事件发布：
    - `alert`
    - `tool_call`
    - `tool_result`
    - `diagnosis_result`
    - `approval_required`
    - `done/error`
  - `LoopOrchestrator` 既有 `loop_progress` 事件继续保留。
  - `trace_publisher` 升级为可持续订阅与自动 `event_id` 注入，支持 `last_event_id` 续传。

### P0-6 告警实时输入链路
- 状态：部分落地（IN_PROGRESS）。
- 已完成：
  - 新增 `GET /api/alerts` 快照接口（来源为服务内 `alert_store`）。
  - `/ws/alerts` 广播链路可用，且支持 `last_event_id`。
- 未完成：
  - “真实 Alertmanager 后台轮询拉取服务”仍待接入 `AlertChannel` 实时抓取。

### 验证证据
- `pytest sre_agent/tests/test_api.py -q`：通过（15 passed，含 `/api/alerts` 与 `/ws/alerts` 新增校验）。
- `npm --prefix sre_agent/frontend run test`：通过（4 passed）。
- Agent 回归：
  - `python sre_agent/scripts/Agent_demo.py --config-json sre_agent/scripts/agent_demo_test.json`：通过。
  - 基线：`sre_agent/scripts/agent_demo_test_result.json`。
