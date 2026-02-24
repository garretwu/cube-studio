# Fault Injector 测试用例清单

## TC-001 单元测试：注入/回滚状态流

- **目的**：验证本地模拟下命令生成和 MTU 状态恢复
- **前置条件**：代码可运行
- **步骤**：
  1. 执行 `python -m unittest fault_injector.tests.test_roce_mtu_mismatch`
- **预期结果**：
  - 测试通过（OK）
  - 用例覆盖注入命令和回滚后 MTU 恢复

---

## TC-002 模拟模式 E2E：注入

- **目的**：验证 CLI 注入命令和报告输出
- **前置条件**：`fault_injector/conf/fault_injector.conf.json` 中 `mode=simulate`
- **步骤**：
  1. 执行
     `python -m fault_injector --config fault_injector/conf/fault_injector.conf.json --session-id sim-001 inject-roce-mtu-mismatch`
- **预期结果**：
  - 返回 JSON 数组
  - 每个节点 `success=true`
  - 输出命令包含 `ip link set dev <iface> mtu <fault_mtu>`

---

## TC-003 模拟模式 E2E：回滚

- **目的**：验证 rollback 流程
- **前置条件**：先执行 TC-002
- **步骤**：
  1. 执行
     `python -m fault_injector --config fault_injector/conf/fault_injector.conf.json --session-id sim-001 rollback`
- **预期结果**：
  - 返回 JSON 数组
  - 每个节点 `success=true`
  - 输出命令包含 `mtu <original_mtu>`

---

## TC-004 远端模式 E2E：注入+回滚

- **目的**：验证真实服务器执行能力
- **前置条件**：
  - 使用 `Test/remote_config_template.conf.json` 填好服务器信息
  - 配置 `mode=ssh`
  - 用户具备 sudo 权限
- **步骤**：
  1. 注入：
     `python -m fault_injector --config <your-conf>.json --session-id real-001 inject-roce-mtu-mismatch`
  2. 逐机确认 MTU：
     `ssh <user>@<host> "ip link show <iface>"`
  3. 回滚：
     `python -m fault_injector --config <your-conf>.json --session-id real-001 rollback`
  4. 再次确认 MTU 恢复
- **预期结果**：
  - 步骤 1、3 全部成功
  - 步骤 2 中 MTU 与故障值一致
  - 步骤 4 中 MTU 恢复为原始值

---

## TC-005 异常恢复：注入后中断进程，再回滚

- **目的**：验证中断后仍可恢复
- **前置条件**：配置有效
- **步骤**：
  1. 执行注入命令
  2. 不做其他操作，直接执行 rollback（模拟“异常中断后恢复”）
- **预期结果**：
  - rollback 成功
  - MTU 恢复

---

## TC-006 错误配置验证：非法 host/interface

- **目的**：确认失败可见性
- **前置条件**：复制一份配置并写入错误 host 或 interface
- **步骤**：
  1. 执行注入命令
- **预期结果**：
  - 返回失败信息（stderr/returncode）
  - 不应影响其他正确主机（建议单机先测）

