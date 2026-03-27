# SRE Agent 完整链路代码改进与工程验收方案（v2026-03-26）

## 1. 文档定位
- 文档类型：实施蓝图（人类可读 + AI 可执行）。
- 作用：指导 `sre_agent` 按波次完成 P0/P1 全链路打通，并完成工程级验收。
- 约束：本方案仅定义实施步骤与验收标准，不直接修改业务目标。

## 2. 目标与边界
### 2.1 最终目标
在 Lab 真实环境完成可重复的完整闭环：
1. 真实告警上报并被系统采集。
2. SRE Agent 自动诊断并展示 thinking/tool 调用过程。
3. 输出根因与修复计划并进入人工审批。
4. 审批后执行修复、失败可回滚。
5. 修复后进行验证，前端可观察指标变化并产出证据归档。

### 2.2 固定策略
- 验收环境：Lab 真实环境（Alertmanager + 真实指标源）。
- 契约策略：以后端主契约为准，前端适配。
- 审批策略：人工审批门控（必须经过 approval）。
- 验收策略：量化门槛必过（非仅演示通过）。

### 2.3 不在本阶段处理
- 不改 `load_simulator` 与 `fault_injector` 内部逻辑。
- 不引入新的平台级调度系统。
- 不更换现有认证体系，仅做链路对齐。

## 3. 公共契约（实施基线）
### 3.1 REST
- `GET /api/alerts`
- `POST /api/handle`
- `GET /api/sessions/{id}`
- `GET /api/sessions/{id}/loop`
- `POST /api/remediate/{id}/approve`

### 3.2 WebSocket
- `/ws/alerts?token=...`
- `/ws/thinking-trace/{id}?token=...`
- 支持 `last_event_id` 重连接续传。

### 3.3 标准事件类型
- `alert`
- `thinking_step`
- `tool_call`
- `tool_result`
- `diagnosis_result`
- `approval_required`
- `loop_progress`
- `remediation_progress`
- `done`
- `error`

## 4. 实施波次（Wave 1~5）

## Wave 1（P0 装配）
### Objective
打通后端默认可运行能力，做到“不注入 runner 也能跑最小闭环”。

### Inputs
- 配置文件（含 auth/ontology/memory/remediation 配置）。
- 现有 `sre_agent` server/cli/runtime 代码。

### Tasks
1. 装配默认 `diagnosis_runner` 与 `re_diagnose_runner`。
2. 新增统一启动入口 `sre-agent serve --config ...`。
3. 启动日志输出依赖装配摘要（runner/channel/knowledge/memory）。

### Expected Output
- 单命令可启动 API + WS。
- `/api/handle` 在默认配置下可执行且不报 “runner 未配置”。

### Exit Criteria
- 启动后 60 秒内可完成健康调用与一次 `handle`。

### Rollback
- 保留旧启动方式并可回退到显式注入 runner 模式。

## Wave 2（契约统一）
### Objective
前端与后端主契约一致，关闭“看起来可用但真实不可用”的假联调。

### Tasks
1. 前端 API client 迁移到主契约。
2. 审批路径统一到 `/api/remediate/{id}/approve`。
3. 统一 `SREResponse` envelope 解析。
4. 默认关闭 MSW，仅 `VITE_USE_MSW=true` 时启用。

### Expected Output
- 前端关闭 MSW 后核心页面无 404。
- 关键页面可获取真实后端数据。

### Exit Criteria
- `alerts -> diagnosis -> remediation` 页面链路可在真实后端模式访问。

### Rollback
- 保留一版前端构建与 mock 模式开关用于回退排障。

## Wave 3（实时链路）
### Objective
实现真实告警采集 + 事件流实时可观测。

### Tasks
1. 实现 `/api/alerts` 快照。
2. 实现 `/ws/alerts` 广播。
3. 完整发布会话关键事件（thinking/tool/result/approval/remediation/done/error）。
4. WS token 鉴权 + `last_event_id` 续传。

### Expected Output
- 告警在轮询周期内出现在页面。
- 诊断过程在时间线上可见。

### Exit Criteria
- 单次会话可完整重放关键事件序列。

### Rollback
- 告警链路可降级为“仅快照，不实时流”模式。

