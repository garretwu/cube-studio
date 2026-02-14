# AIDC Auto SRE Agent 设计评审（Demo 优先）

**评审角色**：Senior SRE Platform Architect（分布式系统 + LLM Agent 安全）
**评审范围**：`AIDC-auto-SRE.md` 全文（3057 行）
**评审标准**：Demo 可行性优先，兼顾生产安全底线

---

## Findings（按严重度）

### P0 — Demo 阻塞项

1. **[P0] Demo 1 关键步骤的工具调用与工具定义不匹配，Demo 无法跑通。**
- 证据：Demo 1 Step 5（`AIDC-auto-SRE.md:2143-2146`）调用：
  ```
  read_system_log(node="gpu-1-1",
      command="nvidia-smi --query-compute-apps=...")
  ```
  但 `read_system_log` 的工具定义（`AIDC-auto-SRE.md:1052`）参数为 `node: str, log_path: str, tail: int?`，无 `command` 参数。
  LogChannel 实现（`AIDC-auto-SRE.md:1259-1263`）也是 `tail -n {tail} {log_path}`，不支持任意命令。
- 影响：Demo 1 的核心诊断步骤（发现 gpu-burn 进程）无法执行，诊断流程断裂。
- 建议（Demo 最小改动）：新增 `get_gpu_processes` 只读工具，专门调用 `nvidia-smi --query-compute-apps`，避免开放任意命令执行：
  ```
  | get_gpu_processes | node: str | {processes: list[{pid, name, gpu_uuid, memory_mb}]} | SSH |
  ```

2. **[P0] 修复引擎的 `_verify()` 使用 `eval()` 执行 LLM 生成的字符串，存在任意代码执行风险，与核心不变量冲突。**
- 证据：`AIDC-auto-SRE.md:1615` 和 `AIDC-auto-SRE.md:1619`：
  ```python
  return eval(config.success_condition, {"value": value})
  return eval(config.success_condition, {"result": result})
  ```
  `success_condition` 来自 `RemediationPlan`，而 `RemediationPlan` 由 LLM 的 ReAct 循环生成（`AIDC-auto-SRE.md:711`）。
- 影响：LLM 幻觉或恶意 prompt injection 可通过 success_condition 字段执行任意 Python 代码，直接绕过所有安全层（工具级、Channel 级、WAL）。这与 1.4 核心不变量 "LLM 可以自由观测，但永远不直接执行写操作" 冲突。
- 建议：替换 `eval()` 为安全的条件求值器（预定义比较操作符白名单）：
  ```python
  # 白名单: "value < 500", "status == 'Running'", "error_rate < 0.01"
  OPERATORS = {"<": op.lt, ">": op.gt, "<=": op.le, ">=": op.ge, "==": op.eq}
  def safe_eval(condition: str, variables: dict) -> bool:
      # 解析 "field op literal" 三元组
      ...
  ```

---

### P1 — 重要问题（不阻塞 Demo 但影响正确性/安全性）

3. **[P1] `get_blast_radius()` 仅遍历 out_edges，关系方向导致影响范围计算不完整。**
- 证据：`AIDC-auto-SRE.md:506-510`，BFS 只跟踪 `self.graph.out_edges(current)`，且只跟踪 `SERVES`、`DEPENDS_ON`、`HOSTED_ON`。
- 但关系方向定义为（`AIDC-auto-SRE.md:427-428`）：
  - `HOSTED_ON`：Pod → Node（子 → 父）
  - `SERVES`：InferenceService → Pod
- 因此，当 Node 故障时，通过 Node 的 out_edges 无法找到 hosted_on 它的 Pods。需要沿 in_edges 反向追踪 `HOSTED_ON` 才能找到受影响的 Pod。
- 影响：Demo 场景中 `_build_initial_context` 调用 `get_blast_radius(entity_id)` 来构建诊断上下文（`AIDC-auto-SRE.md:726`）。如果影响范围不完整，Agent 可能缺少关键拓扑信息。
- 建议：BFS 应同时遍历 out_edges 和 in_edges，根据关系类型决定遍历方向。参考做法：
  ```python
  # 故障向上传播（影响依赖者）：沿 SERVES/DEPENDS_ON 的 in_edges
  # 故障向下定位（找到宿主）：沿 HOSTED_ON/PART_OF 的 out_edges
  ```

