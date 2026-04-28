# 假设驱动 ReAct 诊断流程落地计划

## Summary

将 TTFT 诊断从“LLM 自由选择工具的 ReAct”升级为“后端维护 Hypothesis Board，LLM 围绕假设补证据”的流程。目标是解决近似语言重复、重复工具调用、证据链漂移、coverage 卡死、诊断广度和深度不稳定等问题。

核心结果：

- 每个诊断方向都有稳定 `cause_key`，不再靠自然语言标题去重。
- 每个工具调用必须服务于某个假设的某类证据需求。
- 已完成、已阻塞、已重复抑制的工具动作会进入下一轮 prompt，并从候选动作中移除。
- TTFT 场景优先落地，非 TTFT 保持现状。
- 修复方案继续按 root_cause rank 执行审批，但 TTFT 的 kill_process 方案只从真实进程证据生成。

## Key Changes

### 1. 数据结构与兼容字段

- 在 `SREAgentState` 增加可选字段 `hypothesis_board`，只作为运行时状态和 session snapshot 存储，不要求数据库迁移。
- Board 使用 dict 结构，版本固定为 `version=1`，TTFT 初始结构包含：
  - `scenario: "ttft"`
  - `hypotheses[]`
  - `completed_evidence[]`
  - `blocked_evidence[]`
  - `suppressed_actions[]`
  - `next_action_candidates[]`
  - `stop_recommendation`
- TTFT 初始假设固定为：
  - `hyp-gpu-contention`: `cause_key=ttft/gpu_contention/<node-or-unknown>/fi_gpu_burn`
  - `hyp-external-load`: `cause_key=ttft/external_load/<external-node>/load_simulator`
  - `hyp-cache-pressure`: `cause_key=ttft/cache_pressure/<service>`
  - `hyp-scheduler`: `cause_key=ttft/scheduler/<service>`
  - `hyp-thermal`: `cause_key=ttft/gpu_thermal/<node-or-unknown>`
  - `hyp-topology-gap`: `cause_key=ttft/topology_gap/<namespace>/<service>`
- 在诊断模型中增加可选字段，保持向后兼容：
  - `DiagnosedRootCause.cause_key: str | None`
  - `Hypothesis.id: str | None`
  - `Hypothesis.cause_key: str | None`
  - `Hypothesis.linked_root_cause_id: str | None`
  - `Hypothesis.evidence_refs: list[str]`
- 不扩展现有 `factor_type` enum；`gpu_thermal` 和 `topology_gap` 在 final diagnosis 中映射为 `factor_type="unknown"`，通过 `cause_key` 和 `layer` 表达真实类型。

### 2. Board 初始化与证据解释

- 在 reason 前初始化 TTFT board；如果 state 已有 board，则复用并基于当前 `tool_runs` 重建一次，保证恢复/重试一致。
- 为 TTFT 定义固定 evidence types：
  - `external_process_find`
  - `service_pods`
  - `pod_listing`
  - `pod_node`
  - `gpu_processes`
  - `gpu_metrics`
  - `ttft_metric`
  - `process_detail`
- 每次 act 后用 deterministic interpreter 更新 board：
  - `process.find` 发现 `load_simulator/stress/wrk/locust/jmeter/fio/iperf` 等白名单进程：确认或贡献 `external_load`。
  - `process.find` 结果为空：反证 `external_load`。
  - `gpu.get_processes` 发现 `fi_gpu_burn/gpu_burn`：确认 `gpu_contention`。
  - `gpu.get_processes` 仅发现 `VLLM::Worker` / 服务进程：反证 `gpu_contention`。
  - `gpu.get_metrics` GPU util 高且温度正常：支持 `gpu_contention` 或服务内部压力，反证 `gpu_thermal`。
  - `gpu.get_metrics` 温度超过阈值：支持 `gpu_thermal`。
  - `prometheus.query_instant` TTFT 高于阈值：确认告警仍有效，但不单独确认根因。
  - `k8s.resolve_service_pods` 为空且 `k8s.list_pods` 已执行仍无法定位服务 pod：将 `pod_node/gpu_processes` 标记为 blocked，并确认 `topology_gap` 作为证据链断点。
- 每条 completed evidence 记录：
  - `evidence_type`
  - `tool`
  - `params_hash`
  - `tool_ref`
  - `result_summary`
  - `satisfies`
  - `information_gain`
- 每条 blocked evidence 记录：
  - `evidence_type`
  - `blocked_by`
  - `reason`
  - `affected_hypotheses`

### 3. Next Action Candidates 与工具约束

- 在每轮 reason prompt 中新增 `Hypothesis Board` section 和 `Next action candidates` section。
- `next_action_candidates` 由后端生成，最多 4 个，按优先级排序：
  1. TTFT 最小 coverage 缺失项。
  2. 已确认高优根因的影响验证。
  3. 主要竞争假设的反证。
  4. topology fallback。
