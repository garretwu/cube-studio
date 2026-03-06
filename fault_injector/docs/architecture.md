# Fault Injector 与 Load Simulator 架构说明

最后更新: 2026-03-04  
状态: Active

## 1. 文档范围

本文描述当前代码中的运行时架构与联动现状，覆盖:
- `fault_injector`: 故障注入、观测、恢复、会话与证据落盘
- `load_simulator`: 压测负载生成与阶段化执行
- `lib/channels`: 通用通道层（SSH/Redfish/Switch/K8s/Prometheus）
- `fault_injector -> load_simulator` 的已实现调用契约、流程与限制

## 2. 当前代码架构

### 2.1 fault_injector

核心模块:
- `fault_injector/cli.py`: 命令入口（`run`、`recover`、`resume`、`validate-config`、`list-scenarios`）
- `fault_injector/config/*`: 配置 Schema（Pydantic）与 YAML 解析
- `fault_injector/orchestrator/*`: 会话生命周期、调度、watchdog、报告
- `fault_injector/scenarios/*`: 场景实现（vLLM 延迟类、RDMA 异常类）
- `fault_injector/agents/*`: 分层代理（hardware/os/platform/service）
- `fault_injector/safety/*`: 安全门禁与 WAL 回滚日志

执行相位:
1. `init`: 组件初始化（session / channels / guard / rollback / watchdog）
2. `baseline`: 基线采集
3. `inject`: 故障注入
4. `observe`: 观测窗口（可并行等待 load simulator）
5. `recover`: 恢复
6. `verify`: 恢复校验
7. `report`: 报告落盘

### 2.2 load_simulator

核心模块:
- `load_simulator/cli.py`: 命令入口（`run`、`validate-config`、`list-scenarios`）
- `load_simulator/orchestrator/engine.py`: 模式计划（`single/mixed/stress/soak`）、阶段执行、adaptive 规则
- `load_simulator/agents/*`: inference / pipeline / finetune / notebook
- `load_simulator/config/*`: 配置 schema、默认值、加载器

典型职责:
- 生成可控负载并输出结构化 JSON 摘要
- 在 `soak/stress` 模式下按多阶段执行（总时长可能显著大于单 agent duration）

### 2.3 通用通道层（lib/channels）

主要通道:
- `ssh.py` (`asyncssh`)
- `redfish.py` (`httpx`)
- `switch.py` (`ncclient`)
- `kubernetes.py`
- `prometheus.py` (`httpx`)

场景通过通道层执行外部操作，配合 `SafetyGuard` 与 `RollbackJournal` 保证可控与可恢复。

## 3. fault injector 调用流程（当前实现）

以命令为例:

```bash
python -m fault_injector run --config fault_injector/fault-injector-test.yaml --scenario network_jitter --yes
```

主链路:
1. CLI 加载配置并筛选 `--scenario`
2. `FaultOrchestrator.run()` 创建会话并启动 watchdog
3. 进入 `inject`，调用对应 agent/scenario
4. 场景注入成功后进入 `observe`
5. `observe` 阶段并行等待:
   - 故障观测时长 `sleep(duration)`
   - load simulator 后台任务完成（若启用）
6. 进入 `recover`、`verify`
7. 写入 `session.json` 与 `report/report.json`

## 4. Load Simulator 联动（已实现）

### 4.1 联动配置入口（场景级）

在 `scenarios.<name>.params.load_simulator` 或 `scenarios.<name>.load_simulator` 配置:
- `enabled`
- `config_path`
- `only`
- `timeout_seconds`
- `strict`

配置解析位于:
- `fault_injector/config/schema.py` (`LoadSimulatorConfig`)
- `fault_injector/config/loader.py`

### 4.2 运行契约（已落地）