## Wave 4（审批与修复）
### Objective
保证审批状态机、修复执行与回滚可追踪、可验证。

### Tasks
1. 审批态严格校验（session_id + 当前状态）。
2. 审批通过后执行修复，拒绝进入 rejected/escalated。
3. 失败路径触发 `error` 与 WAL 回滚可见。
4. 修复验证结果写回会话并上报事件。

### Expected Output
- 审批通过/拒绝行为可追溯。
- 失败有清晰回滚记录和结果状态。

### Exit Criteria
- 至少一条成功修复与一条失败回滚路径演练通过。

### Rollback
- 允许人工触发 rollback 补偿并保留审计日志。

## Wave 5（工程化）
### Objective
提升稳定性与可维护性，满足工程级上线前验收。

### Tasks
1. 启动依赖状态可观测（真实模式/降级模式）。
2. WS 重连与背压策略验证。
3. 前端缺失能力页面提供降级显示策略。
4. 统一证据归档结构并自动化收集。

### Expected Output
- 联调问题定位路径明确。
- 演练证据可复盘、可审计。

### Exit Criteria
- 工程级门槛达标并形成验收报告。

## 5. 测试计划（必须执行）

## 5.1 Unit Test
### Scope
- 后端：`test_api.py`、`test_loop_orchestrator.py`、`test_auth.py`、`test_remediation.py` 及改动点测试。
- 前端：`frontend/src/api/client.test.ts`、WS/store 相关单测。

### Pass Criteria
1. 新增/修改路径均有覆盖。
2. 关键失败路径必须覆盖：
   - 无 token。
   - 审批拒绝。
   - 修复失败/回滚。

## 5.2 Integration Test
### Scope
1. API 合约：状态码、envelope、字段类型一致性。
2. WS 合约：鉴权、事件顺序、断线续传。
3. Agent 工具链：`tool_call -> tool_result -> diagnosis_result`。

### Pass Criteria
- 合约检查全部通过，无结构性不一致。

## 5.3 E2E Test（关闭 MSW）
### User Journey
1. 页面看到真实告警。
2. 发起处理并进入诊断会话。
3. 查看 thinking/tool 时间线。
4. 人工审批。
5. 修复执行并展示结果。

### Pass Criteria
- 全流程成功且会话可追溯（session_id 全链路一致）。

## 5.4 Real Test（Lab）
### Scope
- 至少 3 轮不同告警类型演练。

### 每轮必记录字段
- 触发时间
- 检测时间
- 诊断时间
- 审批耗时
- 修复耗时
- 恢复时间

### Pass Criteria
- 3 轮均满足闭环，证据完整。

## 5.5 分步验收测试（新增强制）
1. Step 1：真实告警被 `/api/alerts` 与 `/ws/alerts` 捕获。
2. Step 2：SRE Agent 进入诊断并展示 `thinking/tool`。
3. Step 3：输出根因与修复计划，状态进入 `approval_required`。
4. Step 4：人工审批后执行修复，展示 `remediation_progress`。
5. Step 5：修复验证通过，指标回落并产出 `done`。
6. Step 6：失败场景触发 `error` + 回滚，结果可见。

每一步必须满足“三证合一”：
- 画面证据（截图/录屏）
- 日志证据（API/WS/服务日志）
- 指标证据（时序变化）

## 6. 工程级验收门槛（量化）
1. 完整链路成功率 >= 95%（连续 20 次演练）。
2. 必需事件缺失率 <= 1%。
3. 告警进入 UI P95 <= 10s。
4. WS 事件到达 P95 <= 2s。
5. WS 重连成功率 >= 99%。
6. 重连后续传正确率 100%（抽样）。
7. 审批后到修复完成 P95 <= 5min（按场景微调）。
8. 会话可追溯性 100%（session/审批/工具/修复全链路记录完整）。

## 7. 证据归档规范
统一目录：`sre_agent/docs/evidence/<run_id>/`
- `api/`：请求响应样本、契约校验结果。
- `ws/`：事件流记录、重连续传记录。
- `metrics/`：关键指标曲线与导出数据。
- `ui/`：关键步骤截图/录屏。
- `summary.md`：本轮结论、风险、遗留问题单。

`run_id` 命名建议：`YYYYMMDD-HHMM-<scenario>-<env>`。