- 候选 action 固定结构：
  - `candidate_id`
  - `hypothesis_id`
  - `evidence_type`
  - `tool`
  - `params`
  - `reason`
  - `repeat_policy`
- LLM prompt 要求：如果要调用工具，必须选择候选中的 `tool + params`；否则输出 final JSON。
- act 阶段校验工具调用：
  - 与候选完全匹配：执行。
  - 同参已成功执行：不执行，记录 `duplicate_suppressed`，并写入 board 的 `suppressed_actions`。
  - 不在候选且不是允许的 forced TTFT coverage call：返回只读失败 observation，提示选择候选或总结，不真实执行。
- 重复阈值固定：
  - 同一个 `dedupe_key` suppress 第 1 次：提示换角度。
  - 第 2 次：从下一轮候选中移除该工具动作，并在 prompt 明确 forbidden。
  - 第 3 次：强制进入 final/blocked diagnosis，不再继续 ReAct。

### 4. Prompt 与收敛逻辑

- `_build_state_rebuilt_sections` 增加：
  - `Hypothesis Board`
  - `Next action candidates`
  - `Forbidden repeated actions`
  - `Blocked evidence`
- `Evidence status` 不再只列 TTFT coverage，也列出已满足的关键工具：
  - `process.find(10.11.4.13): satisfied`
  - `k8s.list_pods(monitoring): satisfied, do not repeat`
  - `gpu.get_processes(worker-03): missing/blocked/satisfied`
- 收敛规则固定：
  - 至少一个高优假设 confirmed，且主要竞争假设已 confirmed/eliminated/blocked，则允许 final。
  - `gpu_processes` 缺失但 `pod_node` 被 topology gap 阻塞时，不再无限要求 GPU coverage。
  - `service_pods + pod_listing` 都完成仍无法定位服务 pod，则输出 `topology_gap` 或 `ambiguous`，不重复 list。
  - 工具预算达到 10 个真实 TTFT tool_runs 或连续 suppress 达阈值时，强制 final。
- final_turn 时禁止工具调用，要求基于 board 输出 diagnosis。

### 5. Root Cause / Hypothesis Canonicalization

- 新增 cause_key 生成逻辑，优先级：
  1. 使用已有 `cause_key`。
  2. 用 `factor_type + entities + evidence_refs` 推导。
  3. 用 process family / node role / tool family 推导。
  4. 最后才用文本 token overlap 兜底。
- 归一化规则：
  - `fi_gpu_burn*`、`gpu_burn*` 统一为 `fi_gpu_burn`。
  - `load_simulator`、`stress`、`benchmark`、`wrk` 等统一到 `external_load` 家族。
  - 中文“占用GPU”“占用全部GPU”“GPU资源争用”映射到 `gpu_contention`。
  - “KV cache / cache miss / cache pressure” 映射到 `cache_pressure`。
  - “scheduler / 调度瓶颈 / 排队” 映射到 `scheduler`。
- `_normalize_hypotheses_payload` 不再只按 description 去重；改为按 `cause_key` 合并。
- confirmed hypothesis 与 root cause 同 `cause_key` 时：
  - hypothesis 保留，但写入 `linked_root_cause_id`。
  - 前端候选假设验证默认不再把它展示成独立未排除候选。
- 合并策略：
  - `confidence` 取高值。
  - `status` 优先级：`confirmed > contributing > testing > suspected > eliminated`。
  - `evidence_for/evidence_against/evidence_refs` 合并去重。
  - title/description 取 root_cause 版本或更短更结构化的版本。
  - `recommended_fix` 只保留通过后端校验的方案。

### 6. Final Diagnosis 与修复方案生成

- 最终诊断仍由 LLM 输出自然语言 JSON，但 prompt 中提供 board-derived skeleton，要求模型保持 `cause_key`、`evidence_refs` 和 `linked_root_cause_id`。
- 后端 finalize 后再次用 board 修正：
  - 缺少 confirmed root cause 时，从 confirmed board hypothesis 生成 root_cause skeleton。
  - LLM 多输出近似重复 root/hypothesis 时按 `cause_key` 合并。
  - LLM 引用了不存在 evidence_ref 时降级为 `suspected` 或移除该 ref。
- TTFT 修复方案生成只使用真实 evidence：
  - `gpu_contention` 仅从 `gpu.get_processes` / process detail 中提取真实 PID 或白名单进程名。
  - `external_load` 仅从 `process.find` 的 suspicious process 列表生成。
  - `cache_pressure/scheduler/topology_gap/gpu_thermal` 默认 `recommended_fix=null`，只给 next_action。
- 继续复用现有多 root_cause rank 审批流程；每个 `root_cause[i].recommended_fix` 独立审批。

## Implementation Plan

### Phase 1: Board 可观测化

- 增加 state 字段 `hypothesis_board`。
- 新增 TTFT board 初始化和重建 helper。
- 在 prompt 中展示 board，但不硬限制工具。
- act 后更新 board，并在 trace 中记录 `hypothesis_board_update`。
- 目标：不改变最终诊断行为，只让 session 能看见每个假设的状态、证据和缺口。

