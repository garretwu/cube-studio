# AIDC-auto-SRE 设计文档评审

## 总体结论
该设计文档覆盖面完整（诊断、修复、拓扑、知识、记忆、对话、GUI、安全），整体方向“自由诊断 + 受控修复”是合理的，且对 SRE 场景的分层较清晰。

但当前文档中的多个代码片段和接口契约存在**可落地性缺陷**与**安全高风险点**。若按当前方案直接实现，较大概率在生产环境出现安全事故、执行失败或行为偏差。建议先完成高优先级整改（见 P0/P1）再进入开发。

## 架构 soundness 评估
- 优点
  - 将诊断（LLM）与执行（确定性引擎）解耦，原则正确。`AIDC-auto-SRE.md:11` `AIDC-auto-SRE.md:16` `AIDC-auto-SRE.md:50`
  - 修复链路引入审批、灰度、WAL，有较好的操作安全意识。`AIDC-auto-SRE.md:63` `AIDC-auto-SRE.md:1489` `AIDC-auto-SRE.md:1552`
  - 拓扑/知识/记忆三类上下文增强思路完整，有助于降低盲诊。`AIDC-auto-SRE.md:286` `AIDC-auto-SRE.md:1629` `AIDC-auto-SRE.md:1795`
- 主要问题
  - “架构不变量”与示例实现不一致，存在落地断层（如 timeout、模型字段、审批机制、返回类型）。
  - 安全模型在“概念层”较完整，但在“代码层”有明显绕过点（`eval`、命令拼接、缺少鉴权）。
  - 多个核心模块缺少并发一致性和故障恢复细节，影响生产可用性。

## 关键问题（按严重度）

### P0（必须先修）
1. 远程代码执行风险：验证逻辑使用 `eval`
- 位置：`AIDC-auto-SRE.md:1615` `AIDC-auto-SRE.md:1619`
- 问题：`success_condition` 来源于计划配置/模型输出，直接 `eval` 可执行任意 Python 表达式。
- 影响：执行主机被接管、越权访问、数据破坏。
- 建议：改为受限表达式引擎（AST 白名单）或 DSL（如 `metric.lt(500)`）。

2. 命令注入风险：SSH 命令字符串拼接未转义
- 位置：`AIDC-auto-SRE.md:1262` `AIDC-auto-SRE.md:1270`
- 问题：`log_path`、`filter_str` 直接拼接 shell 命令。
- 影响：可执行任意命令（特别是在 read-only 工具链中形成“读工具写执行”绕过）。
- 建议：参数白名单 + `shlex.quote` + 禁止任意路径/管道字符。

3. API/WebSocket 缺少鉴权与 RBAC 落地
- 位置：`AIDC-auto-SRE.md:2633` `AIDC-auto-SRE.md:2697` `AIDC-auto-SRE.md:2877`
- 问题：接口示例未体现任何认证、授权、租户隔离；但文档声明有角色权限。
- 影响：未授权触发诊断/审批/回滚。
- 建议：统一依赖注入鉴权中间件，接口级权限注解，审计绑定用户身份。

4. 审批逻辑示例不可运行
- 位置：`AIDC-auto-SRE.md:1544`
- 问题：`asyncio.Queue.get()` 不支持 `timeout` 参数。
- 影响：审批等待逻辑失效，修复流程卡死或抛错。
- 建议：`await asyncio.wait_for(queue.get(), timeout=300)`。

### P1（高优先级）
1. 数据模型与返回值契约不一致
- 位置：`AIDC-auto-SRE.md:1430` `AIDC-auto-SRE.md:1469` `AIDC-auto-SRE.md:1482` `AIDC-auto-SRE.md:1572`
- 问题：`RemediationResult` 必填字段较多，但多个返回示例仅填少数字段，且示例使用了未定义字段 `reason`。
- 影响：运行时校验失败，API 响应不稳定。
- 建议：统一 Result schema（错误字段、可选字段）并修正所有返回路径。

2. 修复引擎依赖未注入
- 位置：`AIDC-auto-SRE.md:1560` `AIDC-auto-SRE.md:1614`
- 问题：`_verify` 使用 `self.prometheus`，但构造函数未注入该对象。
- 影响：验证阶段 `AttributeError`，触发误回滚。
- 建议：在构造函数显式注入 Prometheus client，并加启动时依赖校验。

3. ReAct timeout 设计未实现
- 位置：`AIDC-auto-SRE.md:2425` `AIDC-auto-SRE.md:2426` `AIDC-auto-SRE.md:662`
- 问题：配置声明单步/总超时，但主循环未用 `wait_for`/deadline。
- 影响：长尾调用导致会话悬挂。
- 建议：步级和会话级 deadline 双重控制。

4. 发现流程吞异常导致“部分失败无感知”
- 位置：`AIDC-auto-SRE.md:536` `AIDC-auto-SRE.md:541`
- 问题：`gather(return_exceptions=True)` 后未检查结果。
- 影响：拓扑缺失但系统误认为成功。
- 建议：聚合错误、标注数据新鲜度与完整性，必要时 fail-fast。

5. 对话工具调用循环不完整
- 位置：`AIDC-auto-SRE.md:2929` `AIDC-auto-SRE.md:2935`
- 问题：收到 `tool_call` 后仅执行工具并写历史，未重新向 LLM 续推理。
- 影响：多步工具链中断，回答不完整。
- 建议：实现标准 function-calling loop（tool result -> model -> next action）。

