# AIDC Auto-SRE 分阶段开发计划

> 文档版本：v2.4 | 更新日期：2026-02-20 | 分支：claude/phased-development-plan-ZTWRB
> v2.0 修订：对照 load-simulator.md / fault-injector.md / AIDC-auto-SRE.md 全文补全 14 项差距
> v2.1 修订：`load_simulator` 包 v0.1.0 已实现（25 文件，2229 行）；更新实现进度与 Sprint Track A 状态
> v2.2 修订：全文对照三份设计文档核对，新增 G-15 至 G-32 共 18 项遗漏；更新 Sprint 1-4 Track B/C/D/E；补入验收标准矩阵（修复G-23）；POC 交付物补 P1-10/P2-1
> v2.3 修订：Sprint 条目去除实现细节，改为模块名 + 设计文档 §节 + Gap ID 引用格式
> v2.4 修订：对照 AIDC-auto-SRE-review-feedback.md 审查，新增 G-33（OpenTelemetry）；补入性能基线验收、LLM 降级验收；Sprint 3 注明 resource_lock 分阶段决策

---

## 目录

- [背景与目标](#背景与目标)
- [组件总览与目录结构](#组件总览与目录结构)
- [Gap Analysis 修订说明](#gap-analysis-修订说明)
- [Demo 阶段（第 1–2 月）](#demo-阶段第-12-月)
- [POC 阶段（第 3–4 月）](#poc-阶段第-34-月)
- [Prod Phase 1（第 5–6 月）](#prod-phase-1第-56-月)
- [Prod Phase 2（第 7–8 月）](#prod-phase-2第-78-月)
- [Prod Phase 3（第 9–10 月）](#prod-phase-3第-910-月)
- [并行开发 Session 分工](#并行开发-session-分工)
- [附录](#附录)

---

## 背景与目标

AIDC Auto-SRE 是面向 AI 数据中心的自主运维智能体系统，通过 LangGraph ReAct + NeMo Guardrails 实现告警驱动的根因诊断与受控修复。整体由三个组件构成：

| 组件 | 职责 | 优先级 | 状态 |
|------|------|--------|------|
| `load_simulator` | 生成确定性推理/训练/微调/Notebook 负载，暴露瓶颈 | 前置依赖，最优先 | **✅ v0.1.0 已实现**（2026-02-19） |
| `fault_injector` | 多维度故障注入（硬件/OS/平台/服务），驱动 Demo 场景 | 前置依赖，最优先 | ❌ 待实现（Sprint 1-2） |
| `sre_agent` | LangGraph ReAct 诊断 + NeMo 受控修复 + GUI | 核心交付物 | ❌ 待实现（Sprint 1-4） |

**核心不变量**：LLM 只做只读分析，确定性引擎（asyncio）负责注入/恢复/执行，WAL 保证可回滚。

---

## 组件总览与目录结构

```
cube-studio/
├── load_simulator/
│   ├── __main__.py / cli.py          # CLI 入口（含 run --output-format json 子命令）
│   ├── config/                        # schema.py / loader.py / defaults.py
│   ├── orchestrator/                  # engine.py / session.py / scheduler.py
│   ├── agents/
│   │   ├── inference.py               # 推理压测 Agent
│   │   ├── pipeline.py                # 训练 Pipeline Agent
│   │   ├── finetune.py                # LLaMA-Factory 微调 Agent
│   │   ├── notebook.py                # Notebook Agent
│   │   ├── monitor.py                 # 三层指标采集
│   │   └── bottleneck.py              # MiniMax-2.1 六层瓶颈分析（唯一 LLM 组件）
│   ├── channels/                      # cube_studio / inference / prometheus / notebook / k8s
│   ├── load/                          # profile.py / rate_limiter.py / token_distribution.py / prompt_pool.py
│   ├── metrics/                       # collector.py / aggregator.py / time_series.py / thresholds.py
│   ├── reporting/                     # html_report.py / charts.py / comparison.py
│   └── tests/
│
├── fault_injector/
│   ├── __main__.py / cli.py
│   ├── config/
│   ├── orchestrator/                  # engine.py / session.py / scheduler.py / watchdog.py
│   ├── agents/                        # hardware / os_fault / platform / service / monitor / diagnosis
│   ├── channels/                      # ssh / redfish / switch / kubernetes / cube_studio / prometheus
│   ├── scenarios/
│   │   ├── vllm_latency.py            # RC-1~RC-6 六个场景
│   │   ├── rdma_anomaly.py            # F-1~F-6 六个场景
│   │   └── registry.py
│   ├── safety/                        # guard.py / rollback.py (WAL)
│   ├── reporting/                     # timeline.py / html_report.py / charts.py / resilience.py
│   └── tests/
│
├── sre_agent/
│   ├── cli.py / server.py / config.py
│   ├── agent/                         # sre_agent.py / graph.py / state.py / nodes.py
│   │                                  # discovery_agent.py / monitor_agent.py
│   │                                  # conversational_agent.py / thinking_trace.py / prompts/
│   ├── guardrails/                    # config.yml / rails/{input,output,execution,dialog}.co
│   │                                  # actions.py / prompts.yml
│   ├── nat/                           # workflow.yml / eval_dataset.jsonl
│   ├── channels/                      # ssh / redfish / switch / k8s / cube_studio / prometheus
│   │                                  # log / alert / ontology / knowledge（新增）
│   ├── tools/                         # registry.py / definitions.py / metrics / logs / k8s_tools
│   │                                  # network / bmc / platform / ontology_tools
│   │                                  # knowledge_tools / remediation_tools
│   ├── skills/
│   │   ├── runtime/                   # registry.py / executor.py / tools.py（4个固定@tool）/ policy.py
│   │   ├── builtin/                   # vllm-diagnosis / rdma-diagnosis / gpu-health / network-diagnosis
│   │   └── custom/                    # 用户热加载目录
│   ├── ontology/
│   │   ├── models.py / graph.py / store.py
│   │   └── discovery/                 # bmc_scanner / switch_scanner / k8s_scanner / prometheus_scanner
│   ├── remediation/
│   │   ├── engine.py / loop_orchestrator.py / incident_handler.py
│   │   ├── planner.py / validator.py / canary.py / approval.py / wal.py
│   ├── knowledge/                     # store.py / ingest.py / chunker.py / runbook.py
│   ├── memory/                        # store.py / incident.py / pattern.py / config_memory.py
│   ├── concurrency/                   # resource_lock.py / alert_dedup.py / alert_correlator.py
│   ├── auth/                          # middleware.py / secrets.py
│   ├── safety/                        # guard.py / forbidden.py
│   ├── ha/                            # heartbeat.py / leader.py
│   ├── slo/                           # metrics.py / degradation.py
│   ├── lifecycle/                     # data_lifecycle.py
│   ├── api/                           # routes/{diagnosis,remediation,topology,knowledge,incidents}
│   │                                  # websocket.py / auth.py
│   ├── frontend/                      # React 18 + Ant Design 5 + D3.js（Vite + Zustand）
│   └── tests/
│
└── docs/demo/
    ├── 01-deployment.md
    ├── 02-demo-steps.md
    ├── 03-config-reference.md
    ├── 04-troubleshooting.md
    └── 05-architecture.md
```

---

## Gap Analysis 修订说明

v1.0 → v2.0 修订的 14 项差距：

| # | 差距 | 修复内容 |
|---|------|---------|
| G-01 | Demo 2 RDMA RC-2 根因错误 | 改为 F-2（ecn_misconfiguration）+ F-4（rdma_link_flap），删除"QP 耗尽" |
| G-02 | Demo 3 缺失 | 新增 Demo 3（ambiguous 场景 + LoopOrchestrator 多候选循环验证） |
| G-03 | Skills 框架未入 Sprint | Sprint 2 Track C 新增 skills/runtime + skills/builtin 实现 |
| G-04 | 前端栈错误 | 修正为 Vite + Zustand（非 webpack + MobX） |
| G-05 | LoopOrchestrator + IncidentHandler 缺失 | Sprint 3 Track C 新增 |
| G-06 | 并发模块缺失 | Sprint 3 Track C 新增 concurrency/（alert_dedup, resource_lock） |
| G-07 | load-simulator 只提推理 Agent | 明确列出 4 个功能 Agent（inference/pipeline/finetune/notebook） |
| G-08 | 联动接口缺 `run --output-format json` | Sprint 2 Track A 新增该子命令实现 |
| G-09 | Discovery 子扫描器未覆盖 | Sprint 3 Track E 新增 ontology/discovery/ 四个扫描器 |
| G-10 | remediation/validator.py 缺失 | Sprint 3 Track C 新增 |
| G-11 | 六层瓶颈阈值引擎缺失 | Sprint 2 Track A 新增 metrics/thresholds.py |
| G-12 | NeMo Guardrails .co 文件未入 Sprint | Sprint 2 Track C 新增 rails/{input,output,execution,dialog}.co |
| G-13 | NAT 评估集成缺失 | Sprint 4 Track E + Prod Phase 1 新增 |
| G-14 | 场景名称未引用设计精确名 | 全文替换为设计文档中的精确 scenario 名 |

v2.0 → v2.1 修订（load_simulator v0.1.0 实现状态更新）：见 load-simulator.md 实现状态节。

v2.1 → v2.2 修订（核对三份设计文档全文后补入的 18 项遗漏）：

| # | 来源文档 | 差距 | 处理 Sprint |
|---|----------|------|------------|
| G-15 | AIDC-auto-SRE.md §4.2, Appendix C P1-10 | 同步库（sqlite3/ChromaDB）阻塞事件循环：sqlite3→aiosqlite；ChromaDB→asyncio.to_thread() | Sprint 2 Track E + Sprint 3 Track C |
| G-16 | AIDC-auto-SRE.md §10.6, Review P1-6 | SkillExecutor 脚本沙箱隔离（namespace/cgroup 隔离 + 输出注入防护）未入 Sprint | Sprint 2 Track C |
| G-17 | AIDC-auto-SRE.md §7.3, Review P1-7 | Canary 多条件聚合 `criteria_mode`（all/any）未入 Sprint | Sprint 3 Track C |
| G-18 | AIDC-auto-SRE.md §13.3, Review P2-3 | WebSocket backpressure + 断线重连 `last_event_id` 未入 Sprint | Sprint 3 Track D |
| G-19 | AIDC-auto-SRE.md §3.5, Appendix C P1-7 | DiscoveryAgent `gather(return_exceptions=True)` 未检查结果（错误聚合 + 数据新鲜度标注）| Sprint 3 Track E |
| G-20 | AIDC-auto-SRE.md §8, Appendix C P2-4 | 知识库文档 ID 哈希碰撞（增加 source/category/version 维度）计划中但未入 Sprint | Sprint 3 Track E |
| G-21 | AIDC-auto-SRE.md §18.2 | `nat/workflow.yml` 实现（NAT 包裹 LangGraph profiling）未入 Sprint Track 项 | Sprint 4 Track C |
| G-22 | AIDC-auto-SRE.md §12 | sre_agent 完整 YAML 配置 Schema 实现（AgentConfig/RemediationConfig/DiscoveryConfig/HAConfig/SLOConfig）未明确入 Sprint | Sprint 1 Track C |
| G-23 | AIDC-auto-SRE.md Appendix C §6333 | 15 维度验收标准矩阵未纳入交付物检查清单 | Demo/POC/Prod Phase 1 各阶段 |
| G-24 | AIDC-auto-SRE.md §7.2.1, Review P0-1 | `SREResponse[T]` 统一响应契约 + ErrorCode 表在 Sprint 3 Track C 中未明确列出 | Sprint 3 Track C |
| G-25 | AIDC-auto-SRE.md §7.8 | LoopOrchestrator 终止约束 `max_candidates=3` / `max_re_diagnosis_rounds=1` 未在 Sprint 中明确约束 | Sprint 3 Track C |
| G-26 | fault-injector.md §12 | fault_injector DiagnosisAgent（单次 LLM 诊断，与 SRE Agent 无关）未入 Sprint | Sprint 2 Track B |
| G-27 | fault-injector.md §3, §4 | Hardware/OS Fault Agent 细分子类型未明确入 Sprint：CPU stress（stress-ng）/ 内存（stress-ng --vm）/ 存储（dd）/ 电源（Redfish 电源封顶）/ 内核（sysctl）/ 文件系统（/dev/full）/ 进程（kill -9 + ulimit） | Sprint 2 Track B |
| G-28 | fault-injector.md §11 | fault_injector Orchestrator 五阶段执行流（pre-flight/setup/injection/monitoring/recovery）+ `--dry-run` 模式未明确入 Sprint | Sprint 1 Track B |
| G-29 | load-simulator.md §4 | load_simulator Monitor Agent 三层架构（Layer1 应用层/Layer2 Prometheus层/Layer3 平台API层）未明确入 Sprint（v0.1 仅实现系统层） | Sprint 2 Track A |
| G-30 | load-simulator.md §7, §9 | load_simulator 执行模式（stress/soak/mixed/single）+ AdaptiveRules（breaking point 二分搜索）未明确入 Sprint | Sprint 2 Track A |
| G-31 | load-simulator.md §2.9 | load_simulator Cube Studio 认证机制（JWT `CUBE_STUDIO_JWT_SECRET` + refresh）未明确入 Sprint | Sprint 2 Track A |
| G-32 | fault-injector.md §5 | Platform Fault Agent 覆盖范围缺 Ceph（osd pause）/Kafka/Prometheus 故障子类型 | Sprint 3 Track B |
| G-33 | AIDC-auto-SRE-review-feedback.md §2.2.6 | 分布式追踪（OpenTelemetry）：统一 trace_id 贯穿 API→Agent→Channel→外部系统，缺失导致跨服务排障困难（P2-7） | Prod Phase 1 Sprint 7 |

---

## Demo 阶段（第 1–2 月）

### 目标

| 维度 | 内容 |
|------|------|
| 场景 | Case 1: p95 vLLM 延迟异常（2 根因）；Case 2: RDMA 异常（2 根因）；Case 3: ambiguous 多候选验证 |
| GUI | 展示 7 个页面：拓扑/告警/诊断/修复/对话/知识库/记忆库（其中 Skills 在诊断/工具页面） |
| 文档 | 部署手册、Demo 步骤、Config 参考、故障排查、架构图 |
| 测试 | 单元测试覆盖率 ≥ 70%，E2E 测试跑通 3 个完整 Case |

---

### Demo Case 详细说明

#### Case 1：p95 vLLM 推理延迟异常

| | 根因 | fault_injector 场景名 | 注入操作 | 诊断关键工具 |
|--|------|----------------------|---------|-------------|
| RC-A | GPU 资源争抢：同节点 `gpu-burn` 进程占用 GPU-0 全部算力，PCIe 总线带宽争用影响推理服务 | `gpu_contention` | SSH 启动 `gpu-burn -d 600` | `get_gpu_metrics` → `get_gpu_processes` |
| RC-B | 网络链路抖动：tc netem 注入 50ms±100ms Pareto 分布延迟，推理请求尾延迟呈长尾分布 | `network_jitter` | SSH `tc qdisc add dev eth0 root netem delay 50ms 100ms distribution pareto` | `check_nic_errors` → `query_prometheus(vllm p95)` |

**Agent 完整诊断 Trace（RC-A，共 10 步，与设计 §11.1 对齐）**

```
Step 1: query_prometheus → vllm_request_duration_seconds{quantile="0.95"} > 0.5 [触发]
Step 2: query_topology(service="vllm-deepseek") → Pod→Node(gpu-1-1)→GPU-0,GPU-1→NIC→Switch
Step 3: get_gpu_metrics(node="gpu-1-1") → GPU-0 util=97.2%, GPU-1 util=68.5%
Step 4: [Think] GPU-0 异常高，推断争抢，假设 A 升级
Step 5: get_gpu_processes(node="gpu-1-1") → gpu-burn PID=12847 on GPU-0  [P0-5 新增工具]
Step 6: get_thermal_status(node="gpu-1-1") → 温度正常 → 假设 D(热降频)排除
Step 7: check_nic_errors(node="gpu-1-1") → 无 NIC 错误 → 假设 B(网络)排除
Step 8: search_knowledge("GPU contention vllm") → 命中 KB-042
Step 9: [Conclude] 根因=gpu-burn争用 GPU-0，置信度=0.94
Step 10: kill_process(node="gpu-1-1", pid=12847) [human_confirm→执行→验证 P95<500ms→写记忆]
```

---

#### Case 2：RDMA 网络异常

| | 根因 | fault_injector 场景名 | 注入操作 | 诊断关键工具 |
|--|------|----------------------|---------|-------------|
| RC-A | ECN 标记阈值错配：交换机 ECN threshold 设为 100 bytes（正常应 ~150KB），DCQCN 频繁降速触发 PFC 风暴，NCCL AllReduce 超时 | `ecn_misconfiguration` | Switch CLI `qos wred queue 3 ecn; qos queue 3 wrr weight 1` | `get_pfc_counters` → `get_switch_port_status` → `get_ecn_config` |
| RC-B | RDMA 链路间歇性中断：200G 端口 30s 周期 shutdown/undo shutdown，RDMA QP 重建风暴，训练 hang | `rdma_link_flap` | Switch CLI 脚本周期性 `shutdown`/`undo shutdown` HGE1/0/1 | `get_switch_port_status` → `check_rdma_status` → `query_prometheus(rdma errors)` |

**Agent 完整诊断 Trace（RC-A，共 8 步，与设计 §11.2 对齐）**

```
Step 1: 告警 NCCLTimeout → Pipeline(pytorch-training) 失败
Step 2: query_topology → Pod→Node(gpu-1-1/gpu-1-2)→NIC→SwitchPort(sw-200g:HGE1/0/1,HGE1/0/2)
Step 3: check_rdma_status(gpu-1-1/gpu-1-2) → LinkUp, Active → 假设 C(link flap)降低
Step 4: get_pfc_counters → rx_pause{3: 4521890} 极高 → 假设 A(PFC 风暴)升级
Step 5: get_switch_port_status(sw-200g, HGE1/0/1) → ecn marking_threshold=100 [异常!]
Step 6: get_ecn_config(sw-200g) → HGE1/0/{1,2} threshold=100, 其他=150000 → 根因确认
Step 7: [Conclude] 根因=ECN配置错误，置信度=0.96，传播链 ECN→DCQCN→PFC风暴→NCCL超时
Step 8: configure_ecn(灰度: HGE1/0/1→150000, 观察60s, HGE1/0/2→150000) [human_confirm]
```

---

#### Case 3（Demo 3）：Ambiguous 多候选根因循环验证

**说明**：此场景证明 LoopOrchestrator 的多候选根因能力，是需求"multiple root cause"的核心 Demo。

```
fault_injector 同时注入两个低强度故障：
  1. gpu_contention (低强度): gpu-burn --util 60%（GPU-0 利用率 ~62%）
  2. vLLM gpu_memory_utilization 配置调低: 0.80 → 0.65（KV Cache 使用率 ~92%）

告警: VLLMLatencyP95High (720ms > 500ms)

Agent 诊断结论（Step 10）:
  diagnosis_certainty: "ambiguous"
  候选 #1: KV Cache 配置不足 (confidence=0.55)
  候选 #2: GPU 资源争用   (confidence=0.50)
  → 无法仅通过只读观测区分，触发 LoopOrchestrator

LoopOrchestrator 循环执行:
  尝试 #1: 修复 KV Cache (gpu_memory_utilization → 0.90)
    验证结果: KV Cache 92%→58% ✓，P95 720ms→580ms ✗ (仍超标)
    → 验证失败，WAL 回滚 gpu_memory_utilization，进入下一候选

  尝试 #2: kill gpu-burn
    验证结果: GPU-0 util=3.2% ✓，P95 580ms→280ms ✓
    → 验证成功，结论: 两个根因均存在，gpu-burn 是主因
    → 记忆写入: "低强度 GPU 争用 + KV Cache 偏低可叠加放大延迟"
```

---

### P0 安全问题修复清单（Sprint 1 前必须确认方案）

| ID | 问题 | 修复方案 | 设计文档位置 |
|----|------|---------|------------|
| P0-1 | ~~load↔fault 联动接口：fault-injector 调用 `run --output-format json`，但 load-simulator 未定义此子命令~~ | **✅ 已修复（v0.1.0）**：`python -m load_simulator run --output-format json` 已实现，stdout 输出完整 JSON 摘要 | fault-injector §9.2 |
| P0-2 | SSH 命令拼接注入（log_path/filter_str 未转义） | 所有 SSH 参数使用 `shlex.quote()`，禁止管道字符 | review §P0-2 |
| P0-3 | API/WebSocket 全部缺鉴权 | 统一 JWT 中间件 + 接口级 RBAC | review §P0-3 |
| P0-4 | `asyncio.Queue.get(timeout=...)` 不存在 | 改为 `asyncio.wait_for(queue.get(), timeout=300)` | review §P0-4 |
| P0-5 | Demo 1 Step 5 调用工具与定义不匹配 | 新增 `get_gpu_processes` 只读工具 | review §P0-5 |

---

### Sprint 计划（8 周，8 个并行 Session）

```
Week 1-2  [Sprint 1 — 骨架与前置依赖]
─────────────────────────────────────────────────────────────────────
Track A │ [✅ 已完成 2026-02-19] load_simulator v0.1.0 实现（详见 load-simulator.md 实现状态节）
        │ [❌ 待实现] channels/ 模块（Sprint 2 Track A）
Track B │ fault_injector: CLI 骨架（含 --dry-run 预检，§11.1，修复G-28）
        │ + Orchestrator 五阶段执行流（§11.2，修复G-28）
        │ + channels/: SSHChannel / RedfishChannel / SwitchChannel（§2.6）
        │ + safety/: WAL rollback.py + SafetyGuard（§2.7 / §13）
        │ + orchestrator/: watchdog.py + Scenario 基类 + 场景注册表（§2.8）
Track C │ sre_agent: LangGraph 骨架（agent/: sre_agent/graph/state/nodes，§4.2）
        │ + ToolRegistry + get_gpu_processes（§5.2，修复P0-5）
        │ + SSH 注入防护 shlex.quote + VerificationCondition DSL（§2.6 / Appendix C P0-1/P0-2）
        │ + sre_agent 完整 Config Schema（§12，修复G-22）
Track D │ sre_agent/frontend: Vite+React18+Zustand+AntDesign5 项目初始化（修复G-04）
        │ + 7 页面路由 + 布局组件 + WebSocket client
Track E │ Docker Compose 部署环境 + 各 Channel mock（Prometheus/K8s/Redfish/Switch）
        │ + CI 骨架（pytest + ruff）

Week 3-4  [Sprint 2 — 核心功能]
─────────────────────────────────────────────────────────────────────
Track A │ load_simulator（Sprint 2 补全）:
        │ + channels/ 全部实现，含 CubeStudio JWT 认证（§2.6 / §2.9，修复G-31）
        │ + load/: LoadProfile / RateLimiter / TokenDistribution / PromptPool（§2.7）
        │ + Monitor Agent 三层架构完整实现（§4，修复G-29）
        │ + InferenceAgent 完整实现（§3.1）
        │ + 四种执行模式 + AdaptiveRules（§7 / §9.1，修复G-30）
        │ + BottleneckAnalyzer LLM 模式（§5.4）
        │ + reporting/ HTML 报告 + Plotly 图表 + 版本对比（§8）
Track B │ fault_injector:
        │ + Demo 场景: gpu_contention(RC-1) + network_jitter(RC-2)（§7.1，修复G-01）
        │ + Demo 场景: ecn_misconfiguration(F-2) + rdma_link_flap(F-4)（§8.1，修复G-01）
        │ + HardwareFaultAgent 全部子类型（§3，修复G-27）
        │ + OSFaultAgent 全部子类型（§4，修复G-27）
        │ + DiagnosisAgent 单次 LLM 诊断（§12，修复G-26）
Track C │ sre_agent:
        │ + NeMo Guardrails 4 个 rail 文件（§17，修复G-12）
        │ + ReAct 循环（agent_node/conclude_node/remediate_node，§4.2）
        │ + asyncio.wait_for 步级 + 会话级超时（§4.2，修复P0-4）
        │ + skills/runtime/: SkillRegistry + SkillExecutor + 4 个固定 @tool（§10，修复G-03）
        │ + SkillExecutor 脚本沙箱（§10.6，修复G-16）
        │ + skills/builtin/: vllm-diagnosis + rdma-diagnosis（§10.3）
Track D │ sre_agent/frontend: 拓扑页 + 诊断页（ThinkingStep 实时流）+ 告警页
Track E │ sre_agent/storage（修复G-15，P1-10）:
        │ + KnowledgeStore: ChromaDB + asyncio.to_thread（§8）
        │ + MemoryStore: aiosqlite，三类记忆（§9）
        │ + OntologyGraph: NetworkX + aiosqlite，BFS 双向遍历（§3.4，修复P1-1）

Week 5-6  [Sprint 3 — 场景集成与修复引擎]
─────────────────────────────────────────────────────────────────────
Track A │ load_simulator:
        │ + load↔fault 联动契约 E2E 测试（联动接口已实现，修复P0-1）
        │ + 单元测试套件（test_config / test_channels / test_load_profile / test_metrics / test_agents）
        │ + 四个 Agent E2E 测试
Track B │ fault_injector:
        │ + 扩展场景: storage_io_interference(RC-3) + platform_cascade(RC-4)（§7.1）
        │ + 扩展场景: pfc_deadlock(F-1) + roce_mtu_mismatch(F-5)（§8.1）
        │ + PlatformFaultAgent 全部子类型（§5，修复G-32）
        │ + ServiceFaultAgent（§6）
        │ + load-simulator 联动集成（§9.2）
Track C │ sre_agent:
        │ + LoopOrchestrator（§7.7 / §7.8，含终止约束，修复G-05 / G-25）
        │ + IncidentHandler（§7.9，修复G-05）
        │ + RemediationEngine: WAL + Canary（含 criteria_mode，§7.2 / §7.3，修复G-17）
        │ + SREResponse[T] 统一响应契约（§7.2.1，修复G-24）
        │ + remediation/validator.py（§7.5，修复G-10）
        │ + concurrency/: alert_dedup + alert_correlator + resource_lock（§6.3 / §7.7，修复G-06）
        │ + JWT 鉴权 + RBAC（§13.2 / §15.2，修复P0-3）
        │ + FastAPI REST routes + WebSocket 思考流（§13）
Track D │ sre_agent/frontend:
        │ + 修复页 + 知识库页 + 记忆库页 + Skills 页 + 对话页
        │ + WebSocket backpressure + 断线重连 last_event_id（§13.3，修复G-18）
Track E │ sre_agent/ontology:
        │ + ontology/discovery/ 四个扫描器（§3.5，修复G-09）
        │ + infer_topology 拓扑推断（§3.5，修复P2-5）
        │ + DiscoveryAgent gather 错误聚合（Appendix C P1-7，修复G-19）
        │ + KnowledgeStore 文档 ID 多维度（Appendix C P2-4，修复G-20）
        │ + 知识库种子数据导入（vLLM / RDMA Runbook）

Week 7-8  [Sprint 4 — 测试、文档、Demo 彩排]
─────────────────────────────────────────────────────────────────────
Track A │ load_simulator: 单元测试 + E2E 测试（4 个 Agent 各一个）
Track B │ fault_injector: 单元测试 + 安全注入测试 + WAL 崩溃恢复测试
Track C │ sre_agent: 单元测试（test_agent / test_ontology / test_remediation / test_tools / test_auth
        │   test_resource_lock / test_alert_correlator / test_slo_degradation）
        │ + nat/workflow.yml（§18.2，修复G-21）+ nat/eval_dataset.jsonl（修复G-13）
Track D │ E2E 测试套件（5 个场景，参见 §11 Demo 场景详细说明）:
        │   Case 1 RC-A/RC-B / Case 2 RC-A/RC-B / Case 3 Loop / GUI Skills / Knowledge / Memory
Track E │ 完整文档（docs/demo/ 五篇）+ Demo 彩排 + 问题修复
```

---

### Demo 交付物检查清单

**load_simulator** *(v0.1.0 已实现，2026-02-19)*
- [x] 4 个功能 Agent（inference/pipeline/finetune/notebook）可独立运行（`--only` 参数）
- [x] `python -m load_simulator run --output-format json` 子命令输出有效 JSON
- [ ] HTML 报告含延迟分布/吞吐/资源时间线图表 *(reporting/ 模块待实现，Sprint 2 Track A)*
- [x] 六层瓶颈阈值引擎检测并报告（`metrics/thresholds.py`）

**fault_injector**
- [ ] `gpu_contention` / `network_jitter` 场景可注入并自动回滚
- [ ] `ecn_misconfiguration` / `rdma_link_flap` 场景可注入并自动回滚
- [ ] WAL 崩溃恢复：进程 kill 后 `--resume` 可恢复所有活跃故障
- [ ] load-simulator 联动：子进程调用成功，stdout JSON 解析正确

**sre_agent**
- [ ] Case 1 RC-A/RC-B：端到端诊断 + 审批 + 修复 + 验证
- [ ] Case 2 RC-A/RC-B：端到端诊断 + 灰度修复 + 验证
- [ ] Case 3：ambiguous 诊断 + LoopOrchestrator 两轮验证 + 记忆写入
- [ ] NeMo Guardrails 4个 rail 文件工作，拦截危险输入/输出
- [ ] Skills：`list_skills` / `load_skill` / `run_skill` 可用，2 个 builtin skill 内容完整
- [ ] GUI 7 个页面全部可访问（拓扑/告警/诊断/修复/对话/知识库/记忆库）
- [ ] JWT 鉴权生效，未授权请求返回 401
- [ ] 单元测试覆盖率 ≥ 70%
- [ ] 5 个 E2E 场景测试通过（Case 1-3 + GUI Skills/Knowledge/Memory）
- [ ] 完整文档（部署+Demo步骤+Config+故障排查+架构）

**验收标准矩阵（AIDC-auto-SRE.md Appendix C §验收标准，修复G-23）**
- [ ] 安全性：无动态代码执行路径，工具命令无 shell 注入面（bandit/semgrep 扫描通过）
- [ ] 契约一致性：所有核心接口 `SREResponse[T]` 强制，字段一致率 100%（CI 契约测试）
- [ ] 权限有效性：高风险接口未授权不可执行，越权测试拦截率 100%
- [ ] 并发安全性：重复告警与并发告警下，无重复修复与交叉写冲突（ResourceLock 压测）
- [ ] 正确性：3 类标准故障场景影响面识别准确率 ≥ 90%
- [ ] 故障注入验收：已知故障类型诊断准确率 ≥ 80%
- [ ] 可执行性：修复计划 schema 一次通过率 ≥ 95%，不合法计划可被 PlanValidator 拦截
- [ ] 可回滚性：所有写操作有 WAL 且通过 `recover_all()` 回滚演练
- [ ] 成本可控：单次诊断 token ≤ 100K，超出自动终止（`max_tokens_per_diagnosis`）

---

## POC 阶段（第 3–4 月）

### 目标

| 维度 | Demo | POC 新增 |
|------|------|---------|
| 根因数/场景 | vLLM 2 + RDMA 2 + ambiguous 1 | vLLM 全 6 RC + RDMA 全 6 F |
| 修复流程 | human_confirm | canary 灰度 + auto_approve（低风险）+ 自动回滚验证 |
| 知识库 | 种子数据 | Runbook 批量摄入 pipeline + 增量更新 |
| 记忆库 | 基础读写 | 置信度双向更新（成功+0.15/失败-0.1）+ 时间衰减 |
| 并发 | 单诊断会话 | ≤ 5 并发诊断（Semaphore + ResourceLock） |
| 压力测试 | — | 1000 次模拟告警回放，无死锁/无未恢复会话 |
| 回滚测试 | — | 注入 10 类失败场景，WAL 回滚成功率 ≥ 99% |

### 里程碑与并行 Track

```
Week 9-10  [Sprint 5 — 扩展故障场景]
─────────────────────────────────────────────────────────────────────
Track A │ fault_injector: vllm_latency.py 补全 RC-3(storage_io_interference)
        │   RC-4(platform_cascade) + RC-5(os_resource_pressure) + RC-6(thermal_throttling)
Track B │ fault_injector: rdma_anomaly.py 补全 F-1(pfc_deadlock) + F-3(rdma_load_imbalance)
        │   + F-5(roce_mtu_mismatch) + F-6(rdma_qos_downgrade)
Track C │ sre_agent: 新增诊断工具(get_ecn_config/get_pfc_counters/check_thermal)
        │ + 写工具 schema 注入 system prompt(修复P1-5)
        │ + LLM 健康监控 + 降级 ThresholdEngine(修复P1-5)
Track D │ GUI: 多候选假设树可视化 + 循环验证进度展示
Track E │ 知识库: Runbook 批量摄入(ingest.py + chunker.py) + 文档版本管理

Week 11-12 [Sprint 6 — 可靠性与压力测试]
─────────────────────────────────────────────────────────────────────
Track A │ load_simulator: 多 Agent 并发混合压测 + historical comparison 报告
Track B │ fault_injector: 组合故障场景(combined_scenario 多阶段注入)
        │ + 1000 次模拟回放 + WAL 回滚 ≥ 99% 验证
Track C │ sre_agent: 并发诊断 Semaphore(max_concurrent_diagnoses 配置项)
        │ + LangGraph checkpoint 持久化(AsyncSqliteSaver) + --resume 支持
        │ + 对话 Agent function-calling 完整 loop(修复P1-8)
        │ + 记忆置信度双向更新(修复P1-9)
Track D │ GUI: 多诊断会话列表 + 实时状态 + 拓扑动态高亮
Track E │ 性能测试: 1000 次告警回放 + 无死锁 + aiosqlite 异步重构验证
```

### POC 交付物

- [ ] 全部 6 个 vLLM RC 场景 + 6 个 RDMA F 场景可注入/诊断
- [ ] 知识库 Runbook 摄入 pipeline 可用
- [ ] 并发诊断 ≤ 5，无死锁，ResourceLock 生效
- [ ] 1000 次告警回放通过（无未恢复会话，内存无泄漏）
- [ ] WAL 回滚成功率 ≥ 99%（注入 10 类失败场景验证）
- [ ] 对话 Agent 多步 function-calling 场景通过 E2E 测试
- [ ] LangGraph checkpoint 持久化，sre-agent --resume 可用
- [ ] aiosqlite + asyncio.to_thread 异步化验证：事件循环阻塞告警为 0（修复G-15，P1-10）
- [ ] SQLite/NetworkX 并发安全：锁和事务边界通过并发压测（P2-1）

---

## Prod Phase 1（第 5–6 月）

### 目标：生产安全加固 + 高可用 + 可观测

| 维度 | 内容 |
|------|------|
| 认证 | JWT/OIDC + token 轮换 + 多租户隔离 |
| 审计 | 防篡改审计日志（hash chain + 远端 S3/OSS 双写） |
| 密钥管理 | K8s Secret / Vault 集成 + 自动轮换（修复 P2-3 默认 TLS 开启）|
| 可观测 | SRE Agent 自监控 SLI/SLO（诊断成功率/MTTR/工具超时率/回滚率）|
| NAT 集成 | NeMo Agent Toolkit 包裹 LangGraph：profiling + 诊断准确率评估（修复 G-13）|
| 高可用 | sre_agent 多副本 + Redis 分布式锁 + ha/leader.py 选主 |
| 幂等 | 重复告警去重 + 重复审批/修复请求去重 |

### 里程碑与并行 Track

```
Week 13-14 [Sprint 7 — 安全加固]
─────────────────────────────────────────────────────────────────────
Track A │ 完整 JWT/OIDC 鉴权 + RBAC 三角色（admin/operator/viewer）
        │ + auth/secrets.py（K8s Secret/Vault）+ 凭据自动轮换
Track B │ AuditLogger: hash chain + S3/OSS 远端双写 + HMAC 签名
Track C │ slo/metrics.py: Prometheus 暴露诊断步数/工具失败率/回滚率/审批时延
        │ + slo/degradation.py: 自动降级策略
        │ + nat/ 集成: NeMo Agent Toolkit profiling 包裹 LangGraph + eval 运行
Track D │ GUI: 用户登录页 + 权限展示 + 审计日志查看 + SLI/SLO 仪表盘
Track E │ 安全测试: 命令注入/表达式注入/未授权访问三类测试全通过

Week 15-16 [Sprint 8 — 高可用与幂等]
─────────────────────────────────────────────────────────────────────
Track A │ ha/heartbeat.py + ha/leader.py（Redis Redlock 选主）
        │ sre_agent 多副本部署 + 故障切换验证
Track B │ lifecycle/data_lifecycle.py: 数据归档/清理策略
        │ + 告警风暴抑制（AlertCorrelator 时间窗口聚合）
Track C │ Helm Chart 生产部署包（load_simulator/fault_injector/sre_agent 各一个）
Track D │ GUI: 系统健康总览 + SLO 趋势图 + 多副本状态展示
Track E │ 端到端回归测试（含安全、幂等、HA 故障切换场景）
```

### Prod Phase 1 交付物

- [ ] 三类安全测试全部通过（命令注入/表达式注入/未授权访问）
- [ ] RBAC 三角色覆盖所有 API + WebSocket
- [ ] 防篡改审计日志 hash chain 验证
- [ ] SLI/SLO Prometheus 指标 + Grafana 看板
- [ ] NAT profiling + eval_dataset 评估报告（诊断准确率 baseline）
- [ ] HA：sre_agent 双副本，单副本故障 < 30s 切换
- [ ] Helm Chart 生产部署完整文档

---

## Prod Phase 2（第 7–8 月）

### 目标：智能化提升

| 维度 | 内容 |
|------|------|
| 多 LLM 路由 | 按告警类型/复杂度动态选模型 + 成本预算控制 |
| 实时拓扑 | K8s Informer watch 替代定时全量刷新（Pod 级实时）|
| Runbook 自动生成 | 从高置信度 LearnedPattern 生成 Runbook YAML → 写回知识库 |
| 联邦记忆（MVP） | 多 AIDC 间匿名化模式记忆离线同步协议 |
| 对话助手增强 | 滑动窗口对话历史（max=50）+ 摘要化（修复 P2-7）|

### 里程碑

```
Week 17-18 [Sprint 9]
Track A │ LLM 路由器（告警类型→模型选择 + token 预算）+ LLM 健康探测
Track B │ K8s Informer 实时拓扑增量更新（替换定时全量扫描）
Track C │ 对话历史滑动窗口 + 摘要化压缩（修复P2-7）
Track D │ GUI: 多 LLM 状态 + 实时拓扑动画
Track E │ 性能优化（BFS deque/LogChannel并发/向量检索连接池）

Week 19-20 [Sprint 10]
Track A │ Runbook 自动生成 Pipeline（LearnedPattern→YAML→ChromaDB）
Track B │ 联邦记忆离线同步 CLI 工具（匿名化导出/导入）
Track C │ 记忆置信度时间衰减因子（防止过期模式永久高权重）
Track D │ GUI: Runbook 自动生成查看 + 联邦记忆导入导出
Track E │ 集成测试: Runbook 命中率对比 POC baseline
```

### Prod Phase 2 交付物

- [ ] 多 LLM 路由上线，成本降低 ≥ 30%（vs 全量强模型）
- [ ] 实时拓扑延迟 ≤ 30s（K8s Pod 事件触发）
- [ ] Runbook 自动生成覆盖 ≥ 50% 高频场景
- [ ] 联邦记忆 CLI 工具可用
- [ ] 对话助手多步 function-calling E2E 通过

---

## Prod Phase 3（第 9–10 月）

### 目标：企业级运营

| 维度 | 内容 |
|------|------|
| 多集群联邦 | 统一管理平面，多 AIDC 注册/切换/跨集群诊断 |
| 灾备策略 | WAL/DB 损坏自动恢复演练 + SOP 自动化 |
| 容量规划 | 历史诊断数据→资源趋势预测（GPU/网络）|
| 合规审计 | 等保/GDPR 审计报告生成 + 数据保留策略 |
| Skills 市场 | YAML schema 版本化 + 签名校验 + 社区贡献 CI/CD |

### 里程碑

```
Week 21-22 [Sprint 11]
Track A │ 多集群注册/管理 API + 跨集群 Channel 路由
Track B │ 灾备演练框架（定期注入 WAL 损坏验证恢复）
Track C │ 容量趋势分析（时序→Prophet预测→阈值建议）
Track D │ GUI: 多集群总览 + 容量规划仪表盘
Track E │ 合规审计报告生成（PDF/Excel模板）

Week 23-24 [Sprint 12]
Track A │ Skills 市场（SKILL.md 版本化 + HMAC 签名校验）
Track B │ 社区贡献 Skills CI/CD（提交→沙箱验证→发布）
Track C │ 全套端到端回归（所有 case + 灾备 + 多集群）
Track D │ GUI: Skills 市场页面 + 版本历史
Track E │ 最终生产文档 + 运营手册 + SLO 验收报告
```

### Prod Phase 3 交付物

- [ ] 多集群管理平面 ≥ 2 个 AIDC 注册
- [ ] 灾备演练自动化通过（WAL/DB 损坏恢复 SOP）
- [ ] 容量预测 backtesting MAPE ≤ 20%
- [ ] 合规审计报告模板完整
- [ ] Skills 市场 ≥ 10 个内置 Skill + 社区贡献流程文档
- [ ] 全套 E2E 回归测试通过

---

## 并行开发 Session 分工

### Demo 阶段（8 个 Session）

| Session | Track | 主目录 | 外部依赖 |
|---------|-------|--------|---------|
| S1 | A - load_simulator | `load_simulator/` | 无 |
| S2 | B - fault_injector 基础 | `fault_injector/` (channels/safety/orchestrator) | 无 |
| S3 | B - fault_injector 场景 | `fault_injector/scenarios/` | S2 channels 完成 |
| S4 | C - sre_agent core | `sre_agent/agent/` + `guardrails/` + `tools/` + `skills/` | 无 |
| S5 | C - sre_agent api + remediation | `sre_agent/api/` + `remediation/` + `concurrency/` | S4 agent 骨架 |
| S6 | D - Frontend | `sre_agent/frontend/` | S5 API 接口定义 |
| S7 | E - Storage + Ontology | `sre_agent/storage/` + `ontology/` | 无 |
| S8 | E - 文档 & 测试 | `docs/` + `tests/` | S1-S7 功能稳定后 |

### Session 间接口契约

```yaml
# S1 → S2/S3: load-simulator stdout JSON 格式（联动契约 §9.2）
load_simulator_json_output:
  exit_code: 0          # 成功
  summary:
    session_id: str
    duration_seconds: int
    scenarios: list[{name, status, metrics}]
    bottlenecks: list[{layer, metric, value, threshold}]
  # stdout 输出此 JSON，fault_injector 通过 json.loads(stdout) 解析

# S4 → S5: 诊断会话模型
diagnosis_session:
  id: str
  alert: Alert          # Pydantic BaseModel（非 @dataclass，修复P1-3）
  status: "pending|diagnosing|concluded|remediating|done|failed"
  diagnosis_certainty: "confirmed|probable|ambiguous"
  candidates: list[RootCauseCandidate]
  thinking_trace: list[ThinkingStep]  # dataclasses.asdict()序列化（修复P2-6）

# S5 → S6: WebSocket 推送格式
ws_event:
  type: "step|conclusion|approval_required|loop_start|loop_progress|remediation_progress|done"
  session_id: str
  payload: dict

# S7 → S4: 存储接口
storage_interface:
  knowledge:
    search(query: str, top_k: int=5) -> list[Document]
  memory:
    query_similar(alert_signature: str) -> list[IncidentRecord]
    update_pattern_confidence(pattern_id, outcome: "resolved"|"failed")
  ontology:
    get_blast_radius(entity_id: str) -> dict[str, {entity, hops}]
    find_entities(type: str, filters: dict) -> list[dict]
```

### 合并策略

- 每个 Session 在 feature branch 开发：`claude/<track>-<sprint>-<id>`
- Sprint 末通过 PR 合并到 `claude/phased-development-plan-ZTWRB`
- PR 合并前必须通过：① 单元测试；② 接口契约测试；③ 无 P0 安全问题

---

## 附录

### 附录 A：精确场景名索引（对照设计文档）

| 分类 | 场景名（scenario key） | 所属文件 | 设计文档位置 |
|------|----------------------|---------|------------|
| vLLM 延迟 | `gpu_contention` | `vllm_latency.py` | fault-injector §7.1 RC-1 |
| vLLM 延迟 | `network_jitter` | `vllm_latency.py` | fault-injector §7.1 RC-2 |
| vLLM 延迟 | `storage_io_interference` | `vllm_latency.py` | fault-injector §7.1 RC-3 |
| vLLM 延迟 | `platform_cascade` | `vllm_latency.py` | fault-injector §7.1 RC-4 |
| vLLM 延迟 | `os_resource_pressure` | `vllm_latency.py` | fault-injector §7.1 RC-5 |
| vLLM 延迟 | `thermal_throttling` | `vllm_latency.py` | fault-injector §7.1 RC-6 |
| RDMA 异常 | `pfc_deadlock` | `rdma_anomaly.py` | fault-injector §8.1 F-1 |
| RDMA 异常 | `ecn_misconfiguration` | `rdma_anomaly.py` | fault-injector §8.1 F-2 |
| RDMA 异常 | `rdma_load_imbalance` | `rdma_anomaly.py` | fault-injector §8.1 F-3 |
| RDMA 异常 | `rdma_link_flap` | `rdma_anomaly.py` | fault-injector §8.1 F-4 |
| RDMA 异常 | `roce_mtu_mismatch` | `rdma_anomaly.py` | fault-injector §8.1 F-5 |
| RDMA 异常 | `rdma_qos_downgrade` | `rdma_anomaly.py` | fault-injector §8.1 F-6 |

### 附录 B：技术栈（与设计文档对齐）

| 组件 | 技术 | 版本 | 说明 |
|------|------|------|------|
| Python | 3.11+ | — | sre_agent 要求 3.11+；load/fault 用 3.9+ |
| Agent 框架 | LangChain + LangGraph | 0.3+ | ReAct StateGraph + tool calling |
| Agent 安全 | NeMo Guardrails | 0.11+ | Colang DSL，4 类 rails |
| Agent 可观测 | NeMo Agent Toolkit (`nvidia-nat`) | 1.4+ | 仅生产基础设施层，不做编排 |
| LLM | MiniMax-2.1（主）/ Claude（备） | — | OpenAI 兼容协议 |
| 向量存储 | ChromaDB + `asyncio.to_thread` | 0.4+ | 同步库异步包装 |
| 图存储 | NetworkX + aiosqlite | — | Ontology，BFS 用 `collections.deque` |
| HTTP 客户端 | httpx (async) | 0.27+ | 所有 HTTP 调用 |
| SSH | asyncssh | 2.14+ | 异步 SSH，连接池 |
| Web API | FastAPI + uvicorn | 0.110+ | REST + WebSocket |
| **前端框架** | **React 18 + TypeScript** | — | — |
| **UI 库** | **Ant Design 5** | — | — |
| **拓扑图** | **D3.js force-directed** | — | — |
| **状态管理** | **Zustand**（非 MobX） | — | 轻量，适合 React 18 |
| **构建工具** | **Vite**（非 webpack） | — | 更快的 HMR |
| 配置 | Pydantic v2 + PyYAML | 2.5+ | 三组件统一 |
| 测试 | pytest + pytest-asyncio + Playwright | — | 单元 + E2E |

### 附录 C：安全条件 DSL（替换 eval，修复 P0-1）

```python
import operator as op, re
from typing import Any

SAFE_OPERATORS = {
    "<": op.lt, ">": op.gt, "<=": op.le,
    ">=": op.ge, "==": op.eq, "!=": op.ne,
}

def safe_eval(condition: str, variables: dict[str, Any]) -> bool:
    """安全条件求值，仅支持 'field op literal' 三元组。"""
    m = re.match(r'^(\w+)\s*(<=|>=|==|!=|<|>)\s*(.+)$', condition.strip())
    if not m:
        raise ValueError(f"不合法条件: {condition!r}")
    field, op_str, literal_str = m.groups()
    if field not in variables:
        raise KeyError(f"未知变量: {field!r}")
    try:
        literal: Any = float(literal_str)
        if literal.is_integer():
            literal = int(literal)
    except ValueError:
        literal = literal_str.strip("'\"")
    return SAFE_OPERATORS[op_str](variables[field], literal)
```

### 附录 D：版本发布节奏

| 阶段 | 时间 | 版本 | 发布物 |
|------|------|------|--------|
| Demo | 第 1–2 月 | v0.1.0-demo | Docker Compose + 文档 + Demo 脚本 |
| POC | 第 3–4 月 | v0.2.0-poc | 全场景包 + 压测报告 + 回滚验证报告 |
| Prod Phase 1 | 第 5–6 月 | v1.0.0 | Helm Chart + 安全审计 + NAT 评估报告 |
| Prod Phase 2 | 第 7–8 月 | v1.1.0 | 多 LLM 路由 + 联邦记忆 + Runbook 生成 |
| Prod Phase 3 | 第 9–10 月 | v2.0.0 | 多集群 + Skills 市场 + 合规报告 |
