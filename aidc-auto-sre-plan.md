# AIDC Auto-SRE 分阶段开发计划

> 文档版本：v1.0 | 创建日期：2026-02-18 | 分支：claude/phased-development-plan-ZTWRB

---

## 目录

- [背景与目标](#背景与目标)
- [组件总览](#组件总览)
- [Demo 阶段（第 1–2 月）](#demo-阶段第-12-月)
- [POC 阶段（第 3–4 月）](#poc-阶段第-34-月)
- [Prod Phase 1（第 5–6 月）](#prod-phase-1第-56-月)
- [Prod Phase 2（第 7–8 月）](#prod-phase-2第-78-月)
- [Prod Phase 3（第 9–10 月）](#prod-phase-3第-910-月)
- [并行开发 Session 分工说明](#并行开发-session-分工说明)

---

## 背景与目标

AIDC Auto-SRE 是面向 AI 数据中心的自主运维智能体系统，通过 LangGraph ReAct + NeMo Guardrails 实现告警驱动的根因诊断与受控修复。整体由三个组件构成：

| 组件 | 职责 | 优先级 |
|------|------|--------|
| `load_simulator` | 生成确定性推理/训练负载，验证系统性能瓶颈 | 前置依赖，最优先 |
| `fault_injector` | 多维度故障注入（硬件/OS/平台/服务），驱动 Demo 场景 | 前置依赖，最优先 |
| `sre_agent` | LangGraph ReAct 诊断 + NeMo 受控修复 + GUI | 核心交付物 |

**开发原则**
- LLM 只做只读分析（观察/诊断），永远不直接驱动写操作
- 确定性引擎（asyncio）负责注入/恢复/执行，WAL 保证可回滚
- 每阶段可并行开发，多 Session 独立交付后合并

---

## 组件总览

```
cube-studio/
├── load_simulator/          # 负载模拟器（前置依赖）
├── fault_injector/          # 故障注入器（前置依赖）
├── sre_agent/               # Auto-SRE 智能体
│   ├── agent/               # LangGraph ReAct 核心
│   ├── guardrails/          # NeMo Guardrails 安全层
│   ├── channels/            # 统一 Channel 抽象层
│   ├── tools/               # 工具注册（read_only / write）
│   ├── storage/             # Ontology / Knowledge / Memory
│   ├── remediation/         # 修复引擎（WAL + canary）
│   ├── api/                 # FastAPI REST + WebSocket
│   └── frontend/            # React 18 + Ant Design GUI
├── docs/                    # 部署文档、Demo 步骤、Config 说明
└── tests/                   # 单元测试 + E2E 测试
```

---

## Demo 阶段（第 1–2 月）

### 目标

| 维度 | 内容 |
|------|------|
| 场景覆盖 | Case 1: p95 vLLM 推理延迟异常（2 个根因）；Case 2: RDMA 网络异常（2 个根因） |
| GUI | 展示 Skills 管理、知识库检索、记忆库查询 3 个功能模块 |
| 文档 | 部署手册、Demo 步骤、Config 说明 |
| 测试 | 单元测试覆盖率 ≥ 70%，E2E 测试跑通 2 个完整 case |

### Demo Case 说明

#### Case 1：p95 vLLM 推理延迟异常

| | 根因 | 注入方式 | 诊断关键工具 |
|--|------|----------|-------------|
| RC-1 | GPU 显存压力：同节点 `gpu-burn` 进程抢占显存，导致 KV cache 驱逐、排队堆积 | `fault_injector` SSH 启动 gpu-burn 进程 | `get_gpu_processes` + `query_prometheus(vllm_queue_size)` |
| RC-2 | 网络带宽瓶颈：节点出口限速，prefill/decode 跨节点 KV 传输超时，p95 spike | `fault_injector` tc netem 限速 100Mbps | `query_prometheus(node_network_transmit_bytes)` + `get_pod_logs` |

**诊断轨迹（10 步，RC-1 示例）**
```
Step 1: query_prometheus → vllm_request_latency_p95 > 2000ms [触发]
Step 2: get_topology → 定位 inference-pod 所在 Node=gpu-1-1
Step 3: query_prometheus → gpu_utilization{node=gpu-1-1} = 98%
Step 4: query_prometheus → gpu_memory_used{node=gpu-1-1} > 95%
Step 5: get_gpu_processes(node=gpu-1-1) → 发现 gpu-burn PID=12345
Step 6: search_knowledge("GPU memory pressure vllm") → 匹配 Runbook KB-042
Step 7: search_memory("gpu_memory_pressure") → 历史事件 incident-2024-11-03 已解决
Step 8: [结论] 根因 = gpu-burn 进程抢占显存; 置信度 0.92
Step 9: [建议修复] kill_process(node=gpu-1-1, pid=12345) [需人工审批]
Step 10: 审批通过 → 执行 → 验证 p95 < 500ms → 写入记忆库
```

#### Case 2：RDMA 网络异常

| | 根因 | 注入方式 | 诊断关键工具 |
|--|------|----------|-------------|
| RC-1 | 交换机端口 flapping：物理层 UP/DOWN 抖动，RDMA QP 重建风暴，训练吞吐骤降 | `fault_injector` 通过 SSH CLI 对 H3C 交换机 shutdown/no shutdown 端口 | `get_switch_port_status` + `query_prometheus(rdma_port_rcv_errors)` |
| RC-2 | RDMA QP 资源耗尽：异常 Job 未释放 QP，新训练任务建连失败 | `fault_injector` SSH 启动占用大量 QP 的程序 | `get_rdma_qp_stats(node)` + `get_pod_logs(training-job)` |

### 里程碑与并行 Track

```
Week 1-2  [Sprint 1 - 基础骨架]
  Track A │ load_simulator: CLI骨架 + InferenceAgent + PrometheusChannel
  Track B │ fault_injector: CLI骨架 + SSHChannel + 安全守卫 + WAL
  Track C │ sre_agent: LangGraph骨架 + tool注册 + 安全条件DSL(替换eval)
  Track D │ GUI: React项目初始化 + 路由 + 布局组件
  Track E │ 基础设施: Docker Compose + 配置Schema + CI骨架

Week 3-4  [Sprint 2 - 核心功能]
  Track A │ load_simulator: 负载曲线 + 指标采集 + 瓶颈分析LLM
  Track B │ fault_injector: vLLM延迟场景(RC-1/RC-2) + OSFaultAgent
  Track C │ sre_agent: ReAct循环 + NeMo Guardrails + get_gpu_processes工具(修复P0-5)
  Track D │ GUI: Skills页面 + 知识库搜索页面
  Track E │ storage: ChromaDB知识库 + aiosqlite记忆库 + Ontology初始化

Week 5-6  [Sprint 3 - 场景集成]
  Track A │ load_simulator: HTML报告 + fault联动接口(读report.json, 修复P0-1)
  Track B │ fault_injector: RDMA场景(RC-1/RC-2) + KubernetesChannel + 回滚验证
  Track C │ sre_agent: 诊断会话API + WebSocket思考流 + 修复审批流程(修复P0-4)
  Track D │ GUI: 记忆库页面 + 诊断流实时展示 + 审批对话框
  Track E │ 知识库种子数据导入 + 拓扑发现(infer_topology hostname匹配，修复P2-5)

Week 7-8  [Sprint 4 - 测试与文档]
  Track A │ load_simulator: 单元测试 + E2E测试
  Track B │ fault_injector: 单元测试 + E2E测试 + 安全注入测试
  Track C │ sre_agent: 单元测试 + RBAC鉴权(修复P0-3) + shlex注入防护(修复P0-2)
  Track D │ GUI: 前端测试 + Demo彩排支持
  Track E │ 完整文档: 部署手册 + Demo步骤 + Config说明 + E2E测试套件
```

### 前置条件与修复 P0 清单

在 Sprint 1 开始前，以下 P0 问题必须在设计层面确认修复方案：

| ID | 问题 | 修复方案 | 负责 Track |
|----|------|----------|-----------|
| P0-1 | load-simulator ↔ fault-injector 联动接口不一致 | 统一为 fault-injector 读取 `report/report.json` | Track A+B |
| P0-2 | SSH 命令拼接注入 (`log_path`/`filter_str` 未转义) | 所有 SSH 命令参数使用 `shlex.quote()`；禁止管道字符 | Track C |
| P0-3 | API/WebSocket 缺鉴权 | 统一 JWT 中间件 + 接口级 RBAC 注解 | Track C |
| P0-4 | `asyncio.Queue.get(timeout=...)` 不存在 | 改为 `await asyncio.wait_for(queue.get(), timeout=300)` | Track C |
| P0-5 | Demo Trace Step 5 调用工具与定义不匹配 | 新增 `get_gpu_processes` 只读工具 | Track C |

### 测试策略（Demo 阶段）

#### 单元测试（各 Track 自测，pytest）

| 模块 | 关键测试点 |
|------|-----------|
| `load_simulator/metrics/` | P50/P95/P99 聚合正确性；阈值检测 |
| `load_simulator/load/` | 令牌桶速率控制精度；负载曲线生成 |
| `fault_injector/safety/` | 禁止操作硬编码拦截；WAL 写入顺序（先写恢复命令再注入） |
| `fault_injector/scenarios/` | vLLM RC-1/RC-2 注入步骤幂等性；RDMA RC-1/RC-2 回滚验证 |
| `sre_agent/agent/` | ReAct 循环工具调用正确路由；NeMo rail 拦截危险输入 |
| `sre_agent/storage/` | 知识库 RAG 检索 top-k 返回非空；记忆置信度更新（成功+0.15/失败-0.1）|
| `sre_agent/remediation/` | safe_eval 条件求值白名单；WAL 回滚顺序 |

#### E2E 测试（Sprint 4，集成环境）

```
test_e2e_case1_rc1.py   # 启动gpu-burn → load_simulator触发p95告警 → sre_agent诊断RC-1 → 审批修复 → 验证恢复
test_e2e_case1_rc2.py   # tc限速 → load_simulator触发p95告警 → sre_agent诊断RC-2 → 验证恢复
test_e2e_case2_rc1.py   # 交换机端口flapping → RDMA异常 → sre_agent诊断RC-1 → 验证恢复
test_e2e_case2_rc2.py   # QP耗尽 → 训练失败 → sre_agent诊断RC-2 → 验证恢复
test_e2e_gui_skills.py  # GUI Skills页面：加载/搜索/调用
test_e2e_gui_knowledge.py  # GUI 知识库：上传/检索/关联诊断
test_e2e_gui_memory.py  # GUI 记忆库：查询历史事件/置信度展示
```

### Demo 文档清单

```
docs/demo/
├── 01-deployment.md        # 部署方式：Docker Compose 本地 + K8s 生产
├── 02-demo-steps.md        # Demo 步骤（Case 1 + Case 2 完整脚本）
├── 03-config-reference.md  # 所有配置项说明（load_simulator/fault_injector/sre_agent）
├── 04-troubleshooting.md   # 常见问题排查
└── 05-architecture.md      # 架构图 + 组件交互时序图
```

### Demo 交付物检查清单

- [ ] `load_simulator` CLI 可运行，生成 `report/report.json`
- [ ] `fault_injector` 可注入 vLLM RC-1/RC-2、RDMA RC-1/RC-2 并自动回滚
- [ ] `sre_agent` 能端到端诊断 2 个 case，每个 case 定位 2 个根因
- [ ] GUI 展示 Skills / 知识库 / 记忆库 3 个功能模块
- [ ] 所有 P0 安全问题已修复
- [ ] 单元测试覆盖率 ≥ 70%
- [ ] 4 个 E2E 场景测试通过
- [ ] 完整文档（部署 + Demo 步骤 + Config）

---

## POC 阶段（第 3–4 月）

### 目标

在 Demo 基础上扩展场景广度、强化修复流程、引入灰度验证，验证系统在准生产环境的可靠性。

| 维度 | Demo | POC 新增 |
|------|------|---------|
| 根因数/场景 | 2 | 4–6（覆盖 fault-injector 设计的 6 个 RC） |
| 修复流程 | 人工审批 | 灰度部署（canary）+ 自动回滚验证 |
| 拓扑 | 单集群静态 | 多集群拓扑动态发现（K8s Informer）|
| 知识库 | 种子数据 | Runbook 自动摄入 + 增量更新 |
| 记忆库 | 基础读写 | 模式学习 + 跨会话置信度传播 |
| 并发 | 单诊断会话 | ≤ 5 并发诊断会话 |

### 里程碑与并行 Track

```
Week 9-10  [Sprint 5 - 扩展场景]
  Track A │ fault_injector: vLLM RC-3至RC-6场景实现（显存泄漏/调度器故障/存储IO/CPU抢占）
  Track B │ fault_injector: RDMA RC-3至RC-6场景（MTU不匹配/固件缺陷/PCIe故障/ECMP不均衡）
  Track C │ sre_agent: 修复引擎 canary + 灰度执行器 + WAL 幂等恢复
  Track D │ GUI: 修复审批详情页 + canary 进度可视化 + 拓扑图(D3.js)
  Track E │ 知识库: Runbook Markdown 批量摄入流水线 + 文档版本管理

Week 11-12 [Sprint 6 - 可靠性]
  Track A │ load_simulator: 多场景并发压测 + 历史报告对比功能
  Track B │ fault_injector: 组合故障场景（同时注入多层故障）+ 回滚成功率≥99%验证
  Track C │ sre_agent: 并发诊断会话(Semaphore限制) + ReAct timeout双层控制(修复P1-6)
  Track D │ GUI: 多会话诊断列表 + 实时拓扑变更推送
  Track E │ 性能测试: 1000次模拟告警回放 + 无死锁验证 + aiosqlite异步重构(修复P1-10)
```

### 新增测试内容

| 类型 | 新增内容 |
|------|---------|
| 单元测试 | canary 成功/失败判定逻辑；组合故障优先级排序；并发诊断 Semaphore 边界 |
| 集成测试 | K8s Informer 拓扑实时更新；Runbook RAG 命中率（人工标注 golden set）|
| 压力测试 | 1000 次告警回放：无死锁、无未恢复会话、内存无泄漏 |
| 回滚测试 | 注入 10 类失败场景：WAL 回滚成功率 ≥ 99% |

### POC 交付物

- [ ] 全部 6 个 vLLM 根因场景 + 6 个 RDMA 根因场景可注入/诊断
- [ ] 修复引擎支持 canary 灰度 + 自动回滚
- [ ] 并发诊断会话 ≤ 5，无死锁
- [ ] 1000 次告警回放通过
- [ ] WAL 回滚成功率 ≥ 99%
- [ ] 拓扑图在 GUI 可视化展示

---

## Prod Phase 1（第 5–6 月）

### 目标

生产安全加固、多租户隔离、系统可观测性、高可用部署。

| 维度 | POC | Prod Phase 1 新增 |
|------|-----|-----------------|
| 认证 | JWT 基础 | JWT/OIDC + token 轮换 + 租户隔离 |
| 审计 | 基础日志 | 防篡改审计日志（签名 + 远端归档） |
| 可观测 | 无 | 系统自监控 SLI/SLO（诊断成功率/MTTR/工具超时率）|
| 密钥管理 | 环境变量 | Vault/K8s Secret + 自动轮换 |
| 高可用 | 单节点 | sre_agent HA 部署（多副本 + 状态持久化）|
| TLS | verify_ssl=false | 默认开启 TLS 验证 |

### 里程碑与并行 Track

```
Week 13-14 [Sprint 7 - 安全加固]
  Track A │ 完整 JWT/OIDC 鉴权 + RBAC 角色管理（admin/operator/viewer）
  Track B │ 防篡改审计日志（HMAC 签名 + 写入远端对象存储）
  Track C │ Secret 管理：K8s Secret / Vault 集成 + 凭据轮换
  Track D │ GUI: 用户登录/权限展示/审计日志查看页面
  Track E │ 安全测试：命令注入/表达式注入/未授权访问三类测试全部通过

Week 15-16 [Sprint 8 - 可观测与高可用]
  Track A │ SRE Agent 自监控指标（Prometheus metrics）：诊断步数/工具失败率/回滚率/审批时延
  Track B │ SRE Agent HA：多副本 + Redis 分布式锁 + 会话状态持久化（可 resume）
  Track C │ 幂等机制：重复告警/重复审批/重复修复去重（幂等键）
  Track D │ GUI: 系统健康仪表盘 + SLI/SLO 展示
  Track E │ K8s Helm Chart 生产部署包 + 升级回滚文档
```

### Prod Phase 1 交付物

- [ ] 三类安全测试全部通过（命令注入/表达式注入/未授权访问）
- [ ] RBAC 三角色（admin/operator/viewer）覆盖所有 API
- [ ] 防篡改审计日志上线
- [ ] SLI/SLO Prometheus 指标已暴露并配置 Grafana 看板
- [ ] HA 部署：sre_agent 多副本，单副本故障不影响在途诊断
- [ ] 幂等处理：重复告警去重率 100%
- [ ] Helm Chart 生产部署包文档完整

---

## Prod Phase 2（第 7–8 月）

### 目标

智能化提升：多 LLM 路由、联邦记忆、Runbook 自动生成、实时拓扑同步。

| 维度 | 内容 |
|------|------|
| 多 LLM 路由 | 按诊断复杂度/成本自动选择模型（简单告警→轻量模型，复杂根因→强模型）|
| 实时拓扑 | K8s Informer watch 替代定时全量刷新，Pod 级别实时拓扑 |
| Runbook 自动生成 | 从高置信度 LearnedPattern 自动生成 Runbook YAML，写入知识库 |
| 联邦记忆（MVP） | 多 AIDC 间匿名化模式记忆共享协议（离线同步版） |
| 对话助手 | 完整 ConversationalAgent function-calling loop（修复 P1-8） |

### 里程碑与并行 Track

```
Week 17-18 [Sprint 9 - 智能路由与实时拓扑]
  Track A │ LLM 路由器：按告警类型/历史响应时间动态选模型 + 成本预算控制
  Track B │ K8s Informer 实时拓扑同步 + 图增量更新（替代定时全量扫描）
  Track C │ 对话助手：完整 function-calling loop + 对话历史滑动窗口(max=50轮)
  Track D │ GUI: 多 LLM 状态展示 + 实时拓扑动画
  Track E │ 性能优化: BFS 改 deque / LogChannel 并发拉取 / 向量检索连接池

Week 19-20 [Sprint 10 - 知识闭环]
  Track A │ Runbook 自动生成 Pipeline（LearnedPattern → YAML → ChromaDB 写回）
  Track B │ 联邦记忆离线同步协议（匿名化导出/导入格式定义 + CLI 工具）
  Track C │ 记忆置信度衰减（时间因子 + 负向反馈修复，应用 P1-9 修复）
  Track D │ GUI: Runbook 自动生成查看 + 联邦记忆导入导出
  Track E │ 集成测试：Runbook 命中率提升验证（与 POC baseline 对比）
```

### Prod Phase 2 交付物

- [ ] LLM 路由器上线，成本相比全量使用强模型降低 ≥ 30%
- [ ] 实时拓扑延迟 ≤ 30s（K8s Pod 事件触发）
- [ ] Runbook 自动生成命中率（覆盖 ≥ 50% 高频场景）
- [ ] 联邦记忆离线同步 CLI 工具可用
- [ ] 对话助手多步 function-calling 场景通过 E2E 测试

---

## Prod Phase 3（第 9–10 月）

### 目标

企业级运营能力：多集群联邦、灾备、容量规划集成、合规审计。

| 维度 | 内容 |
|------|------|
| 多集群联邦 | 统一管理平面，多 AIDC 集群注册/切换/跨集群诊断 |
| 灾备策略 | WAL 损坏/DB 损坏/部分回滚失败的恢复 SOP + 自动化演练 |
| 容量规划 | 基于历史诊断数据的资源趋势分析（GPU 显存/网络带宽） |
| 合规审计 | 满足等保/GDPR 要求的审计报告生成 + 数据保留策略 |
| 插件市场 | Skills 热加载市场（内置 + 社区贡献），版本管理 |

### 里程碑与并行 Track

```
Week 21-22 [Sprint 11 - 多集群与灾备]
  Track A │ 多集群注册/管理 API + 跨集群 Channel 路由
  Track B │ 灾备 SOP 自动化演练框架（定期注入 WAL 损坏并验证恢复）
  Track C │ 容量趋势分析模块（时序数据 → 线性/Prophet 预测 → 告警阈值建议）
  Track D │ GUI: 多集群拓扑总览 + 容量规划仪表盘
  Track E │ 合规审计报告生成器（模板化 PDF/Excel 导出）

Week 23-24 [Sprint 12 - Skills 市场与收尾]
  Track A │ Skills 热加载市场（YAML schema 版本化 + 签名校验）
  Track B │ 社区贡献 Skills CI/CD pipeline（提交 → 沙箱验证 → 发布）
  Track C │ 端到端回归测试全套：所有 case + 灾备演练 + 多集群
  Track D │ GUI: Skills 市场页面 + 版本历史
  Track E │ 最终生产文档更新 + 运营手册 + SLO 验收报告
```

### Prod Phase 3 交付物

- [ ] 多集群管理平面上线，≥ 2 个 AIDC 注册
- [ ] 灾备演练：WAL/DB 损坏恢复 SOP 自动化通过
- [ ] 容量趋势预测模块上线，精度验证（backtesting MAPE ≤ 20%）
- [ ] 合规审计报告模板完整
- [ ] Skills 市场：≥ 10 个内置 Skill + 社区贡献流程文档
- [ ] 全套 E2E 回归测试通过（含多集群、灾备、容量规划场景）

---

## 并行开发 Session 分工说明

每个 Sprint 可同时开启多个独立 Session，按 Track 分工。以下为推荐的 Session 切分边界：

### Demo 阶段 Session 切分（8 个 Session）

| Session | Track | 主要工作目录 | 依赖 |
|---------|-------|------------|------|
| S1 | Track A - Load Simulator | `load_simulator/` | 无 |
| S2 | Track B - Fault Injector 基础 | `fault_injector/` (safety/ + channels/ + orchestrator/) | 无 |
| S3 | Track B - Fault Injector 场景 | `fault_injector/scenarios/` | S2 完成 channels |
| S4 | Track C - SRE Agent Core | `sre_agent/agent/` + `sre_agent/guardrails/` + `sre_agent/tools/` | 无 |
| S5 | Track C - SRE Agent API | `sre_agent/api/` + `sre_agent/remediation/` | S4 完成 agent 骨架 |
| S6 | Track D - GUI | `sre_agent/frontend/` | S5 API 接口定义完成 |
| S7 | Track E - Storage | `sre_agent/storage/` + `sre_agent/channels/` | 无 |
| S8 | Track E - 文档 & 测试 | `docs/` + `tests/` | S1-S7 功能稳定后 |

### 接口契约（Session 间协调点）

Session 之间需提前对齐以下接口，避免并行开发产生冲突：

```yaml
# S1 ↔ S2/S3: load-simulator 输出格式
load_simulator_output:
  path: report/report.json
  schema:
    session_id: str
    start_time: datetime
    end_time: datetime
    scenarios: list[ScenarioResult]
    bottlenecks: list[Bottleneck]

# S4 ↔ S5: 诊断会话状态模型
diagnosis_session:
  id: str
  alert: Alert (Pydantic BaseModel, 非 dataclass)
  status: "pending|diagnosing|concluded|remediating|done|failed"
  candidates: list[RootCauseCandidate]
  thinking_trace: list[ThinkingStep]

# S5 ↔ S6: WebSocket 推送格式
ws_event:
  type: "step|conclusion|approval_required|remediation_progress|done"
  session_id: str
  payload: dict

# S7 ↔ S4: 存储接口
storage_interface:
  knowledge: search(query: str, top_k: int) -> list[Document]
  memory: query_similar(pattern: str) -> list[IncidentRecord]
  ontology: get_blast_radius(entity_id: str) -> list[Entity]
```

### 合并策略

- 每个 Session 在独立 feature branch 开发：`claude/<track>-<sprint>-<id>`
- Sprint 结束时通过 PR 合并到 `claude/phased-development-plan-ZTWRB`
- 合并前需通过：① 单元测试；② 接口契约测试；③ 无 P0 安全问题

---

## 附录 A：关键技术选型

| 组件 | 选型 | 说明 |
|------|------|------|
| Agent 框架 | LangGraph 0.2+ | ReAct StateGraph，支持工具调用与状态持久化 |
| 安全层 | NeMo Guardrails 0.9+ | Colang DSL，input/output/execution 三层 rails |
| LLM 主力 | MiniMax-2.1 (OpenAI 兼容) | 诊断分析，只读；修复由确定性引擎执行 |
| 向量存储 | ChromaDB（asyncio.to_thread 包装） | 知识库 RAG 检索 |
| 图存储 | NetworkX + aiosqlite | Ontology 拓扑图，aiosqlite 异步持久化 |
| 记忆存储 | aiosqlite | 事件/模式/配置记忆 |
| HTTP 客户端 | httpx (async) | 所有 HTTP 调用 |
| SSH | asyncssh | 异步 SSH，连接池 |
| API 框架 | FastAPI + uvicorn | REST + WebSocket |
| 前端 | React 18 + Ant Design 5 + D3.js | GUI |
| 配置 | Pydantic v2 + PyYAML | 类型安全配置 |
| 测试 | pytest + pytest-asyncio + Playwright | 单元 + E2E |

## 附录 B：安全条件 DSL（替换 eval）

```python
# 替换 AIDC-auto-SRE.md 中的 eval() 风险点（P0-1）
# 白名单格式: "field op literal"，如 "p95_latency < 500", "status == 'Running'"

import operator as op
from typing import Any

SAFE_OPERATORS = {
    "<": op.lt, ">": op.gt, "<=": op.le,
    ">=": op.ge, "==": op.eq, "!=": op.ne,
}

def safe_eval(condition: str, variables: dict[str, Any]) -> bool:
    """安全条件求值，仅支持 'field op literal' 三元组，无任意代码执行风险。"""
    import re
    pattern = r'^(\w+)\s*(<=|>=|==|!=|<|>)\s*(.+)$'
    m = re.match(pattern, condition.strip())
    if not m:
        raise ValueError(f"不合法的条件表达式: {condition!r}")
    field, op_str, literal_str = m.groups()
    if field not in variables:
        raise KeyError(f"未知变量: {field!r}")
    value = variables[field]
    # 类型推断: 尝试数字，否则字符串
    try:
        literal: Any = float(literal_str)
        if literal.is_integer():
            literal = int(literal)
    except ValueError:
        literal = literal_str.strip("'\"")
    return SAFE_OPERATORS[op_str](value, literal)
```

## 附录 C：版本发布节奏

| 阶段 | 时间 | 对应版本 | 发布物 |
|------|------|---------|--------|
| Demo | 第 1–2 月 | v0.1.0-demo | Docker Compose 包 + 文档 + Demo 脚本 |
| POC | 第 3–4 月 | v0.2.0-poc | 扩展场景包 + 压测报告 |
| Prod Phase 1 | 第 5–6 月 | v1.0.0 | Helm Chart + 安全审计报告 |
| Prod Phase 2 | 第 7–8 月 | v1.1.0 | LLM 路由 + 联邦记忆 + Runbook 生成 |
| Prod Phase 3 | 第 9–10 月 | v2.0.0 | 多集群 + Skills 市场 + 合规报告 |
