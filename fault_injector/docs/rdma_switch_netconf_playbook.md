# RDMA Switch NETCONF Playbook

> 适用项目: `fault_injector`
> 
> 目标: 指导 AI/开发者通过 `lib/channels/switch.py` 实现和验证 RDMA 相关故障注入场景（F-1~F-6）。

## 1. 约束与原则

1. 所有交换机操作必须走 NETCONF (`SwitchChannel`)，禁止走 CLI 直连路径。
2. 安全门禁由 `fault_injector/safety/guard.py` 统一负责；场景仅调用，不重复实现危险拦截逻辑。
3. 每个注入动作必须执行统一流程:
   - 基线采集
   - guard 校验
   - 注入
   - 注入后验证
   - 回滚并验证恢复
4. 参考规范文档:
   - `fault_injector/docs/H3C_NETCONF_GUIDE.md`

## 2. 关键代码位置

- Switch Channel: `lib/channels/switch.py`
- RDMA 场景: `fault_injector/scenarios/rdma_anomaly.py`
- 安全门禁: `fault_injector/safety/guard.py`
- 回滚日志(WAL): `fault_injector/safety/rollback.py`

## 3. 推荐实现模式

### 3.1 SwitchChannel 能力分层

1. 读取能力（用于基线与验证）
   - `get_interface_status(switch, interface)`
   - `get_interface_config(switch, interface)`
2. 写入能力（用于注入与回滚）
   - `apply_interface_config(...)`
   - `shutdown_port(...)` / `bringup_port(...)`
   - `apply_raw_config(...)`（保留为高级扩展入口）
3. 验证能力
   - `verify_admin_state(switch, interface, expected)`

### 3.2 场景模板（推荐）

在场景里统一复用 `_SwitchRDMACommon`：
1. `_target` 获取并校验 switch/interface
2. `_guard_switch_action` 调用 guard
3. `_capture_baseline` 读取原始配置
4. 执行注入
5. 读取配置验证注入成功
6. `recover` 调 `_restore_interface_baseline`

## 4. 测试策略（按 testing.md）

### 4.1 单元测试

路径:
- `fault_injector/tests/unit/features/channels/test_switch_channel.py`
- `fault_injector/tests/unit/features/scenarios/test_scenarios.py`

重点覆盖:
1. 基线读取成功/失败
2. 注入后验证成功/失败
3. 回滚成功/失败
4. guard 拦截时应拒绝执行
5. WAL 记录与 `mark_recovered`

### 4.2 集成测试（真实设备）

必须包含:
1. 注入前读取端口状态（baseline）
2. 注入后再次读取并断言变更已生效
3. 回滚后读取并断言恢复到 baseline

## 5. 实操检查清单（真实交换机）

1. 确认 NETCONF 开启且账号可用
2. 仅使用测试端口（例如 `200GE1/0/4`）
3. 执行窗口内无生产流量
4. 每次测试前保存 baseline
5. 测试后验证已完全恢复

## 6. 常见风险与处理

1. 风险: IfIndex 解析失败
   - 处理: 先 `get_all_interfaces()`，按 `AbbreviatedName` 解析
2. 风险: edit-config 成功但状态未变化
   - 处理: 强制二次读取验证，不通过则判失败
3. 风险: 危险命令误执行
   - 处理: 依赖 `SafetyGuard.FORBIDDEN_PATTERNS`，并持续扩展规则

## 7. 给 AI 的执行指令模板

1. 先读取基线，不允许直接注入。
2. 所有 switch 变更必须经 `SwitchChannel`。
3. 每次注入后必须执行“状态验证 + 回滚验证”。
4. 如果验证失败，立即返回失败并保留错误上下文。
5. 不允许执行 `reload` / `reboot` / factory reset 类操作。
