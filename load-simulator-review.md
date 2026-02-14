# load-simulator 设计评审（Demo 优先，二次评审）

## Findings（按严重度）

1. **[P0] 未明确对 fault-injector 的机器可读联动接口，跨系统集成契约不闭合。**
- 证据：文档定义了人用 CLI（`load-simulator --config ...`）和文件产物（`report/report.json`），未定义 `run` 子命令与 `--output-format json` 的 stdout 协议（`load-simulator.md:1835`、`load-simulator.md:1850`、`load-simulator.md:1631`、`load-simulator.md:1643`）。
- 同时 `fault-injector` 已按 stdout JSON 方式设计联动（`fault-injector.md:1546`、`fault-injector.md:1549`）。
- 影响：Demo 联动时容易出现“命令可执行但结果不可解析”的失败。
- 建议（Demo 最小改动）：显式补一条联动接口规范
  1. 推荐：保留现有 CLI，不新增子命令；由调用方读取 `report/report.json` 作为标准输出结果；
  2. 若坚持 stdout JSON，则在文档和 CLI 同步定义 `--output-format json`。

## 本轮已确认修复项（相对上轮）
- 监控任务取消/收尾已补齐（`load-simulator.md:1903`、`load-simulator.md:1905`、`load-simulator.md:1908`）。
- CLI 参数已统一为 `--only`（`load-simulator.md:1579`、`load-simulator.md:1841`）。
- 认证示例已收敛为 JWT 且 secret 使用环境变量（`load-simulator.md:1979`、`load-simulator.md:1982`）。
- 阈值示例笔误已修正（`load-simulator.md:1956`）。

## Demo 结论
- 单体 Demo 已基本可用；若要做 fault+load 联动 Demo，需先补齐对外联动结果契约（本轮 P0）。

## 长期产品级优化（单独列出）
1. 对外集成接口版本化（CLI/API schema version + 兼容策略）。
2. 报告与事件流统一（stdout 摘要 + 文件全量 + 可追溯 session ID）。
3. 认证与权限继续细化（最小权限 token、过期轮换、审计字段）。