### P2（中优先级）
1. 线程/并发安全缺失（SQLite + NetworkX）
- 位置：`AIDC-auto-SRE.md:453` `AIDC-auto-SRE.md:1854`
- 问题：在 async/多任务场景下直接共享连接与内存图，无锁无事务策略。
- 影响：竞争条件、数据损坏、偶发崩溃。
- 建议：引入 async DB 层、读写锁、事务边界和恢复策略。

2. 观测数据与思考轨迹可能泄露敏感信息
- 位置：`AIDC-auto-SRE.md:760` `AIDC-auto-SRE.md:807`
- 问题：完整思考文本 + 工具结果实时推送，未定义脱敏规则。
- 影响：泄露密钥、主机信息、内部路径。
- 建议：trace 分级展示，默认脱敏，原文仅限高权限审计。

3. 默认安全配置偏弱
- 位置：`AIDC-auto-SRE.md:2439`
- 问题：`verify_ssl: false`。
- 影响：中间人攻击风险。
- 建议：默认开启 TLS 校验，禁用不安全默认值。

4. 知识库文档 ID 仅按内容哈希截断，冲突与覆盖风险
- 位置：`AIDC-auto-SRE.md:1750`
- 问题：不同来源相同文本会碰撞，版本不可追踪。
- 影响：检索结果漂移、可追溯性差。
- 建议：ID 增加 `source/category/chunk_idx/version` 维度。

## Code quality 评估
- 优点
  - 类型标注充分，模块边界清晰。
  - 通过 Pydantic 建模整体一致性较好。`AIDC-auto-SRE.md:2537`
- 问题
  - 示例代码存在多处“伪可运行”问题（未定义类型、未注入依赖、返回模型不匹配）。
  - 错误处理策略不统一（有的吞异常、有的直接抛出、有的 `pass` 占位）。`AIDC-auto-SRE.md:1295` `AIDC-auto-SRE.md:1344`
  - 多处占位符 `...`、`pass` 在核心路径，需明确 MVP 边界避免误解为已设计完成。

## 潜在 bug 列表
- `K8sNode` 在实体定义中未出现，`discover_k8s` 中直接使用。`AIDC-auto-SRE.md:598`
- `discover_switches` 仅加关系，不保证端口实体存在。`AIDC-auto-SRE.md:585`
- `get_entity` 对不存在 ID 会抛 `KeyError`，无防护。`AIDC-auto-SRE.md:468`
- `get_path` 未处理节点不存在异常（仅捕获 `NoPath`）。`AIDC-auto-SRE.md:516`
- 记忆记录依赖 `session.outcome`/`duration_seconds`，但诊断阶段可能未填。`AIDC-auto-SRE.md:1876` `AIDC-auto-SRE.md:1877`
- `ingest_markdown` 末尾再次 chunk 全量 section，重复计算且参数不一致。`AIDC-auto-SRE.md:1690`

## 缺失组件
- 鉴权与会话管理的具体实现（JWT 验签、token 轮换、租户隔离）。
- 幂等机制（重复告警、重复审批、重复修复请求去重）。
- 任务编排与状态机（诊断/审批/执行/回滚的可恢复状态转换定义）。
- SLA/SLO 级别的系统自监控（队列长度、工具超时率、诊断成功率）。
- 灾备策略（WAL 损坏、数据库损坏、部分回滚失败处理）。

## 安全评估
- 已有基础：分级工具、禁止操作、审批门控、审计日志框架。`AIDC-auto-SRE.md:957` `AIDC-auto-SRE.md:2844` `AIDC-auto-SRE.md:2859`
- 主要短板：
  - 输入执行链未充分净化（shell/eval）。
  - 接口缺少统一认证与权限校验。
  - 审计日志未定义防篡改（签名、远端归档、不可抵赖）。
  - Secret 管理未明确（配置中出现用户名与密码占位，缺轮换机制）。

## 性能评估
- 风险点
  - `get_blast_radius` 使用 `list.pop(0)`，大图上退化。`AIDC-auto-SRE.md:500`
  - LogChannel 跨 Pod 顺序拉取日志，缺并发与限流。`AIDC-auto-SRE.md:1252`
  - 向量检索与 SQLite 混用未定义连接池策略，可能在高并发下阻塞。
  - ThinkingTrace/WebSocket 队列无背压策略，易内存膨胀。`AIDC-auto-SRE.md:814`
- 建议
  - BFS 用 `collections.deque`。
  - 工具调用引入并发控制（semaphore）和批处理。
  - 建立缓存层（拓扑摘要、热点知识检索）。
  - 给 trace 推送增加限速与截断策略。

## 改进建议（实施顺序）
1. 安全兜底优先：移除 `eval`、修复 shell 注入、给全部 API/WS 增加鉴权和 RBAC。
2. 契约收敛：统一 `RemediationResult`、`DiagnosisSession`、错误模型；补全所有返回路径。
3. 流程可靠性：补齐 timeout、重试、幂等键、状态机持久化（可 resume）。
4. 并发与数据一致性：重构 Ontology/Memory 存储访问层，定义锁与事务边界。
5. 可观测性：增加端到端指标（诊断步数、工具失败率、回滚率、审批时延）。
6. 文档治理：将“示例代码”标注为伪代码或补齐为可运行 reference implementation。

## 建议的验收门槛（进入开发前）
- 安全门槛：通过命令注入、表达式注入、未授权访问三类测试。
- 一致性门槛：核心模型 schema 与 API 返回全部通过契约测试。
- 稳定性门槛：1000 次模拟告警回放无死锁、无未恢复会话。
- 回滚门槛：注入 10 类失败场景，WAL 回滚成功率 >= 99%。
