# AIDC 平台设计文档综合评审

> 本文档合并了对 AIDC-auto-SRE、fault-injector、load-simulator 三份设计文档的评审结果，去重并统一严重度分级。

---

# 第一部分：AIDC-auto-SRE 设计评审

**评审范围**：`AIDC-auto-SRE.md` 全文（3057 行）
**评审标准**：Demo 可行性 + 生产安全底线

## 总体结论

设计文档覆盖面完整（诊断、修复、拓扑、知识、记忆、对话、GUI、安全），整体方向"自由诊断 + 受控修复"是合理的，对 SRE 场景的分层清晰。

但当前文档中的多个代码片段和接口契约存在**可落地性缺陷**与**安全高风险点**。若按当前方案直接实现，较大概率在 Demo 阶段出现流程断裂、在生产环境出现安全事故或行为偏差。建议先完成 P0/P1 整改再进入开发。

## 架构 soundness 评估

**优点**
- 将诊断（LLM ReAct）与执行（确定性引擎）解耦，原则正确。`AIDC-auto-SRE.md:11` `AIDC-auto-SRE.md:16` `AIDC-auto-SRE.md:50`
- 修复链路引入审批、灰度、WAL，有较好的操作安全意识。`AIDC-auto-SRE.md:63` `AIDC-auto-SRE.md:1489` `AIDC-auto-SRE.md:1552`
- 拓扑/知识/记忆三类上下文增强思路完整，有助于降低盲诊。`AIDC-auto-SRE.md:286` `AIDC-auto-SRE.md:1629` `AIDC-auto-SRE.md:1795`
- Demo 场景（11.1、11.2）的 10 步诊断 Trace 足够具体，可直接用于 Demo 脚本编写。
- Skills 框架采用 YAML 格式打包工具 + 提示词 + 示例，扩展性好。

**主要问题**
- "架构不变量"与示例实现不一致，存在落地断层（timeout、模型字段、审批机制、返回类型）。
- 安全模型在"概念层"较完整，但在"代码层"有明显绕过点（`eval`、命令拼接、缺少鉴权）。
- 多个核心模块缺少并发一致性和故障恢复细节，影响生产可用性。

---

## 关键问题（按严重度）

### P0 — 必须先修 / Demo 阻塞

**P0-1. 远程代码执行风险：验证逻辑使用 `eval()`**
- 位置：`AIDC-auto-SRE.md:1615` `AIDC-auto-SRE.md:1619`
- 问题：`success_condition` 来源于 `RemediationPlan`（由 LLM ReAct 循环生成，`AIDC-auto-SRE.md:711`），直接 `eval` 可执行任意 Python 表达式。
- 影响：LLM 幻觉或恶意 prompt injection 可通过 `success_condition` 字段执行任意代码，直接绕过所有安全层（工具级、Channel 级、WAL）。与 1.4 核心不变量"LLM 可以自由观测，但永远不直接执行写操作"冲突。
- 建议：替换为安全条件求值器（预定义比较操作符白名单）或 AST 白名单引擎：
  ```python
  # 白名单: "value < 500", "status == 'Running'", "error_rate < 0.01"
  OPERATORS = {"<": op.lt, ">": op.gt, "<=": op.le, ">=": op.ge, "==": op.eq}
  def safe_eval(condition: str, variables: dict) -> bool:
      # 解析 "field op literal" 三元组
      ...
  ```

**P0-2. 命令注入风险：SSH 命令字符串拼接未转义**
- 位置：`AIDC-auto-SRE.md:1262` `AIDC-auto-SRE.md:1270`
- 问题：`log_path`、`filter_str` 直接拼接 shell 命令（`f"tail -n {tail} {log_path}"`、`f"| grep -i '{filter_str}'"`）。
- 影响：LLM 构造恶意 `log_path`（如 `/dev/null; rm -rf /`）或 `filter_str` 时，SSH channel 会执行注入命令。虽然声明为 read_only 工具，但实际可执行写操作，形成"读工具写执行"绕过。
- 建议：参数白名单 + `shlex.quote()` + 禁止任意路径/管道字符；或改用 SSH exec 模式传递参数数组而非拼接字符串。

