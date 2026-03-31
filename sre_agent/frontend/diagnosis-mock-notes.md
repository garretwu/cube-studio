# Diagnosis Mock Notes

## 1. 目标

这版 Diagnosis mock 改成一条可控、连续、按步骤推进的完整排障脚本。

前端交互目标只有两件事：

- 在一个连续对话框中完整展示从诊断到修复的主路径。
- 用步骤列表控制推进节奏，用户只能按顺序点击，不能跳步。

## 2. 核心约束

- 诊断结论固定为 `diagnosis_certainty = "ambiguous"`。
- 只做单轮主路径，不做复诊、loop、异常分支、失败回放。
- `tool_result` 返回后，会在诊断轨迹中沉淀为一条 `Observation`。
- 候选根因直接来自 `diagnosis_result.ranked_candidates`，并可直接携带 `recommended_fix`；候选根因出现后，不再额外插入一轮新的方案 thinking。
- `ThinkingStep.action_type = "remediate"` 只出现在审批通过后的执行阶段，它依赖项目里已有的修复模型层：`RemediationPlan`、`RemediationAction`、`VerificationConfig`、`CanaryConfig`。

## 3. 事件覆盖矩阵

| 事件 | 是否纳入这版脚本 | 用途 |
| --- | --- | --- |
| `thinking_step` | 是 | 展示观测规划、结果归纳、修复执行意图。 |
| `tool_call` | 是 | 展示工具调用真正发出。 |
| `tool_result` | 是 | 展示工具结果回流，并沉淀为 `Observation`。 |
| `diagnosis_result` | 是 | 产出本轮 `ambiguous` 诊断结论。 |
| `approval_required` | 是 | 展示审批卡片。 |
| `remediation_progress` | 是 | 展示修复步骤和 canary 进度。 |
| `done` | 是 | 标记本轮演示结束。 |
| `alert` | 否 | 不单独播放。 |
| `error` | 否 | 不做异常分支。 |
| `loop_start` | 否 | 不做 loop。 |
| `loop_progress` | 否 | 不做 loop。 |

## 4. Event 交互设计要求

以下要求参考 Ant Design X 的 RICH 交互范式，以及常见 Agent 产品的过程可见、状态分层、风险显性确认原则，目标是让事件流既可读、又不过载。

### 4.1 `thinking_step`

- 展现形态：使用 `ThoughtChain` 或等价的“可折叠思考阶段”组件，不直接塞进普通消息气泡正文。
- 默认状态：默认折叠，只展示当前阶段标题和一句摘要；用户可主动展开查看细节。
- 文案要求：用“意图 + 原因”表达，例如“检查 GPU 利用率，判断是否存在单点持续过载”。
- 分层要求：`tool_call` 类型强调“准备查什么”，`conclude` 类型强调“基于什么证据得出什么阶段判断”，`remediate` 类型强调“准备如何执行修复”。
- 节奏要求：同一阶段如果连续出现多条 `thinking_step`，应合并到一个阶段容器内，避免消息流被思考文本刷屏。
- 动效要求：进入时使用轻量展开或渐入，不使用高干扰动画。

### 4.2 `tool_call`

- 展现形态：作为对话流中的系统过程卡片或紧凑气泡，建议和对应的 `tool_result` 成对出现。
- 信息层级：默认只展示 `tool_name` 和“正在查询/执行”的状态标签，`tool_params` 放到折叠区。
- 状态反馈：触发后立即进入 loading，避免用户感知为系统无响应。
- 可读性要求：不要把完整参数 JSON 直接摊开；参数较长时只显示关键字段摘要。
- 关系表达：如果该 `tool_call` 来自上一条 `thinking_step`，在视觉上保持缩进或分组归属，体现“思考 -> 执行”的连续性。

### 4.3 `tool_result`

- 展现形态：作为 `tool_call` 的结果回执卡片，紧跟在对应调用之后。
- 核心内容：只显示本轮判断真正需要的结果摘要，不直接展示全量原始 payload。
- 结果状态：明确区分 `success`、`partial signal`、`not enough evidence` 这类结果语义，而不只是“调用完成”。
- 轨迹映射：在 UI 文案或实现注释中明确，这条结果回流会在 trace 中沉淀为一条 `Observation`。
- 展开策略：默认摘要、支持展开；展开后展示结构化字段，不建议展示未经整理的大段 JSON。
- 视觉关系：和前一条 `tool_call` 组成一组，用户能一眼看出“查了什么，查到了什么”。

### 4.4 `diagnosis_result`

- 展现形态：不能只是普通助手文本，必须是一个高权重的诊断结论卡片。
- 首屏重点：第一视觉先显示 `diagnosis_certainty = "ambiguous"`、主结论摘要、受影响范围、优先级。
- 信息组织：将“当前首选判断”和“为什么还不能 confirmed”同时展示，减少黑盒感。
- 风格要求：结论区强调收敛而非绝对确定，避免使用过度肯定的视觉语言。
- 后续衔接：该卡片下方应自然承接候选根因卡片区，而不是让用户继续在消息流里找候选项。