`fault_injector/scenarios/base.py` 已实现:
- `_run_load_simulator(...)`: 异步子进程调用 `python -m load_simulator run ... --output-format json`
- 捕获 stdout/stderr
- 解析 JSON，校验 `exit_code == 0`
- 超时/失败抛出 `LoadSimulatorError`
- 超时与取消时显式 kill 子进程，避免悬挂

### 4.3 并发关系（当前行为）

已调整为:
- 场景 `inject` 前仅启动 LS 后台任务（不阻塞故障注入）
- 注入成功后在 `observe` 阶段并行等待 `sleep + LS task`
- 谁更慢等待谁，完成后再 recover/verify

该行为可避免“先等 LS 导致故障注入未执行”的旧问题。

### 4.4 事件与日志

会话事件:
- `load_simulator_run`
- `load_simulator_warning`

终端日志（已补充）:
- LS 任务调度
- 子进程启动参数
- 子进程完成耗时与摘要信息
- 失败/取消原因
- 当 `load_simulator.timeout_seconds >= watchdog.auto_recover_timeout` 时输出风险告警

## 5. 已实现功能清单

1. fault injector 全链路可运行: 注入、观测、恢复、校验、报告  
2. load simulator CLI 与多模式执行可运行  
3. 场景级 load simulator 联动已实现并有单测覆盖  
4. 联动失败策略已实现:
   - `strict=true`: 标记错误（当前为注入后在 observe 阶段判定）
   - `strict=false`: 记录 warning 并继续
5. 会话证据可追踪:
   - `session.json` 记录事件与结果
   - `report/report.json` 记录输出摘要

当前已接入 LS 的场景:
- `network_jitter`
- `platform_cascade`

## 5.1 GPU Contention 参数约定（RC-1）

`gpu_contention` 通过 `gpu_burn` 执行，当前参数映射:
- `params.duration` -> `gpu_burn [TIME]`
- `params.memory` -> `gpu_burn -m`（默认 `"60%"`）
- `params.gpu_ids` -> `gpu_burn -i`

行为约定:
1. 未配置 `gpu_ids`（或配置为空列表）: 对全部 GPU 生效（不传 `-i`）
2. 配置单个 GPU: 例如 `gpu_ids: [0]`
3. 配置多个 GPU: 例如 `gpu_ids: [0, 1]`（每个 GPU 启动独立 `gpu_burn` 进程）
4. 兼容旧字段 `gpu_id`（仅单卡），但推荐统一使用 `gpu_ids`

配置示例:

```yaml
scenarios:
  gpu_contention:
    enabled: true
    target_nodes: ["worker-01"]
    params:
      duration: 120
      memory: "60%"
      # 全部 GPU（默认）
      # gpu_ids: []
      # 单卡
      # gpu_ids: [0]
      # 多卡
      # gpu_ids: [0, 1]
```

## 5.2 GPU Contention 与 Load Simulator 联动设计（新增方案）

目标: 在 `gpu_contention` 故障窗口内稳定施加真实业务负载，形成“GPU 资源争用 -> 服务指标抖动 -> 恢复后回归”的完整证据链。

### 5.2.1 设计原则

1. 保持现有场景级联动契约，不引入破坏性配置变更。
2. 默认“先启动负载，再注入争用，再共同观测”，确保故障发生在有业务压力的窗口内。
3. 严格区分 `strict=true/false` 行为，避免联动失败时语义不清。
4. 结果可追踪: `session.json` 与 `report/report.json` 必须能还原关键时间线。

### 5.2.2 建议配置契约（gpu_contention）

在 `scenarios.gpu_contention.params` 下新增（或复用）:

- `load_simulator.enabled`: 是否启用联动。
- `load_simulator.config_path`: LS 配置文件路径。
- `load_simulator.only`: 可选，限制执行 agent（如 `["inference"]`）。
- `load_simulator.timeout_seconds`: LS 子进程超时。
- `load_simulator.strict`: 联动失败是否判定场景失败。
- `load_simulator.preload_seconds`: 预热窗口（建议 15~30 秒），给 LS 建连与流量爬坡。

