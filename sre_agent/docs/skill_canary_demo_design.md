# Skill 设计：canary-demo

## 1. 设计目标

这个 skill 的目标是把灰度发布演示变成一个可复用、可操作、可讲解的标准流程。

它的定位参考了 `sre_agent/skills/builtin/gpu-fault-sop/SKILL.md`：

- 它不是泛化的修复 skill。
- 它是一个面向演示的 runbook skill。
- 它负责把“告警 -> 诊断 -> canary -> 验证 -> 扩展”串成一条可讲述的 demo 链路。

## 2. Skill 名称与触发条件

### 建议名称

- `canary-demo`
- 或 `vllm-canary-demo`

### 触发条件

当用户出现以下意图时触发：

- 需要演示 vLLM P95 latency 的灰度修复。
- 需要讲解 canary 发布流程。
- 需要展示故障上报、诊断、灰度验证与回滚。
- 需要前端完整回放修复过程。

### 适用场景

- 现场演示
- 产品说明
- 方案汇报
- 代码联调时的灰度链路验证

## 3. Skill 的职责边界

### 负责的内容

- 解释 demo 目标。
- 指导故障注入与负载模拟的运行顺序。
- 说明前端应展示的关键状态。
- 指导 canary 修复计划如何设计。
- 输出 coding plan，推动灰度链路落地。

### 不负责的内容

- 不直接实现 GPU 故障注入。
- 不替代 remediation engine。
- 不替代前端页面实现。
- 不替代 LLM 诊断逻辑。

## 4. 参考来源

该 skill 的结构参考 `gpu-fault-sop` 的做法：

- 明确适用场景。
- 明确脚本选择指导。
- 明确 Phase 分段。
- 明确观测命令和判定标准。
- 明确恢复和兜底方式。

区别是：

- `gpu-fault-sop` 重点在 GPU 故障运维。
- `canary-demo` 重点在灰度发布演示与链路观感。

## 5. 演示输入与输出

### 输入

- `vLLM P95 latency` 告警。
- 单一 GPU 争用故障。
- `load_simulator` 的稳定负载。
- 当前会话的诊断结果与 remediation plan。

### 输出

- 标准化的演示步骤。
- canary 修复计划。
- 前端展示说明。
- 脚本运行建议。
- coding plan。

## 6. 演示脚本入口约定

建议 skill 说明以下入口脚本：

- `sre_agent/scripts/run_vllm_latency_p95_live_demo.sh`
- `sre_agent/scripts/trigger_vllm_inter_token_latency_alert.py`
- `sre_agent/scripts/vllm_latency_p95_live_demo.py`

skill 应说明：

- 先启动 `load_simulator`。
- 再启动 `fault_injector`。
- 再等待告警。
- 最后启动诊断与修复。

## 7. 与 fault injector / load simulator / 前端的联动方式

### fault injector

- 用于制造单一 GPU 争用。
- 只做一个主故障，保证 demo 叙事清晰。

### load simulator

- 用于维持持续推理压力。
- 用于把 P95 拉到稳定基线以上/以下，便于观察变化。

### 前端

- 告警页展示事件。
- 诊断页展示 ThinkingTrace。
- 修复页展示 canary 批次、观察窗口和回滚。
- 结果页展示 resolved 和指标改善。

## 8. Coding Plan

这个 skill 文档必须附带 coding plan，原因是它不仅要讲故事，还要推动代码真正支持这个故事。

### Phase 1: 补齐 canary 过程事件

目标：让后端可以稳定发出 canary 生命周期事件。

建议改动：

- `sre_agent/remediation/canary.py`
- `sre_agent/api/routes.py`

需要实现：

- `canary_batch_started`
- `canary_batch_completed`
- `canary_check_passed`
- `canary_check_failed`
- 失败时的回滚事件可见

### Phase 2: 让 batch 真正影响执行范围

目标：让“先小范围验证”从叙事变成真实执行。

建议改动：

- `sre_agent/remediation/engine.py`

需要实现：

- 将 batch/targets 传入执行逻辑。
- 让步骤执行真正只作用于当前 canary 范围。
- 让“扩展”成为后续批次，而不是一次性全量。

### Phase 3: 让计划能表达 canary

目标：让 LLM 生成的修复方案带有 canary 配置。

建议改动：

- `sre_agent/agent/prompts.py`
- `sre_agent/remediation/planner.py`
- 必要时补 `sre_agent/models/remediation.py`

需要实现：

- 在 prompt 里要求生成 canary 配置。
- 在 plan normalization 中补默认值和校验。
- 保证 `RemediationPlan` 可表达灰度发布。

### Phase 4: 让前端能完整展示

目标：前端能看到完整灰度链路，而不是只看到最终结果。

建议改动：

- 会话事件流
- 修复页 batch 展示
- WebSocket / events 聚合

需要实现：

- 当前批次显示。
- 观察窗口显示。
- 成功条件显示。
- 回滚状态显示。

## 9. 当前代码与 demo 目标的差距

### 已有能力

- `sre_agent/remediation/canary.py` 已有 canary 执行器骨架。
- `sre_agent/models/remediation.py` 已有 `CanaryConfig`、`CanaryCondition`。
- `sre_agent/remediation/engine.py` 已经把 canary 分支接到主链路。
- 前端已有修复页和事件契约基础。

### 关键差距

- canary 事件粒度还不够强。
- batch 目标与实际执行范围还不够绑定。
- LLM 生成的修复计划未必稳定携带 canary。
- 前端需要更完整的 batch 生命周期事件才能形成“先验证后扩展”的观感。

## 10. 验收标准

一个 canary-demo skill 是否达标，可以按下面标准检查：

- 看到告警后能进入诊断会话。
- 诊断能输出单根因修复计划。
- 修复计划包含 canary 配置。
- canary 批次有明确事件。
- 前端能看到观察窗口与验证结果。
- 失败时能回滚。
- 成功后能扩展并最终完成。

## 11. 交付物建议

最终建议这个 skill 至少包含以下文档化内容：

- demo 目标
- 运行顺序
- 事件流
- 前端展示点
- coding plan
- 验收标准

## 12. 结论

这个 skill 的价值是把灰度发布从“后端实现细节”变成“可讲、可演、可复现”的标准演示流程。

它既能作为 demo 讲解脚本，也能作为后续代码优化的执行路线图。