**P0-3. API/WebSocket 缺少鉴权与 RBAC 落地**
- 位置：`AIDC-auto-SRE.md:2633` `AIDC-auto-SRE.md:2697` `AIDC-auto-SRE.md:2877`
- 问题：接口示例未体现任何认证、授权、租户隔离；但文档声明有角色权限。
- 影响：未授权用户可触发诊断/审批/回滚，安全模型形同虚设。
- 建议：统一依赖注入鉴权中间件，接口级权限注解，审计绑定用户身份。

**P0-4. 审批逻辑示例不可运行**
- 位置：`AIDC-auto-SRE.md:1544`
- 问题：`asyncio.Queue.get()` 不支持 `timeout` 参数。
- 影响：审批等待逻辑失效，修复流程卡死或抛错。
- 建议：`await asyncio.wait_for(queue.get(), timeout=300)`。

**P0-5. Demo 1 关键步骤的工具调用与工具定义不匹配，Demo 无法跑通**
- 位置：Demo Trace `AIDC-auto-SRE.md:2143-2146`，工具定义 `AIDC-auto-SRE.md:1052`，实现 `AIDC-auto-SRE.md:1259-1263`
- 问题：Demo 1 Step 5 调用 `read_system_log(node="gpu-1-1", command="nvidia-smi --query-compute-apps=...")`，但 `read_system_log` 的参数为 `node: str, log_path: str, tail: int?`，无 `command` 参数。实现也是 `tail -n {tail} {log_path}`，不支持任意命令。
- 影响：Demo 1 的核心诊断步骤（发现 gpu-burn 进程）无法执行，诊断流程断裂。
- 建议（Demo 最小改动）：新增 `get_gpu_processes` 只读工具，专门调用 `nvidia-smi --query-compute-apps`，避免开放任意命令执行：
  ```
  | get_gpu_processes | node: str | {processes: list[{pid, name, gpu_uuid, memory_mb}]} | SSH |
  ```

---

### P1 — 高优先级（不阻塞 Demo 但影响正确性/安全性）

**P1-1. `get_blast_radius()` BFS 方向错误，影响范围计算不完整**
- 位置：`AIDC-auto-SRE.md:506-510`（BFS 只跟踪 `out_edges`），`AIDC-auto-SRE.md:427-428`（关系方向定义）
- 问题：BFS 只遍历 `self.graph.out_edges(current)`，只跟踪 `SERVES`、`DEPENDS_ON`、`HOSTED_ON`。但关系方向定义为：
  - `HOSTED_ON`：Pod → Node（子 → 父）
  - `SERVES`：InferenceService → Pod
  因此，当 Node 故障时，通过 Node 的 `out_edges` 无法找到 hosted_on 它的 Pods（边方向是 Pod→Node）。需沿 `in_edges` 反向追踪。
- 影响：`_build_initial_context` 调用 `get_blast_radius(entity_id)` 构建诊断上下文（`AIDC-auto-SRE.md:726`），影响范围不完整导致 Agent 缺少关键拓扑信息。
- 建议：BFS 应同时遍历 out_edges 和 in_edges，根据关系类型决定遍历方向：
  ```python
  # 故障向上传播（影响依赖者）：沿 SERVES/DEPENDS_ON 的 in_edges
  # 故障向下定位（找到宿主）：沿 HOSTED_ON/PART_OF 的 out_edges
  ```

**P1-2. 数据模型与返回值契约不一致**
- 位置：`AIDC-auto-SRE.md:1430` `AIDC-auto-SRE.md:1469` `AIDC-auto-SRE.md:1482` `AIDC-auto-SRE.md:1572`
- 问题：`RemediationResult` 必填字段较多，但多个返回示例仅填少数字段，且示例使用了未定义字段 `reason`。
- 影响：运行时校验失败，API 响应不稳定。
- 建议：统一 Result schema（错误字段、可选字段）并修正所有返回路径。

