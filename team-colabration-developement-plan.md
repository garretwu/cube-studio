# Team Collaboration Development Plan

## 1. 目标

基于 `aidc-auto-sre-plan.md` 和 `aidc-auto-sre-plan-multi-agents.md`，制定适合约 3 人、分布在不同机器上的协同开发方案。

约束如下：

- `load_simulator` 和 `fault_injector` 视为已完成 baseline，不纳入本轮开发分工
- 当前开发目标聚焦 `sre_agent`
- 下周先有 2 人启动开发，第 3 人后续加入
- 目标是尽量提高并行度，同时避免频繁冲突
- 各模块需要可单独验证
- 最终统一集成并执行集成测试

---

## 2. 总体协作策略

采用如下模式：

- `1` 名 Coordinator / Integrator
- `3` 条长期功能分支
- `1` 条长期集成分支
- 模块独立开发、独立验证
- 最后集中集成，不做高频全量联调

核心原则：

1. 先冻结共享契约，再开始并行开发
2. 每人只修改自己拥有的目录
3. 所有功能分支统一向集成分支提 PR
4. 每个 PR 必须能独立验证
5. 最终集成阶段只修接线、兼容性和缺陷，不再新增大功能

---

## 3. 分支模型

### 3.1 长期分支

- `main`
  - 仅保留稳定主线
  - 本轮开发不直接向 `main` 提交功能

- `integration/sre-agent-demo`
  - 本轮多人协作唯一集成主干
  - 所有功能分支统一向该分支提 PR

- `release/sre-agent-demo`
  - 当集成测试通过后，从 `integration/sre-agent-demo` 切出
  - 用于最终验收、演示和稳定性修复

### 3.2 人员功能分支

建议固定为 3 条主分支：

- `feature/sre-a-foundation-<owner>`
- `feature/sre-b-data-ui-<owner>`
- `feature/sre-c-core-infra-<owner>`

如果个人需要拆子任务，可在自己主分支下开短期子分支，例如：

- `feature/sre-a-foundation-zhang/models`
- `feature/sre-b-data-ui-li/frontend-pages`
- `feature/sre-c-core-infra-wang/api`

但最终仍需先合回个人主功能分支，再由主功能分支向 `integration/sre-agent-demo` 发起 PR。

---

## 4. 合并策略

### 4.1 合并原则

- 禁止直接 push 到 `integration/sre-agent-demo`
- 所有改动必须通过 PR 合并
- 统一使用 `squash merge`
- 每个 PR 只解决一个独立模块或一个明确子能力
- 不允许把多个不相关子系统混在同一个 PR

### 4.2 集成窗口

推荐固定集成窗口：

- 周二下午
- 周四下午
- 必要时周六补一次

除紧急修复外，不在窗口外随意合并到集成分支。

### 4.3 PR 必备信息

每个 PR 必须写清楚：

- 改动模块
- 触及目录
- 是否影响其他 owner
- 独立验证命令
- 风险说明
- 上游依赖 PR / 分支

---

## 5. 角色与目录所有权

### 5.1 Coordinator / Integrator

负责：

- 冻结并维护共享契约
- 管理集成分支
- 审核 PR
- 处理跨模块冲突
- 统一修改公共入口文件
- 最终集成测试与验收
- 切 `release/sre-agent-demo`

以下路径只允许 Coordinator 修改：

- `sre_agent/__init__.py`
- 顶层依赖文件
- 总体 contract 文档
- 最终 E2E 测试 wiring
- 集成配置和演示配置

### 5.2 Dev-1：Foundation Owner

主分支：

- `feature/sre-a-foundation-<owner>`

负责目录：

- `lib/channels/` 中新增 channel
- `sre_agent/models/`
- 对应模型和 channel 测试

允许修改：

- `lib/channels/log.py`
- `lib/channels/alert.py`
- `lib/channels/ontology.py`
- `lib/channels/knowledge.py`
- `sre_agent/models/*`
- `lib/tests/test_log.py`
- `lib/tests/test_alert.py`
- `sre_agent/tests/test_models.py`

禁止修改：

- `sre_agent/agent/`
- `sre_agent/api/`
- `sre_agent/frontend/`
- `sre_agent/remediation/`

### 5.3 Dev-2：Data + UI Owner

主分支：

- `feature/sre-b-data-ui-<owner>`

负责目录：

- `sre_agent/frontend/`
- `sre_agent/ontology/`
- `sre_agent/knowledge/`
- `sre_agent/memory/`

第一阶段重点：

- 前端骨架
- 8 页面路由
- mock API / mock WebSocket
- ontology / knowledge / memory 的接口和 demo backend

避免修改：

- `sre_agent/api/`
- `sre_agent/remediation/`
- `sre_agent/agent/`

### 5.4 Dev-3：Core + Infra Owner

主分支：

- `feature/sre-c-core-infra-<owner>`

负责目录：

- `sre_agent/agent/`
- `sre_agent/tools/`
- `sre_agent/skills/`
- `sre_agent/guardrails/`
- `sre_agent/api/`
- `sre_agent/remediation/`
- `sre_agent/concurrency/`
- `sre_agent/auth/`
- `sre_agent/safety/`
- `sre_agent/server.py`
- `sre_agent/config.py`
- `sre_agent/cli.py`

---

## 6. 开发阶段安排

### 6.1 阶段 0：启动前准备

由 Coordinator 在开发开始前完成：