### 4.5 `approval_required`

- 展现形态：必须使用独立审批卡片，不使用一条“是否批准”文本替代。
- 确认类型：这是显性确认节点，审批前阻断后续执行。
- 重点信息：必须同时展示方案内容、影响范围、置信度、安全等级、canary 边界和回滚摘要。
- 操作要求：按钮至少包含 `批准`、`驳回`、`稍后处理`，并在视觉上区分主操作和次操作。
- 风险表达：涉及不可逆或高风险动作时，用警示色或风险标签，但避免整卡高饱和报警色造成误导。
- 上下文要求：卡片中必须明确“基于哪个候选根因、当前 certainty 是什么”。

### 4.6 `remediation_progress`

- 展现形态：使用步骤进度流或时间线，不要退化成连续聊天文本。
- 进度粒度：至少要能区分“执行中”“已完成”“观察中”“扩容中”“完成”。
- 过程感知：长耗时阶段要有持续反馈，避免执行过程像卡住。
- 关键节点：canary 观察、放量、完成这三个节点必须有单独状态，不应混成一句话。
- 风险控制：若这版只做 happy path，也要保留回滚位置信息展示入口，但不展开失败分支。
- 关系表达：该阶段与 Step 8 的 `thinking_step(action_type="remediate")` 组成“计划执行意图 + 执行进度”的双层结构。

### 4.7 `done`

- 展现形态：使用简短的完成态总结卡片或系统消息，不再继续追加新的诊断过程块。
- 信息重点：说明本轮演示完成、当前状态已收敛、修复执行已结束。
- 收尾要求：给出明确结束感，例如 `resolved` 状态、完成标识、最终一句总结。
- 交互要求：步骤列表全部置为完成态，审批和执行类按钮变为不可用，避免用户误以为还能继续推进。

### 4.8 被排除事件的处理原则

- `alert`、`error`、`loop_start`、`loop_progress` 不进入这版脚本，也不需要在 UI 中预留独立展示位。
- 如果底层 mock 数据里仍保留这些类型，前端这版可以直接忽略，不纳入演示路径。

## 5. 演示主线

场景沿用当前项目已有的 GPU / vLLM / 延迟劣化主题：

- 告警主对象：`VLLM 延迟过高`
- 关联信号：`GPU 温度偏高`
- 受影响实体：`gpu-01`、`node-gpu-01`
- 受影响服务：`vllm-latency`、`chat-serving`

候选根因固定为 3 个：

1. 异常基准测试进程导致 GPU 资源争用
2. 推理分片负载倾斜，导致单节点持续过载
3. 网络侧瞬时拥塞放大了推理链路时延

## 6. 步骤列表

mock 按钮点击后，先展示步骤列表。步骤必须顺序执行，前一步完成后置灰，下一步解锁。

| 顺序 | 步骤名 | 目的 | 事件 / UI 产物 | ThinkingStep.action_type |
| --- | --- | --- | --- | --- |
| 1 | 确认影响范围 | 先告诉用户问题集中在哪些节点和服务 | `tool_call`、`tool_result` | 无新增 `ThinkingStep` |
| 2 | 规划观测动作 | 展示系统准备查什么、为什么这样查 | `thinking_step` | `tool_call` |
| 3 | 收集关键观测 | 展示实际查询、结果回流、结果归纳 | `tool_call`、`tool_result`、`thinking_step` | `conclude` |
| 4 | 输出初步诊断 | 给出固定的 `ambiguous` 结论 | `diagnosis_result` | 无新增 `ThinkingStep` |
| 5 | 展示候选根因 | 展示候选根因卡片组 | 候选根因卡片 | 无新增 `ThinkingStep` |
| 6 | 生成待审批方案 | 直接展开第 1 候选的 `recommended_fix` | 待审批方案卡片 | 无新增 `ThinkingStep` |
| 7 | 人工审批执行 | 展示审批闸口 | `approval_required` 审批卡片 | 无新增 `ThinkingStep` |
| 8 | 执行受控修复 | 展示按计划执行修复和 canary | `thinking_step`、`remediation_progress` | `remediate` |
| 9 | 输出最终结果 | 结束本轮演示 | `done` + 总结消息 | 无新增 `ThinkingStep` |

## 7. 对话脚本骨架

### Step 1: 确认影响范围

产物：`tool_call` -> `tool_result`

建议顺序：

1. 一条说明性助手消息：先确认影响半径。
2. `tool_call`：`topology.get_blast_radius`
3. `tool_result`：返回 `node-gpu-01`、`vllm-latency`、`chat-serving`

