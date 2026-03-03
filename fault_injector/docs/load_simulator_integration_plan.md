# Fault Injector 与 Load Simulator 联动计划

Last Updated: 2026-03-02
Owner: fault_injector / load_simulator maintainers

## 1. 目标

建立一套稳定的“故障注入 + 负载施压”联动流程，支持在故障窗口内自动拉起 `load_simulator`，并将结果纳入 `fault_injector` 会话证据。

核心目标：
- 可复现
- 可审计
- 可回滚
- 可在 dry-run 下安全演练

## 2. 联动接口定义

### 2.1 命令接口

统一调用命令：

```bash
python -m load_simulator run --config <ls_config.yaml> --output-format json [--only <scenario>]...
```

### 2.2 适配器函数（建议）

在 `fault_injector/scenarios/base.py` 新增：

```python
async def _run_load_simulator(
    config_path: str,
    only: list[str],
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    ...
```

职责：
1. 启动异步子进程
2. 采集 stdout/stderr
3. 解析 JSON
4. 校验 `exit_code == 0`
5. 返回规范化 summary

### 2.3 返回契约

最小返回字段（建议）：
- `exit_code: int`
- `summary: dict`
- `session_id: str | None`
- `artifacts: dict`（可选）

解析失败、超时、退出码异常都应抛出明确错误类型。

## 3. 配置扩展计划

在 `fault_injector` 场景参数中新增联动字段（建议）：

```yaml
scenarios:
  network_jitter:
    enabled: true
    params:
      interface: eth0
      delay_ms: 80
      load_simulator:
        enabled: true
        config_path: load_simulator/config/notebook-soak-only.yaml
        only: [inference]
        timeout_seconds: 1000
        strict: true
```

字段说明：
- `enabled`: 是否联动 LS
- `config_path`: LS 配置路径
- `only`: 可选，限制 LS agent
- `timeout_seconds`: 子进程超时
- `strict`: 失败是否中断本次 fault scenario

## 4. 编排集成方案

### Phase 1: Adapter 落地

- 在 `scenarios/base.py` 增加统一 `_run_load_simulator`
- 增加 JSON 解析、超时、stderr 截断、错误分类
- 单测覆盖：成功、超时、JSON 非法、exit_code!=0

### Phase 2: 场景接入

- 先接 `network_jitter` 与 `platform_cascade`
- 在 `inject()` 前后定义联动窗口：
  1. 启动 load simulator
  2. 注入故障并观察
  3. 汇总 LS 结果并进入恢复

### Phase 3: 会话与报告

- 将 LS 摘要写入 `session.events`
- 保存 LS 原始 stdout/stderr 到 `fault-reports/sessions/<id>/evidence/`
- 报告中展示 fault 与 load 的时间对齐信息

### Phase 4: 稳定化

- 新增 `--strict-preflight` 联动门禁（可选）
- 增加“联动失败降级策略”配置
- 补齐 e2e 冒烟脚本

## 5. 安全与恢复约束

1. 不得绕过 `SafetyGuard`
2. 任何会影响远端状态的操作必须遵守 WAL 先写原则
3. 超时必须主动终止 LS 子进程，避免僵尸负载
4. `strict=false` 时也要记录显式 warning 事件
5. dry-run 模式下：
   - 不执行真实故障注入
   - LS 调用可选择 mock/跳过，并记录模拟结果

## 6. 验证计划

### 6.1 单元测试

- Adapter 解析与错误路径
- 配置校验（联动字段合法性）

### 6.2 集成测试

- `fault_injector run --dry-run` + 联动字段
- `fault_injector run` 在测试环境联动一个最小 LS profile

### 6.3 回归检查

- 不启用联动时，行为与当前版本一致
- recover/resume 流程不受影响

## 7. 建议执行顺序（两周）

Week 1:
1. 完成 adapter + schema 扩展 + 单测
2. 接入 1 个场景并完成 dry-run 联调

Week 2:
1. 接入第 2 个场景
2. 完成会话证据输出 + 文档 + 冒烟脚本
3. 执行一次真实测试环境演练并固化 runbook

## 8. 最小联调用法（目标态）

```bash
python -m fault_injector validate-config fault_injector/fault-injector-test.yaml
python -m fault_injector run --config fault_injector/fault-injector-test.yaml --scenario network_jitter --dry-run
python -m fault_injector run --config fault_injector/fault-injector-test.yaml --scenario network_jitter
```

当场景配置启用 `load_simulator` 联动时，以上命令会在故障窗口中自动触发 LS 运行并收集摘要。