参考配置:

```yaml
scenarios:
  gpu_contention:
    enabled: true
    target_nodes: ["worker-01", "worker-02"]
    params:
      duration: 180
      memory: "70%"
      gpu_ids: [0, 1]
      load_simulator:
        enabled: true
        config_path: "load_simulator/config/inference-soak.yaml"
        only: ["inference"]
        timeout_seconds: 600
        strict: true
        preload_seconds: 20
```

### 5.2.3 时序编排（建议）

1. `inject` 前启动 LS 后台任务，并等待 `preload_seconds`。
2. 执行 `gpu_burn` 注入（按 `gpu_ids` 并发拉起）。
3. 进入 `observe`，并行等待:
   - `gpu_contention.duration`
   - LS 任务完成
4. `recover` 阶段停止全部 `gpu_burn` 进程。
5. `verify` 阶段检查 GPU 利用率/温度/服务延迟是否回落到阈值内。

说明: 若 LS 执行时长大于故障时长，可在 `observe` 末尾继续等待 LS；若希望严格同窗，可在后续迭代增加 `stop_with_fault=true` 策略。

### 5.2.4 失败策略（建议）

1. LS 启动失败:
   - `strict=true`: 直接标记场景失败，不执行注入。
   - `strict=false`: 记录 `load_simulator_warning`，继续执行 `gpu_contention`。
2. LS 运行超时:
   - kill 子进程并记录超时原因。
   - `strict=true` 标记失败；`strict=false` 降级为 warning。
3. `gpu_burn` 注入失败:
   - 立即进入恢复，回收已启动进程。
   - 无论 strict 与否，场景判定失败（故障本体失败）。

### 5.2.5 可观测性与证据模型（建议）

`session.json` 至少记录:
- `gpu_contention_inject_start/end`
- `load_simulator_run.start/end/success/error`
- `gpu_contention_recover_start/end`

`report/report.json` 增加聚合字段:
- `gpu_contention.active_gpu_ids`
- `gpu_contention.memory_setting`
- `integration.overlap_seconds`（LS 与故障窗口重叠时长）
- `integration.strict_mode`
- `integration.final_status`

### 5.2.6 与 watchdog 协同（硬约束）

建议在配置校验阶段增加:
1. `load_simulator.timeout_seconds < global.safety.auto_recover_timeout`
2. `preload_seconds < params.duration`
3. `timeout_seconds >= preload_seconds + params.duration`

不满足时:
- `strict=true` 配置校验失败；
- `strict=false` 输出 warning 并允许执行（用于测试环境）。

### 5.2.7 分阶段落地建议

P0（本周）:
1. `gpu_contention` 接入与 `network_jitter` 一致的 LS 运行契约。
2. 新增 `preload_seconds`，打通“预热 + 注入 + 并行观测”。
3. 增补单测: 成功、超时、strict 降级。

P1（下周）:
1. 补充 `report` 聚合字段与时间线对齐展示。
2. 增加配置前置校验（watchdog 协同约束）。
3. 输出 runbook（含推荐参数组合）。

## 6. 待完善项

1. 联动覆盖范围有限  
   - 目前不是所有场景都接入 LS。

2. 全局联动编排能力尚未实现  
   - 文档设想中的全局 `load_simulator_integration` 策略（如 `overload_then_inject` DSL）尚未落地为统一执行器。

3. 参数透传能力不足  
   - 当前未直接支持从 fault injector 向 LS 透传 `--duration/--mode` 等 CLI 覆盖参数，主要依赖 LS config 文件本身。

4. watchdog 与联动时长协调依赖人工配置  
   - 当 LS 实际运行时长大于 `auto_recover_timeout` 时，watchdog 可能抢先触发自动恢复。

5. 事件去重与可观测性可继续优化  
   - 某些失败路径下 warning 事件可能重复记录，后续可进一步收敛。

## 7. 运维与调试建议