这一阶段只建立背景：

- 问题集中在 `node-gpu-01`
- 波及 `vllm-latency`、`chat-serving`
- 当前没有扩散到更大范围

### Step 2: 规划观测动作

产物：`thinking_step(action_type="tool_call")`

这一阶段只展示“接下来查什么”。

建议内容：

- 检查 GPU 利用率是否长期高位。
- 检查节点上是否存在异常 GPU 进程。
- 检查网络拥塞是否足以解释当前时延放大。

### Step 3: 收集关键观测

产物：多轮 `tool_call` -> `tool_result` -> `thinking_step(action_type="conclude")`

建议标准序列：

1. `tool_call`：`metrics.query(gpu_utilization)`
2. `tool_result`：`node-gpu-01` GPU 利用率持续高位
3. `thinking_step(action_type="conclude")`：局部热点成立，但还不足以排除异常进程或负载倾斜
4. `tool_call`：`check_gpu_processes(node-gpu-01)`
5. `tool_result`：返回可疑基准测试进程线索
6. `thinking_step(action_type="conclude")`：GPU 争用优先级上升，但还需要补一眼网络侧证据
7. `tool_call`：`network.get_congestion_summary(sw-01)`
8. `tool_result`：网络有轻微信号，但不足以成为首要根因
9. `thinking_step(action_type="conclude")`：压缩总结多轮证据，形成候选排序

这里要明确：每次 `tool_result` 返回后，都会在诊断轨迹里沉淀为一条 `Observation`。

### Step 4: 输出初步诊断

产物：`diagnosis_result`

固定输出：

```ts
diagnosis_certainty = "ambiguous"
```

这一阶段只做一次诊断结果输出，不再掺入新的工具调用或修复规划。

### Step 5: 展示候选根因

产物：候选根因卡片组

数据来源：`diagnosis_result.ranked_candidates`

交互约束：

- 默认展示 3 张卡片，按 `rank` 升序排列
- 默认展开第 1 候选，其余折叠
- 允许浏览卡片，不允许跳到后续步骤

每张卡片展示：

- `#1 / #2 / #3`
- `root_cause`
- `root_cause_layer`
- `confidence`
- `root_cause_entities`
- `evidence_summary`
- `distinguishing_verification`
- 如果存在 `recommended_fix`，额外显示 `可生成待审批方案`

### Step 6: 生成待审批方案

产物：待审批方案卡片

这里不再新增 thinking，而是直接展开第 1 候选根因的 `recommended_fix`。

卡片展示：

- `plan_id`
- `root_cause`
- `description`
- `estimated_impact`
- `confidence`
- `priority`
- `safety_level`
- `canary.target_percentage`
- `steps.length`
- 回滚方式摘要

### Step 7: 人工审批执行

产物：`approval_required` + 审批卡片

审批卡片展示：

- 标题：`批准修复方案`
- 当前候选：`候选根因 #1`
- 当前诊断确定性：`ambiguous`
- `plan_id`
- `root_cause`
- `description`
- `estimated_impact`
- `confidence`
- `priority`
- `safety_level`
- `canary.target_percentage`
- `steps.length`
- 回滚方式摘要

审批按钮文案：

- `批准`
- `驳回`
- `稍后处理`

建议 mock 示例：

```ts
const approvalRequiredEvent = {
  schema_version: "1.0",
  type: "approval_required",
  session_id: "sess-latency-001",
  timestamp: "2026-03-30T09:20:00Z",
  data: {
    candidate_rank: 1,
    diagnosis_certainty: "ambiguous",
    message: "当前将基于优先级最高的候选根因执行受控验证性修复，请人工确认。",
    plan: diagnosisResult.ranked_candidates[0].recommended_fix
  }
};
```

### Step 8: 执行受控修复

产物：`thinking_step(action_type="remediate")` + `remediation_progress`

这是整条脚本里唯一需要出现 `remediate` 的位置。

建议顺序：

1. `thinking_step(action_type="remediate")`：准备按计划执行修复，并先从低风险 canary 开始
2. `remediation_progress`：步骤 1 执行中
3. `remediation_progress`：步骤 1 完成
4. `remediation_progress`：canary 10% 观察中
5. `remediation_progress`：canary 通过，扩大流量
6. `remediation_progress`：修复完成

### Step 9: 输出最终结果

产物：`done` + 总结消息

结束态建议：

- `session.status = "resolved"`
- 输出 `done`
- 一条助手总结消息，说明本次演示完成、影响已收敛

## 8. 诊断结果建议结构

mock 数据优先兼容 `models/diagnosis.py`，并用一份 `diagnosis_result` 同时驱动 Step 4、Step 5、Step 6。