**P1-3. Alert 模型混用 `@dataclass` 和 Pydantic，API 不兼容**
- 位置：Alert 定义 `AIDC-auto-SRE.md:1309`（`@dataclass`），使用处 `AIDC-auto-SRE.md:1289`（`Alert.model_validate(a)`），引用处 `AIDC-auto-SRE.md:1811`（`IncidentRecord(BaseModel)` 中 `alert: Alert`）
- 问题：`@dataclass` 没有 `model_validate` 方法；作为 Pydantic `BaseModel` 字段时也需要是 `BaseModel`。
- 建议：统一 Alert 为 `BaseModel`。

**P1-4. 修复引擎依赖未注入**
- 位置：`AIDC-auto-SRE.md:1560` `AIDC-auto-SRE.md:1614`
- 问题：`_verify` 使用 `self.prometheus`，但构造函数未注入该对象。
- 影响：验证阶段 `AttributeError`，触发误回滚。
- 建议：在构造函数显式注入 Prometheus client，并加启动时依赖校验。

**P1-5. LLM 在诊断阶段看不到 write 工具 schema，无法生成正确的 RemediationPlan**
- 位置：`AIDC-auto-SRE.md:668`（`get_tool_schemas(safety_level="read_only")` 只返回 read_only 工具），`AIDC-auto-SRE.md:710-715`（`remediate` 动作需包含具体 write 工具名和参数）
- 问题：LLM 不知道有哪些修复工具可用，可能生成不存在的工具名或错误的参数格式。
- 建议：在系统提示词中附加 write 工具的描述列表（只读参考，不注册为可调用工具），或增加 `"write_descriptions_only"` 模式。

**P1-6. ReAct timeout 设计未实现**
- 位置：`AIDC-auto-SRE.md:2425` `AIDC-auto-SRE.md:2426` `AIDC-auto-SRE.md:662`
- 问题：配置声明单步/总超时，但主循环未用 `wait_for`/deadline。
- 影响：长尾调用导致会话悬挂。
- 建议：步级和会话级 deadline 双重控制。

**P1-7. 发现流程吞异常导致"部分失败无感知"**
- 位置：`AIDC-auto-SRE.md:536` `AIDC-auto-SRE.md:541`
- 问题：`gather(return_exceptions=True)` 后未检查结果。
- 影响：拓扑缺失但系统误认为成功。
- 建议：聚合错误、标注数据新鲜度与完整性，必要时 fail-fast。

**P1-8. 对话工具调用循环不完整**
- 位置：`AIDC-auto-SRE.md:2929` `AIDC-auto-SRE.md:2935`
- 问题：收到 `tool_call` 后仅执行工具并写历史，未重新向 LLM 续推理。
- 影响：多步工具链中断，回答不完整。
- 建议：实现标准 function-calling loop（tool result → model → next action）。

**P1-9. 模式记忆的置信度更新策略不区分修复成功/失败**
- 位置：`AIDC-auto-SRE.md:1916`
- 问题：无论 `record.outcome` 是 `"resolved"` 还是 `"failed"`，置信度都 +0.1。
- 影响：反复出现但每次修复失败的误判模式，7 次后置信度达 1.0，被优先推荐给 Agent，导致诊断偏差。
- 建议：
  ```python
  if record.outcome == "resolved":
      existing.confidence = min(1.0, existing.confidence + 0.15)
  elif record.outcome == "failed":
      existing.confidence = max(0.0, existing.confidence - 0.1)
  ```

