# fault-injector 设计评审（Demo 优先，二次评审）

## Findings（按严重度）

1. **[P0] 与 load-simulator 的联动接口仍存在跨文档不一致，可能直接阻塞 Demo 联调。**
- 证据：`fault-injector` 假设通过子进程调用 `python -m load_simulator run --output-format json`，并从 stdout 解析 JSON 摘要（`fault-injector.md:1539`、`fault-injector.md:1546`、`fault-injector.md:1549`）。
- 但 `load-simulator` 文档仅定义 `load-simulator --config ...` 这类 CLI，没有 `run` 子命令或 `--output-format` 约定；结果产物以文件报告为主（`load-simulator.md:1835`、`load-simulator.md:1850`、`load-simulator.md:1631`、`load-simulator.md:1643`）。
- 影响：联动步骤在 Demo 现场可能“调用参数不兼容”或“拿不到 stdout JSON”，导致流程中断。
- 建议（Demo 最小改动）：二选一快速收敛
  1. 把 fault-injector 调用改成 `load-simulator --config ...`，完成后直接读取 `report/report.json`；
  2. 或在 load-simulator 明确支持 `run --output-format json` 并保证 stdout 机器可读。

2. **[P1] 失败策略文案存在歧义：契约写“失败即回滚”，配置却允许 `abort_only`。**
- 证据：联动契约固定“失败处理=触发 WAL 回滚”（`fault-injector.md:1537`），但配置示例允许 `on_failure: rollback_and_report | abort_only`（`fault-injector.md:1577`）。
- 影响：执行者可能选择 `abort_only`，与 Demo 的“可恢复”目标冲突。
- 建议：Demo 版先限制为 `rollback_and_report`，`abort_only` 放到长期项并附带严格前置条件。

3. **[P2] BMC 保留段落仍有可执行 PATCH 示例，和“Demo 不执行”语义略冲突。**
- 证据：同一段先声明“以下修改操作被硬编码拦截，Demo 不执行”，后面仍给出 `PATCH MTU` 示例（`fault-injector.md:851`、`fault-injector.md:857`）。
- 影响：实现/演示人员可能误解为可在 Demo 执行。
- 建议：将该段所有修改语句统一改为注释示例并加 `FUTURE ONLY` 标识。

## 本轮已确认修复项（相对上轮）
- BMC 网络修改已从 Demo 场景移除并标注冲突说明（`fault-injector.md:836`、`fault-injector.md:840`）。
- 已新增联动契约章节（`fault-injector.md:1528`）。
- 凭据示例已改为环境变量占位符（`fault-injector.md:2427`）。

## Demo 结论
- 当前可进入 Demo 实施，但需要先解决 **P0 联动接口一致性**，否则端到端联动不稳定。

## 长期产品级优化（单独列出）
1. 回滚机制增强为“状态快照 + ownership token + 幂等恢复”。
2. 联动协议标准化（统一命令/退出码/报告 schema/重试语义）。
3. 高风险网络与交换机变更加入分级审批和时间窗控制。