```ts
const diagnosisResult = {
  root_cause: "当前无法确认单一根因，优先怀疑 node-gpu-01 上的 GPU 资源争用",
  root_cause_layer: "hardware",
  root_cause_entities: ["gpu-01", "node-gpu-01"],
  confidence: 0.62,
  diagnosis_certainty: "ambiguous",
  impact_summary: "问题集中在 node-gpu-01 附近，已影响 vllm-latency 与 chat-serving，但尚未证实是单一硬件或网络根因。",
  affected_services: ["vllm-latency", "chat-serving"],
  triage_priority: "P1",
  hypotheses: [
    {
      description: "异常基准测试进程导致 GPU 资源争用",
      status: "testing",
      evidence_for: ["GPU 利用率长时间维持高位", "节点存在异常 GPU 进程线索"],
      evidence_against: ["尚未直接完成进程级核验"],
      confidence: 0.62
    },
    {
      description: "推理分片负载倾斜导致单节点过载",
      status: "testing",
      evidence_for: ["影响集中在单个推理节点", "业务层延迟与资源热点同步出现"],
      evidence_against: ["暂未观察到明确调度异常日志"],
      confidence: 0.54
    },
    {
      description: "网络侧瞬时拥塞放大了推理链路时延",
      status: "testing",
      evidence_for: ["历史上存在 RoCE/ECN 相关案例"],
      evidence_against: ["当前交换机队列与链路拥塞证据不足"],
      confidence: 0.33
    }
  ],
  ranked_candidates: [
    {
      rank: 1,
      root_cause: "异常基准测试进程导致 GPU 资源争用",
      root_cause_layer: "hardware",
      root_cause_entities: ["gpu-01", "node-gpu-01"],
      confidence: 0.62,
      evidence_summary: "GPU 利用率持续高位，影响集中在单节点，且已有异常 GPU 进程线索，但尚未完成最终核验。",
      distinguishing_verification: "检查 node-gpu-01 上的 GPU 进程列表，并观察终止异常进程后 p95 延迟是否回落。",
      recommended_fix: {
        plan_id: "plan-candidate-1",
        root_cause: "异常基准测试进程导致 GPU 资源争用",
        description: "先小流量排空热点分片，再终止异常 GPU 进程，最后按金丝雀恢复。",
        estimated_impact: "仅对单节点分片进行受控操作，业务整体风险可控。",
        confidence: 0.78,
        priority: "P1",
        safety_level: "high",
        canary: {
          enabled: true,
          target_percentage: 0.1,
          monitor_duration: 120,
          success_criteria: [
            { metric: "vllm_p95_ms", field: "value", operator: "<", value: 300 },
            { metric: "gpu_utilization", field: "value", operator: "<", value: 0.75 }
          ],
          criteria_mode: "all",
          max_batches: 3,
          auto_rollback_on_regression: true
        },
        steps: [
          {
            step_id: 1,
            description: "先从热点节点排出 10% 金丝雀流量。",
            tool: "k8s_cordon_drain",
            params: { node: "node-gpu-01", percentage: 0.1 },
            rollback_tool: "k8s_uncordon",
            rollback_params: { node: "node-gpu-01" },
            verification: { method: "wait", wait_seconds: 30 },
            timeout: 60
          },
          {
            step_id: 2,
            description: "终止节点上的异常 GPU 进程。",
            tool: "shell_command",
            params: { host: "node-gpu-01", command: "pkill gpu-burn" },
            rollback_tool: null,
            rollback_params: null,
            verification: {
              method: "tool_call",
              tool: "check_gpu_processes",
              tool_params: { host: "node-gpu-01" },
              condition: { field: "process_count", operator: "==", value: 0 },
              wait_seconds: 30
            },
            timeout: 45
          }
        ]
      }
    },
    {
      rank: 2,
      root_cause: "推理分片负载倾斜导致单节点过载",
      root_cause_layer: "service",
      root_cause_entities: ["svc-vllm", "node-gpu-01"],
      confidence: 0.54,
      evidence_summary: "单节点长期承压，但暂缺直接证明是调度或分片权重错误。",
      distinguishing_verification: "检查分片权重、实例负载分布与最近变更记录。"
    },
    {
      rank: 3,
      root_cause: "网络侧瞬时拥塞放大了推理链路时延",
      root_cause_layer: "network",
      root_cause_entities: ["sw-01", "node-gpu-01"],
      confidence: 0.33,
      evidence_summary: "网络路径不能完全排除，但当前证据不足以排在前两位之前。",
      distinguishing_verification: "检查交换机队列深度、ECN 与链路丢包摘要。"
    }
  ]
};
```

## 9. 一句话总结

这版 Diagnosis mock 用“一条连续脚本 + 顺序步骤推进 + ambiguous 候选诊断 + 审批后受控修复”的方式取代原来的离散交互和复诊叙事，目标是让前端可以一次性实现完整主路径演示。