### Phase 2: 候选工具动作控制

- 从 board 生成 `next_action_candidates`。
- Prompt 要求 LLM 从候选动作中选择工具。
- act 阶段校验候选；非候选工具返回 observation，不真实执行。
- duplicate suppress 写入 board，并注入下一轮 prompt。
- 目标：解决 `k8s.list_pods(namespace=monitoring)` 这类重复请求。

### Phase 3: 阻塞与强制收敛

- 实现 blocked evidence。
- service->pod->node 链路失败时，将 GPU evidence 标记 blocked，而不是继续要求 coverage。
- 连续 suppress 达 3 次强制 final。
- TTFT 真实 tool_run 预算固定为 10 次。
- 目标：证据不足时稳定输出 `ambiguous/topology_gap`，不循环。

### Phase 4: cause_key 合并与前端去重

- 给 root_cause 和 hypotheses 增加可选 `cause_key` 等字段。
- normalize 阶段按 cause_key 合并近似语言。
- 前端候选假设展示识别 `linked_root_cause_id`：
  - linked hypothesis 展示为对应 root cause 的验证过程。
  - 未 linked 的 testing/eliminated hypothesis 才作为独立候选展示。
- 目标：避免“占用GPU / 占用全部GPU”重复。

### Phase 5: TTFT 修复方案确定性生成

- LLM 只负责诊断解释；TTFT kill_process plan 由后端从 evidence_signals/board 生成。
- 没有真实 PID 或白名单 target 时不生成 write plan。
- 对 `cache_pressure/scheduler/topology_gap` 不生成 kill_process。
- 目标：修复方案参数稳定、安全，不依赖 LLM 编工具参数。

## Test Plan

- Board 初始化：
  - TTFT alert 初始化 6 个固定 hypothesis。
  - 非 TTFT alert 不初始化 board，现有流程不变。
- Evidence 更新：
  - `gpu.get_processes` 发现 `fi_gpu_burn` 后，`hyp-gpu-contention` 变为 confirmed。
  - `process.find` 为空后，`hyp-external-load` 变为 eliminated。
  - `gpu.get_metrics` 温度正常后，`hyp-thermal` 变为 eliminated。
  - `k8s.resolve_service_pods` 为空且 list_pods 已完成后，`pod_node/gpu_processes` blocked，`topology_gap` 有证据。
- Candidate 生成：
  - 已完成的 `k8s.list_pods(namespace=monitoring)` 不再出现在 candidates。
  - serving node 未解析时不生成 `gpu.get_processes` 候选，改生成 topology fallback 或 blocked final。
  - TTFT 最小 coverage 缺失时，只生成未完成且未 blocked 的 evidence candidates。
- 重复调用：
  - 同参工具重复第 1、2 次被 suppress 并进入 prompt。
  - 第 3 次 suppress 后强制 final/blocked diagnosis。
  - 最近 session 的 `k8s.list_pods(monitoring)` 场景不超过 2 次 suppress。
- Final normalize：
  - `GPU资源争用 - 占用GPU` 和 `GPU资源争用 - 占用全部GPU` 合并为同一 `cause_key`。
  - confirmed hypothesis 与 root cause 同 cause_key 时写入 `linked_root_cause_id`。
  - 前端候选假设不重复展示 linked hypothesis。
- 修复方案：
  - `gpu_contention` 有真实 PID 时生成 `kill_process(node, pid)`。
  - 只有虚拟/非白名单进程名时不生成 plan。
  - `cache_pressure/scheduler/topology_gap` 不生成 `kill_process`。
- 回归：
  - 现有 TTFT graph tests。
  - 多 root_cause remediation approve tests。
  - diagnosis modified report model tests。
  - frontend build。

## Acceptance Criteria

- 最新 `b59a6b40...` 类 session 不再出现近似重复 hypothesis。
- 最新 `421317ea...` 类 session 不再循环请求同参 `k8s.list_pods`。
- TTFT 诊断至少覆盖 GPU contention、external load、cache/scheduler、thermal/topology 中可执行的方向。
- 证据链断在 topology 时，诊断能明确输出 topology/evidence insufficient，而不是持续 ReAct。
- 所有 root_cause 和 hypotheses 都有稳定 `cause_key`，UI 展示不依赖标题去重。
- TTFT write plan 只从真实进程证据生成。

## Assumptions

- v1 只对 TTFT 场景启用 Hypothesis Board；非 TTFT 保持现有 ReAct 行为。
- 不做数据库迁移；board 存在 session state/snapshot JSON 中。
- `factor_type` enum 暂不扩展，避免前后端兼容风险；新语义通过 `cause_key` 和 `layer/title` 表达。
- LLM 仍负责最终中文解释，但证据覆盖、工具候选、重复控制、root/hypothesis 去重由后端确定性逻辑兜底。
- 修复审批和多 root_cause rank 执行流程保持不变，只替换诊断阶段的 evidence/plan 生成质量。
