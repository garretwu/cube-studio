# Fault Injector 完整测试计划

## 1. 测试目标

验证 `fault_injector` 在 RoCE MTU 不一致场景下具备以下能力：

- 正确读取统一配置文件并构造目标命令
- 注入前写入 WAL（满足 WAL-first）
- 按会话执行回滚并恢复网络配置
- 在 simulate/ssh 两种模式均可运行
- 出现异常后仍可执行人工或程序化回滚

## 2. 测试范围

### 2.1 包含范围

- `fault_injector` CLI：`inject-roce-mtu-mismatch`、`rollback`
- 配置加载逻辑
- `channel/ssh.py` simulate 与 ssh 执行路径
- WAL 文件记录、读取、清理
- 本地与远端命令级验证

### 2.2 不包含范围

- Redfish、交换机 API 场景
- 多场景并行注入
- 压测联动（load simulator）

## 3. 测试环境

### 3.1 本地环境（必做）

- Python 3.10+
- 仓库代码与可写目录
- 无需真实服务器

### 3.2 远端环境（可选但建议）

- 至少 2 台测试服务器（非生产）
- SSH 可达，执行用户具备 sudo 权限
- 可执行 `ip link set dev <iface> mtu <value>`

## 4. 测试矩阵

| 维度 | 用例 | 目标 |
|---|---|---|
| 单元 | `python -m unittest fault_injector.tests.test_roce_mtu_mismatch` | 验证注入/回滚行为和模拟状态 |
| 模拟 E2E | inject + rollback | 验证 CLI 流程、WAL 流程 |
| 远端 E2E | ssh inject + rollback | 验证真实网络命令执行 |
| 失败恢复 | 注入后中断再 rollback | 验证可恢复性 |
| 配置校验 | 错误 host/interface/权限 | 验证错误可见性与处理流程 |

## 5. 通过标准（Exit Criteria）

满足以下条件视为测试通过：

1. 单元测试全部通过
2. simulate 模式注入与回滚命令全部成功
3. ssh 模式至少完成一次完整注入/回滚闭环
4. 回滚后目标网卡 MTU 与 `original_mtu` 一致
5. 失败演练中可通过 rollback 清理注入影响

## 6. 风险与缓解

| 风险 | 影响 | 缓解方案 |
|---|---|---|
| 远端 sudo 无权限 | 命令失败，无法注入/回滚 | 提前验证 sudo 免密策略 |
| 网卡名错误 | 对应主机无法变更 MTU | 先执行 `ip link show` 确认 |
| 在生产环境误执行 | 业务抖动风险 | 强制仅测试网段和测试机 |
| 回滚遗漏 | 故障残留 | 执行后逐机校验 MTU + 记录 |

## 7. 测试交付物

- 测试执行记录（命令、输出、时间）
- 失败案例与修复建议
- 最终通过结论（可上线/需整改）

