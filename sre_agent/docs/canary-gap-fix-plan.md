# 灰度发布（Canary）Gap 修复方案

## Context

`CanaryExecutor`（`sre_agent/remediation/canary.py`）已有模型和骨架代码，但存在 3 个核心缺陷导致灰度功能不可用：
1. `monitor_duration` 未生效（`sleep(0)`）
2. batch 参数被丢弃，每次执行全量步骤
3. 无批次事件推送，前端 `batch_status` 数据源缺失

此外 LLM prompt 未引导生成 `canary` 配置，`_normalize_remediation_plan_payload` 也不处理 canary 字段。

## 修改范围

### 1. `sre_agent/remediation/canary.py` — 核心修复

**Gap 1: monitor_duration 未生效**
```python
# 当前 (line 39): await asyncio.sleep(0)
# 修改为: await asyncio.sleep(canary.monitor_duration)
```
执行完一批后等待 `monitor_duration` 秒再检查指标。

**Gap 2: batch 参数被丢弃**
```python
# 当前: lambda _targets: self._execute_steps(plan)
# 问题: execute_step(batch) 中的 batch 未被使用
```
修改 `execute_with_canary` 签名和逻辑：
- `execute_step` 回调改为接收 `(plan, batch_targets)` 参数
- 在 engine.py 中，`_execute_steps` 改为接受可选 `target_filter` 参数
- 当 `target_filter` 非空时，只执行 params 中 node/target 匹配当前 batch 的步骤

**Gap 3: 批次事件推送**
- 新增 `event_callback` 参数（类型 `Callable[[str, dict], Awaitable[None]]`）
- 每批开始/完成时调用 `event_callback(stage, details)` 发布事件
- 事件 stage: `canary_batch_started`, `canary_batch_completed`, `canary_check_passed`, `canary_check_failed`

**Gap 4: 渐进扩量**
- 默认策略: 第一批 `target_percentage`，后续每批翻倍（10%→20%→40%...）
- 新增 `progressive: bool = True` 到 CanaryConfig
- 当 `progressive=True` 时，第 N 批的 batch_size = `min(len(targets), base_size * 2^(N-1))`

### 2. `sre_agent/models/remediation.py` — CanaryConfig 扩展

添加 `progressive` 字段：
```python
class CanaryConfig(StrictFrozenModel):
    enabled: bool = True
    target_percentage: float = Field(default=0.1, gt=0.0, le=1.0)
    monitor_duration: int = Field(default=120, ge=1)
    success_criteria: list[CanaryCondition] = Field(default_factory=list)
    criteria_mode: Literal["all", "any"] = "all"
    max_batches: int = Field(default=3, ge=1)
    auto_rollback_on_regression: bool = True
    progressive: bool = True  # 新增: 渐进扩量
```

### 3. `sre_agent/remediation/engine.py` — 对接 canary 修复

修改 `execute()` 方法中的 canary 分支（line 150-156）：
- 将 `session_id` 和事件回调传入 `execute_with_canary`
- `_execute_steps` 支持按 target 过滤步骤
- `_collect_targets` 保持不变（已正确收集 node/target/service_id）

```python
if plan.canary and plan.canary.enabled:
    targets = self._collect_targets(plan)
    return await self.canary.execute_with_canary(
        plan,
        targets,
        execute_step=self._execute_steps,  # 改为传方法引用
        session_id=session_id,
    )
```

### 4. `sre_agent/api/routes.py` — 批次事件映射

在 `_publish_remediation_progress` 的 `stage_to_event_type` 映射中新增：
```python
"canary_batch_started": EventType.REMEDIATION_PROGRESS,
"canary_batch_completed": EventType.REMEDIATION_PROGRESS,
"canary_verification_passed": EventType.REMEDIATION_PROGRESS,
"canary_verification_failed": EventType.REMEDIATION_PROGRESS,
```

在 approve 路由中，canary 模式的 `_publish_remediation_progress` 调用需要传递 `batch_status` 数据。

### 5. `sre_agent/agent/prompts.py` — 引导 LLM 生成 canary 配置

在 `BASE_SYSTEM_PROMPT` 的 `remediation_plan` JSON schema 中添加可选 `canary` 字段：
```json
"canary": {
  "enabled": true,
  "target_percentage": 0.1,
  "monitor_duration": 120,
  "success_criteria": [{"metric": "...", "operator": "<", "value": 0}],
  "max_batches": 3
}
```

在 prompt 规则中添加：
```
If multiple entities are affected (e.g., multiple nodes or pods), include a canary config
to roll out the fix progressively. Otherwise omit canary.
```

### 6. `sre_agent/agent/nodes.py` — 规范化 canary

在 `_normalize_remediation_plan_payload()` 中处理 `canary` 字段：
- 如果 LLM 返回了 `canary`，验证并保留
- 如果受影响实体 >= 2 且未设 canary，自动填充默认 canary 配置

### 7. 前端 — 无需修改

前端已展示 `batch_status`、灰度进度条、成功判定条件。修复后端事件推送后，前端无需额外改动。

## 不在本方案范围

- **暂停/恢复** — 需要额外状态机，复杂度高，后续迭代
- **LLM 自动生成 canary** — prompt 引导已包含，但 LLM 输出不稳定，需要 normalization 兜底
- **单元测试** — 单独跟进，不在本次修改范围

## 验证方式

1. 构造包含 2+ node 的修复计划，启用 canary，确认按批执行且每批间等待 `monitor_duration`
2. 验证批次事件通过 WebSocket 推送到前端，`batch_status` 正确更新
3. 模拟 canary 条件失败，确认自动回滚触发
4. 确认 `progressive=True` 时批次大小递增（10%→20%→40%）
5. 运行现有测试无回归