**P1-10. OntologyGraph、KnowledgeStore、MemoryStore 使用同步库但声明为 async，阻塞事件循环**
- 位置：`AIDC-auto-SRE.md:453`（`sqlite3.connect()`），`AIDC-auto-SRE.md:1762`（`async` 方法调同步 `self.collection.query()`），`AIDC-auto-SRE.md:1854-1860`
- 影响：每次知识检索或记忆查询阻塞整个 asyncio event loop，导致 WebSocket 推送延迟、并发诊断挂起。
- 建议：`sqlite3` 替换为 `aiosqlite`；ChromaDB 同步调用包裹在 `asyncio.to_thread()` 中。

---

### P2 — 中优先级（不影响 Demo）

**P2-1. 线程/并发安全缺失（SQLite + NetworkX）**
- 位置：`AIDC-auto-SRE.md:453` `AIDC-auto-SRE.md:1854`
- 问题：在 async/多任务场景下直接共享连接与内存图，无锁无事务策略。
- 影响：竞争条件、数据损坏、偶发崩溃。
- 建议：引入 async DB 层、读写锁、事务边界和恢复策略。

**P2-2. 观测数据与思考轨迹可能泄露敏感信息**
- 位置：`AIDC-auto-SRE.md:760` `AIDC-auto-SRE.md:807`
- 问题：完整思考文本 + 工具结果实时推送，未定义脱敏规则。
- 影响：泄露密钥、主机信息、内部路径。
- 建议：trace 分级展示，默认脱敏，原文仅限高权限审计。

**P2-3. 默认安全配置偏弱**
- 位置：`AIDC-auto-SRE.md:2439`
- 问题：`verify_ssl: false`。
- 建议：默认开启 TLS 校验，禁用不安全默认值。

**P2-4. 知识库文档 ID 仅按内容哈希截断，冲突与覆盖风险**
- 位置：`AIDC-auto-SRE.md:1750`
- 问题：不同来源相同文本会碰撞，版本不可追踪。
- 建议：ID 增加 `source/category/chunk_idx/version` 维度。

**P2-5. `DiscoveryAgent.infer_topology()` 是空实现（`pass`）**
- 位置：`AIDC-auto-SRE.md:610-616`
- 影响：BMC 发现的 Node 和 K8s 发现的 Pod 之间不会自动建立 HOSTED_ON 关系，拓扑图形同虚设。Demo 中需手动确保拓扑完整。
- 建议：至少实现 hostname 匹配逻辑（K8s node name == BMC hostname → HOSTED_ON），Demo 可用。

**P2-6. ThinkingStep 和 Observation 缺少 `to_dict()` 方法但被 WebSocket 推送引用**
- 位置：`AIDC-auto-SRE.md:822` 调用 `step.to_dict()`，但 `ThinkingStep`（`AIDC-auto-SRE.md:756-764`）和 `Observation`（`AIDC-auto-SRE.md:767-772`）为 `@dataclass`，无此方法。
- 建议：使用 `dataclasses.asdict()` 或显式实现 `to_dict()`。

**P2-7. ConversationalAgent 对话历史无限增长**
- 位置：`AIDC-auto-SRE.md:2905`
- 问题：`self.conversations: dict[str, list[dict]] = {}`，只有 `append` 无 trim。
- 影响：长时间运行后内存泄漏，LLM context window 溢出。
- 建议：设置 max_history（如 50 轮），超出后截断或摘要化。

**P2-8. 无 LLM 调用速率限制或成本控制**
- 问题：ReAct 循环最多 20 步，每步一次 LLM 调用。多告警同时触发多个诊断会话时，API 成本和速率失控。
- 建议：增加全局并发诊断数限制（配置已有 `max_concurrent_remediations`，缺少 `max_concurrent_diagnoses`）。

**P2-9. `CanaryConfig.success_criteria` 使用字符串表达式，解析方式未定义**
- 位置：`AIDC-auto-SRE.md:1415` `AIDC-auto-SRE.md:1477`
- 问题：`_check_criterion(criterion)` 方法未给出实现。
- 建议：与 P0-1 的 `eval()` 问题统一解决，定义条件表达式 DSL 或结构化 VerificationConfig。