4. **[P1] Alert 模型混用 `@dataclass` 和 Pydantic，API 不兼容。**
- 证据：Alert 在 `AIDC-auto-SRE.md:1309` 定义为 `@dataclass`，但在 `AIDC-auto-SRE.md:1289` 使用 `Alert.model_validate(a)`，这是 Pydantic v2 的方法。`@dataclass` 没有 `model_validate`。
- 同时，IncidentRecord（`AIDC-auto-SRE.md:1811`）引用 `alert: Alert` 作为 Pydantic BaseModel 的字段，要求 Alert 也是 BaseModel。
- 建议：统一 Alert 为 `BaseModel`。

5. **[P1] OntologyGraph、KnowledgeStore、MemoryStore 使用同步库（sqlite3、ChromaDB）但声明为 async，会阻塞事件循环。**
- 证据：
  - `OntologyGraph.__init__` 使用 `sqlite3.connect()`（`AIDC-auto-SRE.md:453`）
  - `KnowledgeStore` 的 `search()` 方法声明为 `async` 但调用同步的 `self.collection.query()`（`AIDC-auto-SRE.md:1762`）
  - `MemoryStore` 同理（`AIDC-auto-SRE.md:1854-1860`）
- 影响：在 ReAct 循环中，每次知识检索或记忆查询都会阻塞整个 asyncio event loop，导致 WebSocket 推送延迟、并发诊断挂起。
- 建议（Demo 最小改动）：`sqlite3` 替换为 `aiosqlite`；ChromaDB 同步调用包裹在 `asyncio.to_thread()` 中。

6. **[P1] LogChannel 的 `read_system_log` 和 `read_dmesg` 存在命令注入风险。**
- 证据：
  - `AIDC-auto-SRE.md:1262`：`f"tail -n {tail} {log_path}"` — `log_path` 未做 shell escape
  - `AIDC-auto-SRE.md:1270`：`f"| grep -i '{filter_str}'"` — `filter_str` 未做 shell escape
- 影响：如果 LLM 构造恶意的 `log_path`（如 `/dev/null; rm -rf /`）或 `filter_str`，SSH channel 会执行注入的命令。虽然 LLM 在诊断阶段只能调用 read_only 工具，但这些 read_only 工具本身存在写操作风险。
- 建议：对所有 shell 参数使用 `shlex.quote()`，或改用 SSH channel 的 exec 模式传递参数数组而非拼接字符串。

7. **[P1] LLM 在诊断阶段看不到 write 工具的 schema，无法生成正确的 RemediationPlan。**
- 证据：`get_tool_schemas(safety_level="read_only")`（`AIDC-auto-SRE.md:668`）只返回 read_only 工具。但 ReAct 循环要求 LLM 在 `action_type == "remediate"` 时生成包含具体 write 工具名和参数的 `RemediationPlan`（`AIDC-auto-SRE.md:710-715`）。
- 影响：LLM 不知道有哪些修复工具可用，可能生成不存在的工具名或错误的参数格式。
- 建议：在系统提示词中附加 write 工具的描述列表（只读参考，不注册为可调用工具），或在 `get_tool_schemas` 中增加 `"read_only" + "write_descriptions_only"` 模式。

8. **[P1] 模式记忆的置信度更新策略不区分修复成功/失败。**
- 证据：`AIDC-auto-SRE.md:1916`：
  ```python
  existing.confidence = min(1.0, existing.confidence + 0.1)
  ```
  无论 `record.outcome` 是 `"resolved"` 还是 `"failed"`，置信度都 +0.1。
- 影响：一个反复出现但每次修复失败的误判模式，7 次后置信度会达到 1.0，被优先推荐给 SRE Agent，导致诊断偏差。
- 建议：
  ```python
  if record.outcome == "resolved":
      existing.confidence = min(1.0, existing.confidence + 0.15)
  elif record.outcome == "failed":
      existing.confidence = max(0.0, existing.confidence - 0.1)
  ```

---

### P2 — 改进建议（不影响 Demo）

