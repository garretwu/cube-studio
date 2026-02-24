# aidc-auto-sre-plan 重新审核反馈（按新要求补充）

## 0. 说明
本反馈基于以下设计文档重新审阅，并针对新增要求给出**可直接落到 `aidc-auto-sre-plan.md` 各 section 的分阶段 implementation 建议**：

- `load-simulator.md`
- `fault-injector.md`
- `AIDC-auto-SRE.md`

> 约束遵循：不直接修改计划文件；输出到 `-feedback.md`。

---

## 1. 结论（针对本轮 10 条要求）
当前 `aidc-auto-sre-plan.md` 已有较完整骨架，但要满足本轮要求，建议补充为“**阶段目标 + 里程碑 + 并行 session + 验收测试**”的强执行版本。

重点需要强化的方向：

1. 明确 Demo=2个月；后续阶段按 1~1.5 月节奏迭代。  
2. Demo 场景严格收敛为 2 个核心 case：`p95 vLLM latency abnormal`、`RDMA abnormal`，且每个 case 要落 2 个 root cause。  
3. Demo 必须交付 GUI，且显式验收 Skills/知识库/记忆库展示能力。  
4. `load_simulator` 与 `fault_injector` 作为前置依赖，排期必须置前并设置“阻塞门槛”。  
5. 每阶段必须包含 unit test + E2E test，不允许“功能交付无测试”。

---

## 2. 对 10 条新增要求的逐条核对与落位建议

### R1/R9：分 Demo / POC / Prod Phase1-N，并落到计划不同 section
**现状**：已有 Demo/POC/Prod 分节，但“阶段内 implementation 包”粒度不一致。  
**建议**：在每个 section 固定模板（目标/范围/并行 session/验收/风险），避免只写目标不写执行。

### R2：Demo 2 个月；后续每阶段 1~1.5 个月
**现状**：已有月度划分，但未统一成“时长约束”。  
**建议**：
- Demo：M1-M2（8周）
- POC：M3-M4.5（6周）
- Prod Phase 1：M5-M6.5（6周）
- Prod Phase 2：M7-M8.5（6周）
- Prod Phase 3：M9-M10.5（6周）

### R3/R5：Demo 两个 case + 每个 case 约 2 个 root cause（验证 multiple root cause）
**现状**：已有 3 case 描述（含 ambiguous），与本轮“Demo 必须两大 case”不完全一致。  
**建议**：Demo section 调整为“2 大 case + 每 case 2 root cause”，第三个 ambiguous 放到 POC 或作为 Demo Stretch Goal。

**Case A：p95 vLLM latency abnormal（2 root causes）**
- RC-A1: `gpu_contention`
- RC-A2: `network_jitter`（或 `kv cache config`）

**Case B：RDMA abnormal（2 root causes）**
- RC-B1: `ecn_misconfiguration`
- RC-B2: `rdma_link_flap`

### R4：Demo GUI 必须展示 skills / 知识库 / 记忆库
**现状**：计划提到 7 页面，但未将 Skills/Knowledge/Memory 设为 Demo 必达验收指标。  
**建议**：Demo 交付清单新增硬门槛：
- Skills 页面：`list/load/run` 可视化执行
- 知识库页面：检索 + 文档详情
- 记忆库页面：相似事件命中 + 模式置信度

### R6：load-simulator 与 fault-injector 前置，优先实现
**现状**：已有优先级描述，但缺“阻塞关系”。  
**建议**：在并行开发 section 增加 Gate：
- Gate-1：`load_simulator run --output-format json` 稳定后，fault 场景联动开发才可进入 E2E。
- Gate-2：fault WAL + rollback 通过后，sre_agent remediation 才可开自动执行路径。

### R7：Demo 完整文档（部署、demo步骤、config）
**现状**：已有 docs/demo 列表，缺“文档完成定义”。  
**建议**：新增 DoD（Definition of Done）：
- 部署文档可从 0 到跑起（含依赖与 sample config）
- Demo 脚本按步骤可复现 2 case
- config reference 覆盖关键参数与默认值
- FAQ/排障最少 10 条

### R8：必须包含 unit test + end-to-end test
**现状**：有测试条目，但未作为每阶段硬验收。  
**建议**：各阶段统一验收阈值：
- Demo：unit test 覆盖率 >=70%，E2E 通过 2 case（含 GUI 验收流）
- POC+：新增回归 E2E + 契约测试 + 安全扫描门禁

### R10：并行开发，多个 session 同时开发
**现状**：已有 session 分工。  
**建议**：强化“输入/输出契约 + 阻塞关系 + 合并节奏”三件事：
- 每个 session 定义 API/数据契约 owner
- 每周固定集成窗口（例如周三/周六）
- 未过契约测试不得合并

---

## 3. 建议写入 `aidc-auto-sre-plan.md` 的分阶段 implementation（草案）

> 目标：可直接作为各 section 的补丁内容。