**P2-10. 配置中 `kubeconfig` 出现两处，可能冲突**
- 位置：`AIDC-auto-SRE.md:2445`（`channels.kubernetes.kubeconfig`）和 `AIDC-auto-SRE.md:2477`（`ontology.discovery.k8s_clusters.main.kubeconfig`）
- 建议：discovery 中引用 channels 的配置，避免重复维护。

---

## Code Quality 评估

**优点**
- 类型标注充分，模块边界清晰。
- 通过 Pydantic 建模整体一致性较好。`AIDC-auto-SRE.md:2537`

**问题**
- 示例代码存在多处"伪可运行"问题（未定义类型、未注入依赖、返回模型不匹配）。
- 错误处理策略不统一（有的吞异常、有的直接抛出、有的 `pass` 占位）。`AIDC-auto-SRE.md:1295` `AIDC-auto-SRE.md:1344`
- 多处占位符 `...`、`pass` 在核心路径，需明确 MVP 边界避免误解为已设计完成。

## 潜在 Bug 列表

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

**已有基础**：分级工具、禁止操作、审批门控、审计日志框架。`AIDC-auto-SRE.md:957` `AIDC-auto-SRE.md:2844` `AIDC-auto-SRE.md:2859`

**主要短板**：
- 输入执行链未充分净化（shell/eval）。
- 接口缺少统一认证与权限校验。
- 审计日志未定义防篡改（签名、远端归档、不可抵赖）。
- Secret 管理未明确（配置中出现用户名与密码占位，缺轮换机制）。

## 性能评估

**风险点**
- `get_blast_radius` 使用 `list.pop(0)`（O(n) 退化）。`AIDC-auto-SRE.md:500`
- LogChannel 跨 Pod 顺序拉取日志，缺并发与限流。`AIDC-auto-SRE.md:1252`
- 向量检索与 SQLite 混用未定义连接池策略，高并发下阻塞。
- ThinkingTrace/WebSocket 队列无背压策略，易内存膨胀。`AIDC-auto-SRE.md:814`

**建议**
- BFS 用 `collections.deque`。
- 工具调用引入并发控制（semaphore）和批处理。
- 建立缓存层（拓扑摘要、热点知识检索）。
- 给 trace 推送增加限速与截断策略。

---

# 第二部分：fault-injector 设计评审（二次评审）

**P0-1. 与 load-simulator 的联动接口跨文档不一致，可能阻塞 Demo 联调**
- 证据：`fault-injector` 假设通过子进程调用 `python -m load_simulator run --output-format json`，并从 stdout 解析 JSON 摘要（`fault-injector.md:1539`、`fault-injector.md:1546`、`fault-injector.md:1549`）。但 `load-simulator` 文档仅定义 `load-simulator --config ...`，没有 `run` 子命令或 `--output-format` 约定。
- 建议（二选一）：
  1. fault-injector 调用改成 `load-simulator --config ...`，完成后读取 `report/report.json`；
  2. 或在 load-simulator 显式支持 `run --output-format json`。

**P1-1. 失败策略文案歧义：契约写"失败即回滚"，配置却允许 `abort_only`**
- 证据：联动契约固定"失败处理=触发 WAL 回滚"（`fault-injector.md:1537`），但配置示例允许 `on_failure: rollback_and_report | abort_only`（`fault-injector.md:1577`）。
- 建议：Demo 版先限制为 `rollback_and_report`，`abort_only` 放到长期项并附带严格前置条件。

**P2-1. BMC 保留段落仍有可执行 PATCH 示例，与"Demo 不执行"语义冲突**
- 证据：段落先声明"以下修改操作被硬编码拦截，Demo 不执行"，后面仍给出 `PATCH MTU` 示例（`fault-injector.md:851`、`fault-injector.md:857`）。
- 建议：将修改语句统一改为注释示例并加 `FUTURE ONLY` 标识。