1. 先验证 LS 配置的实际总时长，再设 `timeout_seconds`  
   - `soak/stress` 模式为多阶段，总时长通常大于单段 `duration_seconds`。

2. 保持 `auto_recover_timeout > load_simulator.timeout_seconds`  
   - 避免 watchdog 在联动尚未完成时提前恢复。

3. 以 `session.json` 为事实来源  
   - 重点查看 `load_simulator_run.success/error/result.duration_seconds`。

## 8. 相关文档

- `fault_injector/docs/load_simulator_integration_plan.md`
- `fault_injector/docs/techdesign.md`
- `fault_injector/docs/PRD.md`
- `fault-injector.md`
- `load-simulator.md`
- `fault_injector/agent_docs/project_brief.md`

## 9. 下一步计划（联动增强）

### 9.1 目标与边界

目标是在当前“场景级联动已可用”的基础上，补齐统一编排能力，支持故障注入与压测联动的标准化触发、执行与证据沉淀。

优先实现范围:
1. 统一联动执行器（而非分散在各 scenario 的局部逻辑）
2. 四类触发方式: 随机、定时、动态、人工
3. CLI 覆盖参数透传（fault injector -> load simulator）
4. 与 watchdog/safety 的时序安全协同

### 9.2 优先级事项（P0/P1/P2）

P0:
1. 建立统一联动编排层（integration runner）
2. 定义并落地四类触发模式:
   - `random`: 按权重随机选场景，支持 seed 复现
   - `timed`: 按时间表触发（复用现有 scheduler 能力）
   - `dynamic`: 按指标阈值触发（Prometheus 条件命中注入）
   - `manual`: 人工确认后触发（CLI confirm/外部信号）
3. 统一会话事件模型，收敛 warning 重复记录

P1:
1. 增加参数透传能力，支持向 LS 透传:
   - `--duration`
   - `--mode`
   - `--concurrency`
   - `--strict-preflight`
2. 统一 strict/best-effort 失败策略在四种触发模式下的行为
3. 增加 preflight 风险检查:
   - `load_simulator.timeout_seconds < watchdog.auto_recover_timeout`

P2:
1. 补齐 e2e 冒烟与 runbook
2. 扩大 LS 接入场景覆盖面
3. 增强报告中的“fault 时间线 vs load 时间线”对齐展示

### 9.3 两周实施里程碑

Week 1:
1. 实现联动执行器骨架与 `timed` 模式最小闭环
2. 接入 `random` 与 `manual` 模式
3. 完成配置 schema 扩展与单测（成功/超时/错误/取消）

Week 2:
1. 接入 `dynamic` 模式（基于 Prometheus 阈值规则）
2. 完成 LS CLI 参数透传与 strict 策略统一
3. 补充会话证据输出、文档和冒烟脚本
4. 在测试环境完成一次完整联动演练并固化 runbook

### 9.4 验收标准（Definition of Done）

1. 功能验收:
   - 四类触发模式均可在 dry-run 下稳定执行
   - 至少两个真实场景支持联动并产出结构化证据
2. 安全验收:
   - 任意失败路径均可回滚，WAL active faults 最终为 `0`
   - watchdog 不早于联动窗口抢占恢复（有配置检查和告警）
3. 可观测性验收:
   - `session.json` 中完整记录 `load_simulator_run` 与 `load_simulator_warning`
   - `report/report.json` 能反映联动结果摘要与关键耗时
4. 回归验收:
   - 未启用联动配置时，行为与当前版本一致

### 9.5 推荐执行命令（联调）

```bash
python -m fault_injector validate-config fault_injector/fault-injector-test.yaml
python -m load_simulator validate-config load_simulator/config/notebook-soak-only.yaml
python -m fault_injector run --config fault_injector/fault-injector-test.yaml --scenario network_jitter --dry-run --yes
python -m fault_injector run --config fault_injector/fault-injector-test.yaml --scenario network_jitter --yes
```