### 3.1 Demo 阶段（2个月，M1-M2）

**目标**
- 完成前置依赖：`load_simulator` + `fault_injector` 最小可用闭环
- 完成 2 个 Demo case：
  1) p95 vLLM latency abnormal（2 root causes）
  2) RDMA abnormal（2 root causes）
- GUI 可展示并操作 Skills/知识库/记忆库

**并行 Session（建议 8 个）**
- S1: load_simulator 核心补齐（channels/load profile/reporting）
- S2: fault_injector 骨架 + 安全（WAL/guard/watchdog）
- S3: fault 场景（RC-A、RC-B）
- S4: sre_agent 诊断链路（LangGraph + tools + guardrails）
- S5: remediation + 审批 + rollback 集成
- S6: GUI（7页，重点 skills/knowledge/memory）
- S7: storage/ontology（knowledge + memory + discovery）
- S8: docs + test（单测/E2E/演示脚本）

**验收门槛（硬性）**
- Unit Test：>=70%
- E2E：2 case 全通过（每 case 至少 2 root cause）
- 文档：部署 / demo步骤 / config / 排障文档齐全
- 前置依赖 Gate：load/fault 通过后方可执行完整 SRE 演示

---

### 3.2 POC 阶段（1~1.5个月，M3-M4.5）

**目标**
- 场景扩展（vLLM 与 RDMA 全量场景）
- 提升多候选根因循环验证稳定性
- 引入更完整的策略化修复（canary + validator + policy）

**并行 Session（建议 6 个）**
- P1: 场景扩展与注入编排
- P2: LoopOrchestrator 增强（终止条件/重诊）
- P3: 知识库与记忆策略优化
- P4: GUI 增强（时间线/回放/对比）
- P5: 安全与审计（RBAC + 审计链路）
- P6: 测试与性能压测

**验收门槛**
- Unit + Integration + E2E 回归
- 新增安全扫描（bandit/semgrep）
- 关键链路性能指标达标（诊断时延、修复成功率）

---

### 3.3 Prod Phase 1（1~1.5个月，M5-M6.5）

**目标**
- 生产化基础：HA、部署标准化、可观测、审计合规基础
- 完成 NAT profiling/eval 常态化运行

**并行 Session（建议 5 个）**
- PR1: Helm/部署标准化
- PR2: HA + 灾备基础
- PR3: 可观测体系（metrics/tracing/profiling）
- PR4: 审计与权限体系加固
- PR5: 生产回归测试体系

---

### 3.4 Prod Phase 2（1~1.5个月，M7-M8.5）

**目标**
- 成本优化（多模型路由）
- 知识闭环（Runbook 自动化）
- 联邦记忆能力试点

**并行 Session（建议 4~5 个）**
- PR2-1: LLM 路由策略
- PR2-2: Runbook 生成与审核
- PR2-3: 联邦记忆导入导出
- PR2-4: GUI 运营视图
- PR2-5: 回归与成本看板

---

### 3.5 Prod Phase 3（1~1.5个月，M9-M10.5）

**目标**
- 多集群联邦管理
- 企业级运营与合规
- Skills 市场化与治理

**并行 Session（建议 4~5 个）**
- PR3-1: 多集群控制面
- PR3-2: 灾备自动演练
- PR3-3: 合规与审计报告
- PR3-4: Skills 市场与签名校验
- PR3-5: 全量回归

---

## 4. 需要在计划中立即修正的关键文本点

1. 将 Demo 场景表述从“3 case 必做”改为“2 case 必做 + ambiguous 作为 POC/Stretch”。
2. 在 Demo 交付清单中显式写“GUI 必须展示 Skills/知识库/记忆库”。
3. 在每个阶段 section 增加“Unit Test + E2E”硬性验收行。
4. 在并行开发 section 增加 load/fault 的前置 Gate 与阻塞关系。
5. 各 phase 时长统一为：Demo 2 个月，其余每 phase 1~1.5 个月。

---

## 5. 最小可执行补丁（下一版计划建议直接采纳）

- [ ] Demo section：固定 2 case（vLLM latency abnormal + RDMA abnormal），每 case 2 root causes。  
- [ ] Demo section：新增 GUI 验收（Skills/知识库/记忆库）。  
- [ ] Demo/POC/Prod 各 section：补 Unit Test + E2E 验收条款。  
- [ ] 并行开发 section：补 Gate-1/Gate-2 前置依赖关系。  
- [ ] 时间规划 section：明确 Demo=2月，其余阶段=1~1.5月增量迭代。  
- [ ] 文档交付 section：部署、demo步骤、config、排障四类文档设为必达。

---

## 6. 最终结论
计划主框架正确，但为满足本轮要求，应把“阶段化 implementation + 并行 session + 前置 Gate + 测试硬验收”写成明确执行条款。完成上述修正后，计划将从“方向正确”升级为“可落地、可并行、可验收”的执行计划。