**本轮已确认修复项**：BMC 网络修改已从 Demo 移除、已新增联动契约章节、凭据已改为环境变量占位符。

---

# 第三部分：load-simulator 设计评审（二次评审）

**P0-1. 未明确对 fault-injector 的机器可读联动接口，跨系统集成契约不闭合**
- 证据：文档定义了人用 CLI（`load-simulator --config ...`）和文件产物（`report/report.json`），未定义 `run` 子命令与 `--output-format json` 的 stdout 协议。`fault-injector` 已按 stdout JSON 方式设计联动。
- 建议：显式补一条联动接口规范——推荐保留现有 CLI，由调用方读取 `report/report.json` 作为标准输出结果。

**本轮已确认修复项**：监控任务取消/收尾已补齐、CLI 参数已统一为 `--only`、认证示例已收敛为 JWT + 环境变量、阈值笔误已修正。

---

# 第四部分：跨组件问题

**1. fault-injector ↔ load-simulator 联动接口不一致（P0）**
- 这是两份文档中各自发现的同一个问题的两面。需在两端同时收敛，推荐统一为"调用方读取 `report/report.json`"方案。

**2. 安全模式一致性**
- 三个组件在凭据管理、审批机制、WAL 回滚上风格基本一致（好），但鉴权落地程度不一（auto-SRE 最弱，需优先补齐）。

**3. 条件表达式求值**
- auto-SRE 的 `eval()` 和 canary `success_criteria` 解析、fault-injector 的验证判定应统一使用一套安全的条件表达式 DSL，避免各自实现。

---

# 第五部分：改进建议与验收门槛

## 改进建议（实施顺序）

1. **安全兜底优先**：移除 `eval`、修复 shell 注入、给全部 API/WS 增加鉴权和 RBAC。
2. **联动契约收敛**：统一 fault-injector ↔ load-simulator 接口协议；统一 auto-SRE 的 `RemediationResult`、`DiagnosisSession`、错误模型。
3. **Demo 可行性修复**：补齐 `get_gpu_processes` 工具或修正 Demo Trace；修复 Alert model_validate 兼容性；实现 `infer_topology` 最小逻辑。
4. **流程可靠性**：补齐 timeout、重试、幂等键、状态机持久化（可 resume）。
5. **并发与数据一致性**：重构 Ontology/Memory 存储访问层，定义锁与事务边界。
6. **可观测性**：增加端到端指标（诊断步数、工具失败率、回滚率、审批时延）。
7. **文档治理**：将"示例代码"标注为伪代码或补齐为可运行 reference implementation。

## 建议的验收门槛（进入开发前）

- **安全门槛**：通过命令注入、表达式注入、未授权访问三类测试。
- **一致性门槛**：核心模型 schema 与 API 返回全部通过契约测试。
- **联动门槛**：fault-injector → load-simulator 端到端调用成功（CLI 参数 + 结果解析 + 错误处理）。
- **稳定性门槛**：1000 次模拟告警回放无死锁、无未恢复会话。
- **回滚门槛**：注入 10 类失败场景，WAL 回滚成功率 >= 99%。

## 长期产品级优化

1. **Ontology 实时同步**：K8s Informer watch 替代定时全量刷新，保持 Pod 级别的实时拓扑。
2. **多 LLM 路由**：按诊断复杂度和成本自动选择 LLM（简单告警用轻量模型，复杂根因用强模型）。
3. **可观测性**：Agent 自身的 SLI/SLO（诊断成功率、MTTR、LLM 调用耗时/成功率）。
4. **联邦记忆**：多 AIDC 间共享匿名化模式记忆（A 站学到的模式可加速 B 站诊断）。
5. **Runbook 自动生成**：从高置信度 LearnedPattern 自动生成 Runbook YAML，形成知识闭环。
6. **联动协议标准化**：统一命令/退出码/报告 schema/重试语义，接口版本化。
7. **回滚增强**：状态快照 + ownership token + 幂等恢复。
