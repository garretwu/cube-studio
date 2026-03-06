# Fault Injector 与 Load Simulator 架构说明

最后更新: 2026-03-06  
状态: Active

## 1. 文档范围

本文基于当前代码实现，说明以下内容:
- 当前代码结构
- 运行时主流程
- 已完成项目与阶段进度
- 下一步计划

## 2. 当前代码结构

### 2.1 fault_injector

核心模块:
- `fault_injector/cli.py`: 命令入口（`run`、`recover`、`resume`、`validate-config`、`list-scenarios`）
- `fault_injector/config/`: Pydantic Schema、默认值、YAML 加载
- `fault_injector/orchestrator/`: 引擎、调度器、会话、watchdog
- `fault_injector/scenarios/`: 场景实现（vLLM RC-1~RC-6、RDMA F-1~F-6）与注册表
- `fault_injector/agents/`: 分层代理（hardware/os/platform/service/monitor）
- `fault_injector/safety/`: 安全检查与 WAL 回滚日志
- `fault_injector/tests/`: unit/integration/e2e 与场景回归测试

### 2.2 load_simulator

核心模块:
- `load_simulator/cli.py`: run/validate/list 命令
- `load_simulator/orchestrator/engine.py`: single/mixed/stress/soak 模式执行
- `load_simulator/agents/`: inference/pipeline/finetune/notebook
- `load_simulator/config/`: 配置 schema 与加载

### 2.3 通道层

fault_injector 运行时使用 `lib/channels`:
- `ssh.py`
- `prometheus.py`
- `kubernetes.py`
- `redfish.py`
- `ipmi.py`
- `switch.py`

## 3. 运行时主流程（当前实现）

标准执行相位:
1. `init`: 初始化 session/channels/guard/rollback/watchdog
2. `baseline`: 采集监控基线并做质量评估
3. `inject`: 注入故障
4. `observe`: 观测窗口（故障窗口 + 可选 load simulator 并行等待）
5. `recover`: 恢复故障
6. `verify`: 恢复校验
7. `report`: 输出 `session.json` 与 `report/report.json`

调度模式:
- 顺序模式: 按已启用 scenario 逐个执行
- 组合模式: `combined_scenario.phases` 定时注入/恢复

## 4. 当前已完成项目（截至 2026-03-06）

1. 核心编排链路完成  
已实现从 CLI 到引擎、session 事件、WAL 回滚、watchdog 的完整闭环。

2. 场景体系完成并可注册运行  
已注册 12 个场景（vLLM RC-1~RC-6 + RDMA F-1~F-6）。

3. 场景级 load simulator 联动已落地  
`network_jitter`、`platform_cascade` 已接入后台异步联动与 `strict/best-effort` 失败策略。

4. load simulator 子进程契约完成  
已实现 JSON 输出校验、超时 kill、取消处理、错误归一化。

5. 基线配置化能力已完成  
支持 `monitor.baseline_queries` 自定义基线查询，不再只依赖硬编码默认项。

6. 基线质量门禁已完成  
支持 `baseline_min_samples`、`baseline_max_error_ratio`、`baseline_require_non_zero`、`baseline_strict`，并输出 `baseline_quality_warning` 事件。

7. Prometheus 采样统计能力已完成  
`collect_baseline` 已维护 `sample_count/error_count/error_ratio/zero_ratio/last_error` 统计（`last_baseline_stats`）。

8. observe 采样窗口修复已完成  
观测采样改为使用完整 `observe_duration`，避免仅采样 1 次的问题。

9. 测试覆盖已补齐关键路径  
已有单测覆盖 load_simulator 联动、基线质量告警、监控查询渲染告警、observe 时长行为。

## 5. 当前进度

### 5.1 总体状态

- P0（核心可运行能力）: 已完成
- P1（监控基线可信度提升）: 已完成主要实现，待补充报告聚合展示
- P2（联动编排统一化）: 未开始（仍以场景级实现为主）

### 5.2 monitor_baseline_change_plan 对齐情况

1. baseline 查询配置化: 已完成  
2. Prometheus 采样错误可见性: 已完成核心统计（session/report 聚合字段仍可增强）  
3. observe 采样窗口修复: 已完成  
4. baseline quality gate: 已完成（warning + strict）  
5. 场景查询默认值优化: 已完成部分（如 network_jitter 指标）  

## 6. 下一步计划

### P0（本周）

1. 扩展 load simulator 接入场景  
优先将 `gpu_contention` 接入与 `network_jitter` 同等联动契约。
2. 收敛 load_simulator 事件重复记录  
统一 `load_simulator_warning` 生成路径，减少重复事件。

### P1（下周）

1. 增强报告聚合字段  
在 `report/report.json` 增加 baseline 质量摘要和联动结果摘要字段。
2. 增加联动前置校验  
加入 `load_simulator.timeout_seconds` 与 `watchdog.auto_recover_timeout` 的硬校验或明确告警策略。
3. 增补回归测试  
覆盖多目标节点、combined 模式下联动场景与失败路径。

### P2（两周内）

1. 统一联动执行层（integration runner）  
从“场景内局部逻辑”演进为“统一编排能力”。
2. 支持 fault_injector -> load_simulator 参数透传  
支持 `duration/mode/concurrency` 等运行时覆盖参数。
3. 产出联调 runbook  
固化配置模板、超时建议和排障步骤。