- 冻结 `sre_agent/models/*` 字段定义
- 冻结 WebSocket event schema
- 冻结 REST API path 与 request/response 结构
- 冻结 storage interface
- 冻结 `--resume` / checkpoint 入口
- 冻结前端 TS type 的来源
- 冻结目录 owner 表

建议在 `integration/sre-agent-demo` 上打 tag：

- `sre-agent-kickoff-v1`

### 6.2 阶段 1：下周先启动 2 人

#### Dev-1

优先完成 Foundation 层：

- `sre_agent/models/`
- 4 个新增 shared channels
- model serialization tests
- channel unit tests

这是唯一强阻塞其他后端开发的分支，优先级最高。

#### Dev-2

并行完成 Data/UI 的非阻塞部分：

- frontend scaffold
- 8 页面空壳
- mock API / mock WebSocket
- ontology / knowledge / memory 的目录结构和接口层

此阶段不要求真实联通后端。

### 6.3 阶段 2：第 3 人加入后进入主并行

#### Dev-3

在 Dev-1 的 models/channels 合入后开始：

第一步：
- `sre_agent/agent/`
- `sre_agent/config.py`
- `sre_agent/cli.py`

第二步：
- `sre_agent/tools/`
- `sre_agent/skills/`
- `sre_agent/guardrails/`

第三步：
- `sre_agent/api/`
- `sre_agent/remediation/`
- `sre_agent/auth/`
- `sre_agent/safety/`
- `sre_agent/concurrency/`

### 6.4 阶段 3：集中集成

所有模块进入 `integration/sre-agent-demo` 后，由 Coordinator 主导：

- API 与 frontend 真连接
- alert ingress → diagnosis → remediation dry-run → WebSocket → GUI
- `--resume` 恢复链路验证
- Skills / Knowledge / Memory 三页单独验收
- Demo Case 1 / Case 2 跑通
- Case 3 作为 stretch

此阶段禁止继续大规模新功能开发。

---

## 7. 推荐 PR 顺序

推荐按以下顺序进入集成分支：

1. `Foundation PR-1`
   - `sre_agent/models/` + 关键 channel
2. `Foundation PR-2`
   - 剩余 channel + model tests
3. `Data/UI PR-1`
   - frontend scaffold + routes + mock client
4. `Data/UI PR-2`
   - ontology / knowledge / memory demo backend
5. `Core/Infra PR-1`
   - `agent/graph` + `config.py` + `cli.py`
6. `Core/Infra PR-2`
   - `tools/` + `skills/` + `guardrails/`
7. `Core/Infra PR-3`
   - `api/` + `remediation/` + `safety/` + `auth/`
8. `Data/UI PR-3`
   - frontend 接真实 API / WebSocket
9. `Integration PR`
   - wiring、contract 修正、E2E tests、文档收口

---

## 8. 模块级验证要求

### 8.1 Foundation 模块

必须验证：

- `sre_agent/models/*` round-trip serialization
- 新增 channel import / unit tests
- 不重复覆盖已有 `fault_injector` channel 测试

### 8.2 Data/UI 模块

必须验证：

- ontology / knowledge / memory 基础 CRUD 或检索测试
- 前端 mock 模式可运行
- 8 页面路由全部可访问

### 8.3 Core/Infra 模块

必须验证：

- graph 处理 mock alert
- `sre-agent --resume <session_id>` 路径可测
- API 可启动
- remediation dry-run 可跑通
- WAL / SafetyGuard / forbidden write 拦截测试通过

---

## 9. 最终集成测试范围

最终集成阶段统一验证：

- alert → diagnosis → remediation dry-run → GUI timeline 全链路
- Demo Case 1 必做
- Demo Case 2 必做
- Case 3 作为 stretch
- Skills 页单独验收
- Knowledge 页单独验收
- Memory 页单独验收
- `--resume` 崩溃恢复演练
- Gate-3 对应能力检查
- Gate-4 对应 Demo 验收检查

集成阶段只允许：

- 修 wiring
- 修 contract 偏差
- 修测试
- 修明确 bug

不允许在集成阶段继续扩展大功能。

---

## 10. 日常协作机制

### 10.1 每日同步

每天一次短同步，仅汇报：

- 今天改哪些路径
- 是否改 contract
- 当前 blocker
- 是否需要优先合并某个 PR

建议控制在 15 分钟内。

### 10.2 冲突处理规则

- 共享契约冲突由 Coordinator 决策
- 不允许各分支临时维护不同 JSON shape
- 如果要变更 contract，必须先更新文档，再调整实现
- 如果跨 owner 需要改文件，由该文件 owner 执行修改

---

## 11. 执行建议

最推荐的启动顺序：

- 下周 Dev-1 先开 `feature/sre-a-foundation-<owner>`
- 下周 Dev-2 同时开 `feature/sre-b-data-ui-<owner>`
- 第 3 人到位后再开 `feature/sre-c-core-infra-<owner>`

这样可以在只有 2 人的首周仍然保持较高并行度，同时把最容易冲突的共享模型和核心链路拆开。

---

## 12. 结论

该方案适合当前 3 人左右、分批上人、多机协作的实际情况。

它的主要优势：

- 并行度高
- 冲突面小
- 每个模块可以单独验证
- 最终集成边界清晰
- 与 `aidc-auto-sre-plan.md` 和 `aidc-auto-sre-plan-multi-agents.md` 一致