## 8. 执行模板（AI 可直接按此运行）
每个任务使用统一模板：
- `Objective`：该步要达成什么。
- `Inputs`：需要的配置、环境、凭据、前置状态。
- `Commands`：执行命令（含参数）。
- `Expected Output`：期望输出或状态。
- `Evidence`：要保存的文件与截图。
- `Exit Criteria`：通过条件。
- `Rollback`：失败回退动作。

## 9. 角色与职责
- Backend：服务装配、API/WS、审批/回滚、事件发布。
- Frontend：契约适配、实时展示、降级策略、MSW 开关治理。
- QA：测试执行、分步验收、证据归档。
- SRE/Operator：真实告警演练、审批操作、结果确认。

## 10. 风险与应对
1. 契约切换期 404/字段不匹配。
- 应对：灰度切换 + 契约回归测试先行。
2. 实时流背压或丢事件。
- 应对：限流、批处理、重连续传、事件完整率监控。
3. 审批错配。
- 应对：严格 session 状态校验与审计日志校验。
4. 真环境噪声影响结论。
- 应对：固定演练窗口、固定场景脚本与基线。

## 11. 里程碑与完成定义
- M1：Wave 1 完成（默认可启动、默认可诊断）。
- M2：Wave 2 完成（前后端主契约统一，关闭默认 MSW）。
- M3：Wave 3 完成（告警与诊断实时事件链路打通）。
- M4：Wave 4 完成（审批与回滚可演练可追溯）。
- M5：Wave 5 完成（量化门槛达标，形成工程验收报告）。

最终完成定义：
- 分步验收 6 步全部通过。
- 四层测试（unit/integration/E2E/real）全部通过。
- 工程级量化门槛全部达标。
- 证据归档完整且可复盘。

## 12. Wave 2 执行记录（2026-03-26）
### 已完成项
1. 前端 API client 已统一到后端主契约：
   - `POST /api/handle`
   - `GET /api/sessions/{id}`
   - `GET /api/sessions/{id}/loop`
   - `POST /api/remediate/{id}/approve`
2. 审批路径已从 `/api/remediation/{id}/approve` 迁移为 `/api/remediate/{id}/approve`。
3. 前端已统一 `SREResponse` envelope 解析。
4. MSW 维持显式开启策略（默认关闭）。

### 回归结果
1. `npm --prefix sre_agent/frontend run test`：通过（4 passed）。
2. `npm --prefix sre_agent/frontend run build`：通过。
3. `pytest sre_agent/tests/test_api.py -q`：通过（13 passed）。
4. `pytest sre_agent/tests/test_loop_orchestrator.py -q`：通过（6 passed）。
5. Agent 回归：
   - 命令：`python sre_agent/scripts/Agent_demo.py --config-json sre_agent/scripts/agent_demo_test.json`
   - 结果：通过，基线更新到 `sre_agent/scripts/agent_demo_test_result.json`。

### 注意事项
- 为提升真实环境回归稳定性，`agent_demo_test.json` 的 `log_since` 已调整为 `6h`。
- `/api/alerts` 与告警实时链路完整打通仍在 Wave 3 范围内。

## 13. Wave 3 执行记录（2026-03-26）
### 已完成项
1. `GET /api/alerts` 快照接口已落地（返回 `alerts + clusters`）。
2. `/ws/alerts` 已支持 `last_event_id` 续传。
3. `trace_publisher` 升级为持续订阅流，支持自动 `event_id` 与断线续传基础能力。
4. `IncidentHandler` 已补齐会话关键事件发布：
   - `alert`
   - `tool_call`
   - `tool_result`
   - `diagnosis_result`
   - `approval_required`
   - `done/error`

### 未完成项（仍属 Wave 3）
1. 真实 Alertmanager 后台轮询拉取服务（`AlertChannel` 定时抓取并写入 alert store）尚未接入。

### 回归结果
1. `pytest sre_agent/tests/test_api.py -q`：通过（15 passed）。
2. `npm --prefix sre_agent/frontend run test`：通过（4 passed）。
3. Agent 回归：
   - `python sre_agent/scripts/Agent_demo.py --config-json sre_agent/scripts/agent_demo_test.json`
   - 通过，基线已更新为 `sre_agent/scripts/agent_demo_test_result.json`。