9. **[P2] `DiscoveryAgent.infer_topology()` 是空实现（`pass`），数字孪生会有断连子图。**
- 证据：`AIDC-auto-SRE.md:610-616`。
- 影响：BMC 发现的 Node 和 K8s 发现的 Pod 之间不会自动建立 HOSTED_ON 关系，拓扑图形同虚设。Demo 中需要手动确保拓扑完整。
- 建议：至少实现 hostname 匹配逻辑（K8s node name == BMC hostname → HOSTED_ON 关系），Demo 可用。

10. **[P2] ThinkingStep 和 Observation 缺少 `to_dict()` 方法但被 WebSocket 推送引用。**
- 证据：`AIDC-auto-SRE.md:822` 调用 `step.to_dict()`，但 `ThinkingStep`（`AIDC-auto-SRE.md:756-764`）和 `Observation`（`AIDC-auto-SRE.md:767-772`）均为 `@dataclass`，无 `to_dict()` 方法定义。
- 建议：使用 `dataclasses.asdict()` 或显式实现 `to_dict()`。

11. **[P2] ConversationalAgent 的对话历史无限增长。**
- 证据：`AIDC-auto-SRE.md:2905`：`self.conversations: dict[str, list[dict]] = {}`，只有 `append` 无 trim。
- 影响：长时间运行后内存泄漏，LLM context window 溢出。
- 建议：设置 max_history（如 50 轮），超出后截断或摘要化。

12. **[P2] 无 LLM 调用速率限制或成本控制。**
- ReAct 循环最多 20 步，每步一次 LLM 调用。如果同时有多个告警触发多个诊断会话，LLM API 成本和速率可能失控。
- 建议：增加全局并发诊断数限制（配置已有 `max_concurrent_remediations`，缺少 `max_concurrent_diagnoses`）。

13. **[P2] `CanaryConfig.success_criteria` 使用字符串表达式（如 `"p95_latency < 500ms"`），解析方式未定义。**
- 证据：`AIDC-auto-SRE.md:1415`、`AIDC-auto-SRE.md:1477`。`_check_criterion(criterion)` 方法未给出实现。
- 建议：与 P0-2 的 `eval()` 问题统一解决，定义条件表达式 DSL 或结构化 VerificationConfig。

14. **[P2] 配置中 `kubeconfig` 出现两处，可能冲突。**
- 证据：`channels.kubernetes.kubeconfig: "~/.kube/config"`（`AIDC-auto-SRE.md:2445`）和 `ontology.discovery.k8s_clusters.main.kubeconfig: "~/.kube/config"`（`AIDC-auto-SRE.md:2477`）。
- 影响：维护两份 kubeconfig 路径易不一致。
- 建议：discovery 中引用 channels 的配置，避免重复。

---

## 本轮已确认的设计亮点

- "自由诊断、受控修复" 架构边界清晰，ReAct 循环 vs 确定性引擎的分离合理。
- Demo 场景（11.1、11.2）的 10 步诊断 Trace 足够具体，可直接用于 Demo 脚本编写。
- 记忆系统的三层设计（事件/模式/配置）与系统提示词的集成方式实用。
- WAL + 灰度 + 审批门控的三层修复安全设计与 fault-injector 保持一致。
- Skills 框架采用 YAML 格式打包工具 + 提示词 + 示例，扩展性好。

## Demo 结论

- **P0-1（工具定义不匹配）**：必须在 Demo 前补齐 `get_gpu_processes` 工具或修正 Demo Trace 中的工具调用。
- **P0-2（eval 安全漏洞）**：必须在 Demo 前替换为安全求值器，否则现场演示有被质疑安全性的风险。
- 其余 P1/P2 项可 Demo 后迭代。

## 长期产品级优化（单独列出）

1. **Ontology 实时同步**：K8s Informer watch 替代定时全量刷新，保持 Pod 级别的实时拓扑。
2. **多 LLM 路由**：按诊断复杂度和成本自动选择 LLM（简单告警用轻量模型，复杂根因用强模型）。
3. **可观测性**：Agent 自身的 SLI/SLO（诊断成功率、MTTR、LLM 调用耗时/成功率）。
4. **联邦记忆**：多 AIDC 间共享匿名化模式记忆（A 站学到的模式可加速 B 站诊断）。
5. **Runbook 自动生成**：从高置信度 LearnedPattern 自动生成 Runbook YAML，形成知识闭环。
