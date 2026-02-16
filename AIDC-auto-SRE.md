# AIDC Auto SRE Agent 设计文档

## 1. 概述

### 1.1 项目目标

为智算数据中心（AIDC）提供自动化运维 Agent，具备自主根因分析、智能修复、专业指导能力。

### 1.2 架构定位

采用 **"自由诊断、受控修复"** 混合架构，基于 **LangChain/LangGraph + NeMo Guardrails** 实现 Agent 核心：

| 层级 | 技术 | 理由 |
|------|------|------|
| 诊断 Agent | **LangGraph ReAct Agent** + **NeMo Guardrails** | 根因分析需要迭代假设→采集→验证→排除；LangGraph 提供成熟的状态机 + tool calling 循环；NeMo Guardrails 提供输入/输出/执行安全护栏 |
| Agent 安全层 | **NeMo Guardrails**（Colang + Rails） | 输入防注入、输出防泄密、工具调用验证、话题范围约束，替代手写安全检查 |
| Agent 可观测 | **NeMo Agent Toolkit**（`nvidia-nat`） | Agent 工作流 profiling、token 效率分析、准确性评估、瓶颈识别 |
| 修复引擎 | Python + asyncio（确定性） | 修复操作必须精确、可回滚、可审计，不允许 LLM 幻觉 |
| 发现 Agent | Python（确定性） | 拓扑扫描是结构化的 API 调用，无需推理 |
| 监控 Agent | Python（确定性） | 指标采集、阈值检测是规则驱动 |
| Channel 层 | 类型化 Channel 类 | 统一抽象 SSH/Redfish/K8s/交换机/Prometheus 等后端，与 fault-injector / load-simulator 共享 |

### 1.3 与 fault-injector / load-simulator 的关系

```
                    ┌──────────────────┐
                    │  AIDC Auto SRE   │
                    │  (本文档)         │
                    │  · ReAct 诊断     │
                    │  · 受控修复       │
                    │  · 数字孪生       │
                    │  · 知识库 + 记忆  │
                    └────────┬─────────┘
                             │ 共享 Channel 层
              ┌──────────────┼──────────────┐
              │              │              │
    ┌─────────▼──────┐ ┌────▼────────┐ ┌───▼──────────────┐
    │ fault-injector  │ │ 共享 Channel │ │ load-simulator   │
    │ · 确定性注入     │ │ · SSH       │ │ · 确定性负载      │
    │ · 单次 LLM 诊断 │ │ · Redfish   │ │ · 单次 LLM 瓶颈  │
    └────────────────┘ │ · Switch    │ └──────────────────┘
                       │ · K8s       │
                       │ · CubeStudio│
                       │ · Prometheus│
                       └─────────────┘
```

**关键区别**：fault-injector 和 load-simulator 是确定性执行引擎，LLM 仅做单次只读分析。SRE Agent 的诊断核心需要多步推理（ReAct 循环），因为根因分析本质上是迭代的："当有多种组合根因时，通过 试验→验证→排查 的循环最终定位"。但修复执行仍然是确定性的、WAL 保护的。

**为什么选择 LangChain/LangGraph + NeMo**：手写 ReAct 循环需要自行处理 tool calling 协议、消息管理、错误恢复、流式输出等基础设施。LangGraph 提供了成熟的状态图 + 条件路由 + 工具节点，减少约 60% 的 Agent 基础设施代码。NeMo Guardrails 提供声明式安全护栏（输入过滤、输出审查、工具 I/O 验证），比手写 `SafetyGuard` 更系统化。NeMo Agent Toolkit 仅用于生产基础设施层（profiling/evaluation/deployment），**不用于 Agent 编排**——其 YAML `react_agent` 缺少自定义状态、条件路由、审批门控和 checkpoint 持久化，无法满足 SRE Agent 需求（详见 §18.1 对比分析）。

### 1.4 核心不变量

**LLM 可以自由观测，但永远不直接执行写操作。** 所有修复操作通过确定性修复引擎执行，写操作前必须记录 WAL 回滚条目。此不变量通过 NeMo Guardrails 的执行护栏（execution rails）在工具调用层强制执行。

---

## 2. 系统架构

### 2.1 设计原则

| 原则 | 说明 |
|------|------|
| 自由诊断 | 诊断阶段 LLM 可自由调用所有只读工具，无需人工审批 |
| 受控修复 | 写操作按风险分级：auto_approve / human_confirm / blocked |
| 灰度优先 | 修复默认先在子集（canary）上验证，再扩展到全量 |
| 可回滚 | 所有修复操作通过 WAL 记录恢复命令，支持一键回滚 |
| 越用越智能 | 每次事件诊断结果写入记忆系统，积累 AIDC 专属知识 |
| 可解释 | 完整记录 Agent 思考过程（ThinkingTrace），支持回放 |
| 声明式安全 | 通过 NeMo Guardrails (Colang) 声明式定义安全规则，而非散落在代码中的 if-else |
| 框架标准化 | 使用 LangChain/LangGraph 标准 tool calling 协议，与 LLM 生态兼容 |

### 2.2 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         用户界面层                                       │
│  ┌──────────┐  ┌──────────────┐  ┌───────────┐  ┌───────────────────┐  │
│  │   CLI    │  │  REST API    │  │ WebSocket │  │    GUI (React)    │  │
│  │ (Click)  │  │  (FastAPI)   │  │ 思考流推送 │  │ 拓扑/诊断/修复/对话│  │
│  └────┬─────┘  └──────┬───────┘  └─────┬─────┘  └────────┬──────────┘  │
└───────┼───────────────┼────────────────┼─────────────────┼──────────────┘
        │               │                │                 │
┌───────▼───────────────▼────────────────▼─────────────────▼──────────────┐
│                       Agent 核心层                                       │
│                                                                         │
│  ┌─────────────────── NeMo Guardrails ────────────────────────────┐    │
│  │ Input Rails: 防注入/话题约束/PII 过滤                            │    │
│  │ Execution Rails: 工具 I/O 验证（check tool input/output）        │    │
│  │ Output Rails: 防泄密/幻觉检测/敏感信息脱敏                       │    │
│  └─────────────────────────┬──────────────────────────────────────┘    │
│                             │                                          │
│  ┌──────────────────────── LangGraph StateGraph ─────────────────┐    │
│  │                                                                │    │
│  │   Alert ──→ 构建上下文 ──→ ┌──────────────────────────┐       │    │
│  │                           │  Agent Node (Guarded LLM) │       │    │
│  │                           │  RunnableRails(passthrough)│       │    │
│  │                           │  + LLM.bind_tools()        │       │    │
│  │                           └──────────┬────────────────┘       │    │
│  │                                      │                         │    │
│  │                    ┌─────── tools_condition ──────────┐        │    │
│  │                    ▼                                  ▼        │    │
│  │              ToolNode                          END (结论)      │    │
│  │              (LangChain @tool)                  或 remediate   │    │
│  │                │                                    │          │    │
│  │                └──→ Agent Node (循环)               ▼          │    │
│  │                                               审批门控         │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                        │
│  ┌──── NeMo Agent Toolkit (nvidia-nat) ─ 生产基础设施层 ─────────┐    │
│  │ · Profiling: 逐步耗时/token 效率/瓶颈分析（包裹 LangGraph）    │    │
│  │ · Evaluation: 诊断准确率/轨迹评估/RAG 质量（CI/CD 集成）       │    │
│  │ · 注意：不用于编排，仅包裹 LangGraph Agent（详见 §18.1）       │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                        │
│  ┌───────────────┐  ┌───────────────┐  ┌──────────────┐               │
│  │ Discovery     │  │ Monitor       │  │ Remediation  │               │
│  │ Agent         │  │ Agent         │  │ Engine       │               │
│  │ (确定性)      │  │ (确定性)       │  │ (确定性+WAL) │               │
│  │ · BMC 扫描    │  │ · 指标采集     │  │ · 灰度执行   │               │
│  │ · 交换机发现  │  │ · 阈值检测     │  │ · 逐步验证   │               │
│  │ · K8s 发现    │  │ · 告警触发     │  │ · 自动回滚   │               │
│  └───────┬───────┘  └───────┬───────┘  └──────┬───────┘               │
└──────────┼──────────────────┼─────────────────┼───────────────────────┘
           │                  │                 │
┌──────────▼──────────────────▼─────────────────▼───────────────────────┐
│                       工具层 (LangChain @tool)                         │
│                                                                        │
│  只读工具 (safety_level: read_only)        写工具 (write_confirm)       │
│  · query_prometheus    · get_pod_logs      · restart_pod               │
│  · get_gpu_metrics     · check_rdma_status · scale_deployment          │
│  · get_bmc_health      · query_topology    · configure_ecn             │
│  · search_knowledge    · search_incidents  · reset_switch_port         │
│  · get_gpu_processes   (新增: P0 修复)                                 │
└──────────────────────────────┬────────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────────┐
│                       Channel 层（共享）                                │
│                                                                        │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ ┌──────────────┐     │
│  │  SSH    │ │ Redfish  │ │ Switch   │ │ K8s  │ │ CubeStudio   │     │
│  │ Channel │ │ Channel  │ │ Channel  │ │Channel│ │  Channel     │     │
│  └────┬────┘ └────┬─────┘ └────┬─────┘ └──┬───┘ └──────┬───────┘     │
│       │           │            │           │            │             │
│  ┌────┴────┐ ┌────┴─────┐ ┌───┴────┐ ┌───┴─────┐ ┌───┴────────┐    │
│  │Prometheus│ │  Log     │ │ Alert  │ │Ontology │ │ Knowledge  │     │
│  │ Channel │ │ Channel  │ │Channel │ │ Channel │ │Base Channel│     │
│  └─────────┘ └──────────┘ └────────┘ └─────────┘ └────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────────┐
│                       数据层                                           │
│                                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐     │
│  │ 数字孪生      │  │ 知识库        │  │ 记忆系统                  │     │
│  │ (Ontology)   │  │ (RAG)        │  │ · 事件记忆                │     │
│  │ aiosqlite +  │  │ ChromaDB     │  │ · 模式记忆                │     │
│  │ NetworkX     │  │ 向量检索      │  │ · 配置记忆                │     │
│  └──────────────┘  └──────────────┘  │ aiosqlite per-AIDC       │     │
│                                       └──────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.3 Agent 角色

| Agent | 类型 | 职责 | LLM? | 框架 |
|-------|------|------|------|------|
| SRE Agent | LangGraph ReAct | 接收告警/问题，迭代诊断根因，生成修复计划 | 是（多步推理） | LangGraph + NeMo Guardrails |
| Conversational Agent | LangGraph ReAct | 对话式 SRE 助手，支持工具调用 | 是 | LangGraph + NeMo Guardrails |
| Discovery Agent | 确定性 | 扫描 BMC/交换机/K8s/Prometheus，构建数字孪生 | 否 | Python asyncio |
| Monitor Agent | 确定性 | 持续采集指标，阈值检测，触发告警 | 否 | Python asyncio |
| Remediation Engine | 确定性 | 执行修复计划，灰度部署，WAL 回滚 | 否 | Python asyncio |

### 2.4 技术栈

| 组件 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 语言 | Python | 3.11+ | — |
| 异步框架 | asyncio | stdlib | — |
| **Agent 框架** | **LangChain + LangGraph** | 0.3+ / 0.3+ | ReAct 状态图、tool calling、消息管理 |
| **Agent 安全** | **NeMo Guardrails** (`nemoguardrails`) | 0.11+ | 输入/输出/执行护栏、Colang 声明式规则 |
| **Agent 可观测** | **NeMo Agent Toolkit** (`nvidia-nat`) | 1.4+ | Profiling、evaluation、优化 |
| LLM | MiniMax-2.1（主）/ Claude（可选） | — | 通过 LangChain ChatModel 适配 |
| LLM 接入 | langchain-openai / langchain-anthropic | — | OpenAI-compatible 协议适配 MiniMax |
| HTTP 客户端 | httpx | 0.27+ | — |
| SSH | asyncssh | 2.14+ | — |
| 配置校验 | Pydantic v2 | 2.5+ | — |
| 向量存储 | ChromaDB | 0.4+ | — |
| 图存储 | NetworkX + aiosqlite | — | aiosqlite 替代 sqlite3（P1 修复） |
| CLI | Click | 8.1+ | — |
| Web API | FastAPI + uvicorn | 0.110+ | — |
| WebSocket | FastAPI WebSocket | — | — |
| 模板引擎 | Jinja2 | 3.1+ | — |
| 前端 | React 18 + Ant Design + D3.js | — | — |
| 图表 | Plotly | 5.18+ | — |

### 2.5 项目结构

```
sre_agent/
├── cli.py                          # Click CLI 入口
├── config.py                       # Pydantic 配置模型
├── server.py                       # FastAPI 服务入口
│
├── agent/
│   ├── sre_agent.py                # SRE Agent — LangGraph StateGraph 核心
│   ├── graph.py                    # LangGraph 图定义（nodes, edges, conditions）
│   ├── state.py                    # MessagesState 扩展（诊断上下文状态）
│   ├── nodes.py                    # LangGraph 节点函数（agent_node, conclude_node, remediate_node）
│   ├── discovery_agent.py          # 拓扑发现 Agent（确定性）
│   ├── monitor_agent.py            # 持续监控 Agent（确定性）
│   ├── conversational_agent.py     # 对话式 SRE 助手 — LangGraph
│   ├── thinking_trace.py           # 思考过程记录
│   └── prompts/
│       ├── sre_system.j2           # SRE Agent 系统提示词
│       ├── diagnosis.j2            # 诊断上下文模板
│       └── remediation.j2          # 修复计划模板
│
├── guardrails/                     # NeMo Guardrails 配置
│   ├── config.yml                  # 模型配置 + 活跃 rails
│   ├── rails/
│   │   ├── input.co                # 输入护栏（防注入、话题约束）
│   │   ├── output.co               # 输出护栏（防泄密、幻觉检测）
│   │   ├── execution.co            # 执行护栏（工具 I/O 验证）
│   │   └── dialog.co               # 对话护栏（拒绝破坏性操作请求）
│   ├── actions.py                  # 自定义 NeMo Actions
│   └── prompts.yml                 # 安全检查提示词模板
│
├── nat/                            # NeMo Agent Toolkit 配置
│   ├── workflow.yml                # NAT workflow 定义（profiling/eval）
│   └── eval_dataset.jsonl          # 诊断准确率评估数据集
│
├── channels/                       # 共享 Channel 层
│   ├── base.py                     # BaseChannel ABC
│   ├── ssh.py                      # SSHChannel
│   ├── redfish.py                  # RedfishChannel
│   ├── switch.py                   # SwitchChannel
│   ├── k8s.py                      # K8sChannel
│   ├── cube_studio.py              # CubeStudioChannel
│   ├── prometheus.py               # PrometheusChannel
│   ├── log.py                      # LogChannel（新增）
│   ├── alert.py                    # AlertChannel（新增）
│   ├── ontology.py                 # OntologyChannel（新增）
│   └── knowledge.py                # KnowledgeBaseChannel（新增）
│
├── tools/
│   ├── registry.py                 # 工具注册表
│   ├── definitions.py              # 工具定义（JSON Schema）
│   ├── metrics.py                  # 指标类工具
│   ├── logs.py                     # 日志类工具
│   ├── k8s_tools.py                # K8s 类工具
│   ├── network.py                  # 网络类工具
│   ├── bmc.py                      # BMC 类工具
│   ├── platform.py                 # 平台类工具
│   ├── ontology_tools.py           # 拓扑查询工具
│   ├── knowledge_tools.py          # 知识库工具
│   └── remediation_tools.py        # 修复类工具（write）
│
├── ontology/
│   ├── models.py                   # 实体 + 关系 Pydantic 模型
│   ├── graph.py                    # NetworkX 图操作
│   ├── store.py                    # SQLite 持久化
│   └── discovery/
│       ├── bmc_scanner.py          # BMC Redfish 扫描
│       ├── switch_scanner.py       # LLDP/CDP 发现
│       ├── k8s_scanner.py          # K8s API 发现
│       └── prometheus_scanner.py   # Prometheus targets 发现
│
├── remediation/
│   ├── engine.py                   # 修复引擎
│   ├── planner.py                  # 修复计划生成
│   ├── canary.py                   # 灰度部署控制
│   ├── approval.py                 # 审批门控
│   └── wal.py                      # WAL 回滚日志（复用 fault-injector 设计）
│
├── knowledge/
│   ├── store.py                    # ChromaDB 向量存储
│   ├── ingest.py                   # 文档摄入 pipeline
│   ├── chunker.py                  # 文档分块
│   └── runbook.py                  # Runbook 结构化解析
│
├── memory/
│   ├── store.py                    # SQLite 记忆存储
│   ├── incident.py                 # 事件记录
│   ├── pattern.py                  # 模式学习
│   └── config_memory.py            # 配置记忆（基线值、阈值）
│
├── skills/
│   ├── runtime/
│   │   ├── registry.py                 # SkillRegistry — 热扫描 **/SKILL.md
│   │   ├── executor.py                 # SkillExecutor — 安全脚本执行
│   │   ├── tools.py                    # 4 个固定 LangChain @tool
│   │   └── policy.py                   # allow/deny/ask 权限策略
│   ├── builtin/                        # 内置 Skills（Claude Code SKILL.md 格式）
│   │   ├── vllm-diagnosis/
│   │   │   ├── SKILL.md
│   │   │   ├── scripts/
│   │   │   │   └── check_gpu_contention.sh
│   │   │   └── references/
│   │   │       └── vllm-tuning-guide.md
│   │   ├── rdma-diagnosis/
│   │   │   ├── SKILL.md
│   │   │   ├── scripts/
│   │   │   │   └── check_pfc_storm.sh
│   │   │   └── references/
│   │   │       └── rdma-troubleshoot.md
│   │   ├── gpu-health/
│   │   │   ├── SKILL.md
│   │   │   └── scripts/
│   │   │       └── gpu_ecc_check.py
│   │   └── network-diagnosis/
│   │       ├── SKILL.md
│   │       └── scripts/
│   │           └── port_scan.sh
│   └── custom/                         # 用户自定义 Skills（同格式，热加载）
│
├── safety/
│   ├── guard.py                    # SafetyGuard
│   └── forbidden.py                # 禁止操作列表
│
└── tests/
    ├── test_agent.py
    ├── test_ontology.py
    ├── test_remediation.py
    └── test_tools.py
```

### 2.6 核心架构不变量

1. **LLM 不直接执行写操作**：SRE Agent 的 LangGraph 循环中，LLM 仅绑定 read_only 工具（通过 `llm.bind_tools(read_only_tools)`）。write 工具不注册为 LLM 可调用工具，而是作为 schema 描述附加到系统提示词中供 LLM 生成 `RemediationPlan`。修复执行通过确定性的 Remediation Engine。
2. **NeMo Guardrails 强制安全边界**：所有 LLM 输入经过 input rails（防注入/话题约束），所有输出经过 output rails（防泄密/脱敏），所有工具调用经过 execution rails（I/O 验证）。
3. **写操作前必须记录 WAL**：与 fault-injector 相同，任何修复操作在执行前将恢复命令写入回滚日志（fsync）。
4. **灰度优先**：修复默认走 canary 路径，除非配置明确跳过。
5. **可恢复**：进程崩溃时可通过 `sre-agent --resume <session_id>` 恢复。LangGraph 支持 checkpoint 持久化。
6. **条件表达式安全求值**：所有验证条件（`success_condition`、`success_criteria`）通过白名单操作符求值器执行，禁止 `eval()`。

---

## 3. 数字孪生 / Ontology 模型

### 3.1 设计理念

参考 Palantir Ontology 思路，将 AIDC 的物理设备、网络拓扑、平台服务、监控指标统一建模为**实体（Entity）+ 关系（Relationship）**的图结构。所有 SRE Agent 的诊断和修复操作都基于这个拓扑图进行上下文感知。

### 3.2 实体类型

```python
from pydantic import BaseModel
from typing import Literal
from datetime import datetime
from enum import Enum

# ─── 物理层实体 ───

class Rack(BaseModel):
    """机柜"""
    id: str                                     # "rack-A01"
    location: str                               # "DC-1-Floor-2-Row-A"
    power_capacity_kw: float
    cooling_type: str                           # "air" | "liquid"

class Node(BaseModel):
    """物理服务器节点"""
    id: str                                     # "gpu-1-1"
    hostname: str
    rack_id: str                                # 所属机柜
    role: list[str]                             # ["k8s-worker", "gpu-compute"]
    ssh: SSHConfig
    bmc: BMCConfig | None
    cpu_model: str
    cpu_cores: int
    memory_gb: int
    gpus: list[GPUInfo]
    nics: list[NICInfo]
    disks: list[DiskInfo]
    os_version: str
    status: Literal["online", "offline", "maintenance", "degraded"]

class GPUInfo(BaseModel):
    """GPU 设备"""
    id: str                                     # "gpu-1-1:GPU-0"
    node_id: str
    index: int
    model: str                                  # "NVIDIA A100-SXM4-80GB"
    vram_gb: int
    pcie_bus: str                               # "0000:3b:00.0"
    status: Literal["healthy", "degraded", "failed"]

class NICInfo(BaseModel):
    """网络接口"""
    id: str                                     # "gpu-1-1:enp65s0f0"
    node_id: str
    name: str                                   # "enp65s0f0"
    mac: str
    speed_gbps: int                             # 200 for 200GbE
    type: Literal["ethernet", "infiniband", "roce"]
    rdma_capable: bool
    connected_switch_port: str | None           # "sw-200g:HGE1/0/1"
    status: Literal["up", "down", "error"]

# ─── 网络层实体 ───

class Switch(BaseModel):
    """交换机"""
    id: str                                     # "sw-200g"
    model: str                                  # "H3C S9855-24B8D"
    rack_id: str
    management: SwitchMgmtConfig
    role: Literal["leaf", "spine", "tor", "core"]
    port_count: int
    status: Literal["online", "offline", "degraded"]

class SwitchPort(BaseModel):
    """交换机端口"""
    id: str                                     # "sw-200g:HGE1/0/1"
    switch_id: str
    name: str                                   # "HundredGigE 1/0/1"
    speed_gbps: int
    vlan: int | None
    pfc_enabled: bool
    ecn_enabled: bool
    connected_to: str | None                    # 对端实体 ID
    status: Literal["up", "down", "err-disabled"]

# ─── BMC 层实体 ───

class BMCEndpoint(BaseModel):
    """BMC 管理接口"""
    id: str                                     # "bmc-gpu-1-1"
    node_id: str
    ip: str
    firmware_version: str
    capabilities: list[str]                     # ["Redfish", "IPMI", "SOL"]

# ─── 平台层实体 ───

class K8sCluster(BaseModel):
    """K8s 集群"""
    id: str
    name: str
    api_server: str
    version: str
    node_count: int
    namespaces: dict[str, str]                  # {"pipeline": "pipeline", ...}

class K8sPod(BaseModel):
    """K8s Pod"""
    id: str                                     # "service:vllm-xxx-abc123"
    namespace: str
    name: str
    node_id: str
    labels: dict[str, str]
    phase: Literal["Pending", "Running", "Succeeded", "Failed", "Unknown"]
    resource_requests: dict[str, str]
    resource_limits: dict[str, str]
    created_at: datetime

# ─── 服务层实体 ───

class InferenceService(BaseModel):
    """推理服务"""
    id: str
    name: str
    model_name: str
    replicas: int
    endpoint: str
    pod_ids: list[str]
    sla: dict | None                            # {"p95_ms": 500, "p99_ms": 1000}

class TrainingPipeline(BaseModel):
    """训练 Pipeline"""
    id: str
    name: str
    template: str
    task_count: int
    pod_ids: list[str]
    status: Literal["pending", "running", "succeeded", "failed"]
```

### 3.3 关系类型

```python
class RelationType(str, Enum):
    HOSTED_ON = "hosted_on"           # Pod → Node, GPU → Node
    CONNECTED_TO = "connected_to"     # NIC ↔ SwitchPort
    PART_OF = "part_of"               # Node → Rack, Port → Switch
    SERVES = "serves"                 # InferenceService → Pod
    DEPENDS_ON = "depends_on"         # Service → GPU, Pipeline → PVC
    MANAGES = "manages"               # BMCEndpoint → Node
    MONITORS = "monitors"             # MetricEndpoint → Entity

class Relationship(BaseModel):
    source_id: str
    target_id: str
    relation: RelationType
    properties: dict | None = None    # 附加属性（如端口号、带宽）
```

### 3.4 Ontology 图存储

```python
import networkx as nx
import aiosqlite
from collections import deque

class OntologyGraph:
    """数字孪生图：NetworkX 内存图 + aiosqlite 异步持久化"""

    def __init__(self, db_path: str = "./ontology.db"):
        self.graph = nx.DiGraph()
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._init_tables()

    def add_entity(self, entity: BaseModel) -> None:
        self.graph.add_node(entity.id, type=type(entity).__name__,
                            data=entity.model_dump())
        self._persist_entity(entity)

    def add_relationship(self, rel: Relationship) -> None:
        self.graph.add_edge(rel.source_id, rel.target_id,
                            relation=rel.relation, properties=rel.properties)
        self._persist_relationship(rel)

    # ─── 查询 API ───

    def get_entity(self, entity_id: str) -> dict | None:
        return self.graph.nodes.get(entity_id)

    def find_entities(self, entity_type: str,
                      filters: dict | None = None) -> list[dict]:
        results = [
            data for _, data in self.graph.nodes(data=True)
            if data.get("type") == entity_type
        ]
        if filters:
            results = [r for r in results if self._match_filters(r, filters)]
        return results

    def get_neighbors(self, entity_id: str,
                      relation: RelationType | None = None) -> list[dict]:
        neighbors = []
        for _, target, data in self.graph.out_edges(entity_id, data=True):
            if relation is None or data["relation"] == relation:
                neighbors.append({"entity": self.graph.nodes[target],
                                  "relation": data})
        for source, _, data in self.graph.in_edges(entity_id, data=True):
            if relation is None or data["relation"] == relation:
                neighbors.append({"entity": self.graph.nodes[source],
                                  "relation": data})
        return neighbors

    def get_blast_radius(self, entity_id: str) -> dict:
        """故障影响半径分析：BFS 双向遍历受影响实体

        修复说明 (P1-1): 原实现仅遍历 out_edges，但关系方向定义为
        HOSTED_ON: Pod→Node, SERVES: Service→Pod。当 Node 故障时，
        需沿 in_edges 反向追踪 HOSTED_ON 才能找到受影响的 Pod。
        现在 BFS 同时遍历 out_edges 和 in_edges，根据关系类型决定方向：
        - 故障向上传播（影响依赖者）：沿 SERVES/DEPENDS_ON 的 in_edges
        - 故障向下定位（找到宿主/组件）：沿 HOSTED_ON/PART_OF 的 out_edges
        """
        affected = {}
        visited = set()
        queue = deque([(entity_id, 0)])

        # 关系传播方向映射
        PROPAGATE_VIA_OUT = {RelationType.HOSTED_ON, RelationType.PART_OF}
        PROPAGATE_VIA_IN = {RelationType.SERVES, RelationType.DEPENDS_ON,
                            RelationType.HOSTED_ON}

        while queue:
            current, hops = queue.popleft()   # deque.popleft() = O(1)
            if current in visited:
                continue
            visited.add(current)
            affected[current] = {"entity": self.graph.nodes.get(current),
                                 "hops": hops}
            # 正向：沿 out_edges 追踪
            for _, target, data in self.graph.out_edges(current, data=True):
                if data["relation"] in PROPAGATE_VIA_OUT:
                    queue.append((target, hops + 1))
            # 反向：沿 in_edges 追踪（找到依赖当前实体的上层）
            for source, _, data in self.graph.in_edges(current, data=True):
                if data["relation"] in PROPAGATE_VIA_IN:
                    queue.append((source, hops + 1))
        return affected

    def get_path(self, from_id: str, to_id: str) -> list[str] | None:
        try:
            return nx.shortest_path(self.graph, from_id, to_id)
        except nx.NetworkXNoPath:
            return None
```

### 3.5 自动发现 Agent（确定性）

```python
class DiscoveryAgent:
    """拓扑自动发现 — 确定性执行，无 LLM"""

    def __init__(self, config: DiscoveryConfig, channels: dict):
        self.config = config
        self.redfish = channels["redfish"]
        self.switch = channels["switch"]
        self.k8s = channels["k8s"]
        self.prometheus = channels["prometheus"]
        self.ontology = OntologyGraph(config.ontology_db)

    async def discover_all(self) -> OntologyGraph:
        """全量发现：并行扫描所有数据源"""
        await asyncio.gather(
            self.discover_bmc(),
            self.discover_switches(),
            self.discover_k8s(),
            self.discover_prometheus(),
            return_exceptions=True,
        )
        await self.infer_topology()
        return self.ontology

    async def discover_bmc(self) -> list[Node]:
        """Redfish 扫描 BMC IP 范围 → 发现节点 + GPU + NIC"""
        nodes = []
        for ip in self._expand_ip_ranges(self.config.bmc_ip_ranges):
            try:
                token = await self.redfish.authenticate(ip)
                system = await self.redfish.get(ip, "/redfish/v1/Systems/Self")
                chassis = await self.redfish.get(ip, "/redfish/v1/Chassis/Self")

                # GPU 通过 PCIe 设备枚举
                pcie = await self.redfish.get(
                    ip, "/redfish/v1/Systems/Self/PCIeDevices")
                gpus = [d for d in pcie.get("Members", [])
                        if "GPU" in d.get("Name", "")]

                # 位置信息 → 推断机柜
                location = chassis.get("Location", {})
                rack_id = self._infer_rack(location, system.get("HostName"))

                node = Node(
                    id=f"node-{system['HostName']}",
                    hostname=system["HostName"],
                    rack_id=rack_id,
                    gpus=[self._parse_gpu(g) for g in gpus],
                    # ...
                )
                self.ontology.add_entity(node)
                nodes.append(node)
            except Exception as e:
                logger.warning(f"BMC discovery failed for {ip}: {e}")
        return nodes

    async def discover_switches(self) -> list[Switch]:
        """交换机 LLDP 邻居表 → 发现连接拓扑"""
        switches = []
        for sw_config in self.config.switches:
            lldp_output = await self.switch.ssh_cli_execute(
                sw_config, ["display lldp neighbor-information verbose"])
            for neighbor in self._parse_lldp(lldp_output):
                self.ontology.add_relationship(Relationship(
                    source_id=f"{sw_config.name}:{neighbor['local_port']}",
                    target_id=neighbor["remote_node_id"],
                    relation=RelationType.CONNECTED_TO,
                ))
            switches.append(Switch(id=f"sw-{sw_config.name}", ...))
        return switches

    async def discover_k8s(self) -> list[K8sCluster]:
        """K8s API → 发现集群、节点、Pod、服务"""
        for cluster_name in self.config.k8s_clusters:
            nodes = await self.k8s.get_nodes(cluster=cluster_name)
            for node in nodes:
                self.ontology.add_entity(K8sNode(...))
                self.ontology.add_relationship(Relationship(
                    source_id=f"k8s-{node['name']}",
                    target_id=f"node-{node['name']}",
                    relation=RelationType.HOSTED_ON,
                ))
            for ns in ["service", "pipeline", "jupyter", "infra"]:
                pods = await self.k8s.get_pods(namespace=ns, cluster=cluster_name)
                for pod in pods:
                    self.ontology.add_entity(K8sPod(...))
        return [...]

    async def infer_topology(self) -> None:
        """推断未被 LLDP 覆盖的关系"""
        # 1. NIC MAC ↔ Switch Port MAC 匹配
        # 2. K8s Pod → Node 调度关系
        # 3. InferenceService → Pod label 匹配
        # 4. hostname 命名规则 → rack 归属
        pass
```

---

## 4. Agent 核心 — ReAct 诊断循环

### 4.1 为什么 SRE 需要 ReAct（而非 fault-injector 的单次推理）

| 维度 | fault-injector 诊断 | SRE Agent 诊断 |
|------|---------------------|----------------|
| 数据可用性 | 注入前已收集全部基线+观测指标 | 告警时仅有初始信号，需逐步采集 |
| 假设空间 | 已知注入了什么，分析传播链 | 不知根因，需枚举假设并排除 |
| 步骤依赖 | 无，所有数据一次性给 LLM | 有，后续采集依赖前步观测结果 |
| 推理模式 | 单次 Prompt → JSON | 多步 Think → Act → Observe 循环 |
| 需求原文 | — | "试验→验证→排查的循环最终定位" |

### 4.2 ReAct 循环实现（LangGraph + NeMo Guardrails）

> **架构变更**：原设计使用手写 for 循环实现 ReAct。现改为 **LangGraph StateGraph**
> + **NeMo Guardrails RunnableRails** 实现，获得以下优势：
> - LangGraph 自动管理 tool_call → observation → re-think 循环
> - NeMo Guardrails 在 LLM 调用前后自动执行安全检查
> - LangGraph checkpoint 支持会话持久化和 crash recovery
> - 标准 LangChain tool 协议，LLM 模型切换零成本
>
> **Review P1-5 修复**：write 工具的 schema 描述作为 system prompt 附加信息提供给 LLM
> （只读参考，不注册为可调用工具），使 LLM 能生成正确的 RemediationPlan。
>
> **Review P1-6 修复**：步级和会话级 timeout 通过 `asyncio.wait_for` 实现。

```python
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, MessagesState, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from nemoguardrails import RailsConfig
from nemoguardrails.integrations.langchain.runnable_rails import RunnableRails
import asyncio
from typing import TypedDict, Annotated
from operator import add


# ─── 状态定义 ───

class SREAgentState(MessagesState):
    """LangGraph 状态：扩展 MessagesState 添加诊断上下文"""
    alert: Alert                                    # 原始告警
    topology_summary: str                           # Ontology 拓扑摘要
    similar_incidents: list[dict]                   # 历史相似事件
    knowledge_context: str                          # RAG 知识片段
    known_patterns: str                             # 已学习模式
    thinking_trace: list[dict]                      # 思考过程记录
    step_count: int                                 # 当前步数
    diagnosis_result: DiagnosisResult | None        # 诊断结论
    remediation_plan: RemediationPlan | None        # 修复计划


# ─── SRE Agent 核心 ───

class SREAgent:
    """基于 LangGraph + NeMo Guardrails 的 SRE 诊断 Agent"""

    def __init__(self, config: AgentConfig, tool_registry: ToolRegistry,
                 ontology: OntologyGraph, knowledge: KnowledgeStore,
                 memory: MemoryStore):
        self.config = config
        self.tools = tool_registry
        self.ontology = ontology
        self.knowledge = knowledge
        self.memory = memory
        self.max_steps = config.max_steps  # 默认 20

        # ── 构建 LLM ──
        self.llm = ChatOpenAI(
            model=config.llm.model,
            openai_api_base=str(config.llm.api_base),
            openai_api_key=os.environ[config.llm.api_key_env],
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )

        # ── 构建 LangChain Tools ──
        self.lc_tools = self._build_langchain_tools()
        self.llm_with_tools = self.llm.bind_tools(self.lc_tools)

        # ── NeMo Guardrails ──
        rails_config = RailsConfig.from_path("./sre_agent/guardrails")
        self.guardrails = RunnableRails(
            config=rails_config,
            passthrough=True,           # 必须 True 才支持 tool calling
        )

        # ── 构建 LangGraph ──
        self.graph = self._build_graph()

    def _build_langchain_tools(self) -> list:
        """将 ToolRegistry 中的 read_only 工具转为 LangChain @tool"""
        lc_tools = []
        for name, tool_def in self.tools._tools.items():
            if tool_def.safety_level != "read_only":
                continue
            handler = self.tools._handlers[name]
            # 动态创建 LangChain tool
            lc_tool = self._wrap_as_langchain_tool(name, tool_def, handler)
            lc_tools.append(lc_tool)
        return lc_tools

    def _build_graph(self) -> StateGraph:
        """构建 LangGraph 诊断状态图"""
        # 系统提示词（包含 write 工具描述供 LLM 参考）
        write_tools_desc = self.tools.get_tool_descriptions(
            safety_level="write_confirm")
        prompt = ChatPromptTemplate.from_messages([
            ("system", SRE_SYSTEM_PROMPT + f"\n\n## 可用修复工具（仅供参考，不可直接调用）\n{write_tools_desc}"),
            MessagesPlaceholder(variable_name="messages"),
        ])
        guarded_llm = prompt | (self.guardrails | self.llm_with_tools)

        # ── 节点定义 ──
        async def agent_node(state: SREAgentState) -> dict:
            """LLM 推理节点（带 NeMo Guardrails 保护）"""
            response = await asyncio.wait_for(
                guarded_llm.ainvoke(state["messages"]),
                timeout=self.config.step_timeout,
            )
            # 记录思考步骤
            step = {
                "step": state.get("step_count", 0),
                "type": "thought",
                "content": response.content if hasattr(response, 'content') else str(response),
                "has_tool_calls": bool(getattr(response, 'tool_calls', [])),
            }
            return {
                "messages": [response],
                "thinking_trace": state.get("thinking_trace", []) + [step],
                "step_count": state.get("step_count", 0) + 1,
            }

        tool_node = ToolNode(self.lc_tools)

        def should_continue(state: SREAgentState) -> str:
            """条件路由：继续工具调用 or 结束"""
            # 超过最大步数
            if state.get("step_count", 0) >= self.max_steps:
                return END
            # 标准 LangGraph tools_condition
            return tools_condition(state)

        # ── 构建图 ──
        graph = StateGraph(SREAgentState)
        graph.add_node("agent", agent_node)
        graph.add_node("tools", tool_node)
        graph.set_entry_point("agent")
        graph.add_conditional_edges("agent", should_continue)
        graph.add_edge("tools", "agent")

        # 使用 aiosqlite checkpoint 实现会话持久化
        checkpointer = AsyncSqliteSaver.from_conn_string(
            "./data/langgraph_checkpoints.db")
        return graph.compile(checkpointer=checkpointer)

    async def diagnose(self, alert: Alert,
                       trace_callback: Callable | None = None
                       ) -> DiagnosisSession:
        """
        接收告警，启动 LangGraph 诊断循环。

        trace_callback: 实时推送 ThinkingStep 到 GUI（WebSocket）
        """
        session = DiagnosisSession.create(alert)
        context = await self._build_initial_context(alert)

        # 构建初始消息
        initial_message = self._format_alert_message(alert, context)

        # 运行 LangGraph（带总超时）
        try:
            final_state = await asyncio.wait_for(
                self.graph.ainvoke(
                    {
                        "messages": [("user", initial_message)],
                        "alert": alert,
                        "topology_summary": context.topology,
                        "similar_incidents": context.similar_incidents,
                        "knowledge_context": context.knowledge,
                        "known_patterns": context.known_patterns,
                        "thinking_trace": [],
                        "step_count": 0,
                        "diagnosis_result": None,
                        "remediation_plan": None,
                    },
                    config={"configurable": {"thread_id": session.session_id}},
                ),
                timeout=self.config.total_timeout,
            )
        except asyncio.TimeoutError:
            session.status = "timeout"
            return session

        # 解析最终状态
        session.trace = ThinkingTrace.from_langraph_state(
            final_state["thinking_trace"])
        session.status = "diagnosed"

        # 记录到记忆系统
        await self.memory.record_incident(session)
        return session

    async def _build_initial_context(self, alert: Alert) -> DiagnosisContext:
        """构建初始诊断上下文"""
        # 1. 从 Ontology 获取告警关联实体的拓扑
        entity_id = self._resolve_alert_entity(alert)
        blast_radius = self.ontology.get_blast_radius(entity_id)
        topology_summary = self._summarize_topology(blast_radius)

        # 2. 从记忆系统检索相似历史事件
        similar_incidents = await self.memory.search_similar(
            symptoms=alert.labels,
            top_k=3,
        )

        # 3. 从知识库检索相关知识
        knowledge_chunks = await self.knowledge.search(
            query=f"{alert.alert_name} {alert.summary}",
            top_k=5,
        )

        return DiagnosisContext(
            alert=alert,
            topology=topology_summary,
            similar_incidents=similar_incidents,
            knowledge=knowledge_chunks,
            observations=[],
        )
```

### 4.3 思考过程记录（ThinkingTrace）

需求："能显示 agent 的整个思考过程"。

```python
from dataclasses import dataclass, field, asdict

@dataclass
class ThinkingStep:
    """单步思考记录"""
    step: int
    timestamp: datetime = field(default_factory=datetime.now)
    thought: str                                # LLM 的推理文本
    action_type: Literal["tool_call", "conclude", "remediate"]
    tool_name: str | None = None
    tool_params: dict | None = None
    confidence: float | None = None             # 当前假设置信度

    def to_dict(self) -> dict:
        """P2-6 修复：显式实现 to_dict()，用于 WebSocket 推送"""
        return asdict(self)

@dataclass
class Observation:
    """工具调用结果"""
    tool: str
    params: dict
    result: dict
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """P2-6 修复：显式实现 to_dict()，用于 WebSocket 推送"""
        return asdict(self)

@dataclass
class ThinkingTrace:
    """完整思考过程"""
    steps: list[ThinkingStep | Observation] = field(default_factory=list)

    def add_step(self, step: ThinkingStep) -> None:
        self.steps.append(step)

    def add_observation(self, obs: Observation) -> None:
        self.steps.append(obs)

    @classmethod
    def from_langraph_state(cls, trace_dicts: list[dict]) -> "ThinkingTrace":
        """从 LangGraph state 中的 thinking_trace 重建 ThinkingTrace"""
        trace = cls()
        for d in trace_dicts:
            if d.get("type") == "thought":
                trace.add_step(ThinkingStep(
                    step=d["step"], thought=d.get("content", ""),
                    action_type=d.get("action", "tool_call"),
                ))
            elif d.get("type") == "observation":
                trace.add_observation(Observation(
                    tool=d["tool"], params=d.get("params", {}),
                    result=d.get("result", {}),
                ))
        return trace

    def to_display(self) -> list[dict]:
        """格式化为 GUI 展示格式"""
        display = []
        for item in self.steps:
            if isinstance(item, ThinkingStep):
                display.append({
                    "type": "thought",
                    "step": item.step,
                    "time": item.timestamp.isoformat(),
                    "content": item.thought,
                    "action": item.action_type,
                })
            elif isinstance(item, Observation):
                display.append({
                    "type": "observation",
                    "tool": item.tool,
                    "time": item.timestamp.isoformat(),
                    "result_summary": self._summarize(item.result),
                })
        return display
```

**实时推送**：通过 WebSocket 将每个 ThinkingStep / Observation 实时推送给 GUI 前端：

```python
# server.py
@app.websocket("/ws/thinking-trace/{session_id}")
async def thinking_trace_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    queue = asyncio.Queue()
    # 注册回调
    async def on_step(step):
        await queue.put(step)
    sessions[session_id].trace_callback = on_step
    # 持续推送
    while True:
        step = await queue.get()
        await websocket.send_json(step.to_dict())
```

### 4.4 系统提示词工程

```python
SRE_SYSTEM_PROMPT = """
你是 AIDC（智算数据中心）的高级 SRE 专家 Agent。你将接收告警或运维人员的问题，
通过迭代诊断找到根因，并提出修复方案。

## 你的能力
- 调用诊断工具采集指标、日志、K8s 状态、网络状态、BMC 信息
- 查询数字孪生拓扑，了解实体间依赖关系和故障影响范围
- 搜索知识库获取硬件手册、Runbook、历史案例
- 搜索记忆系统查找此 AIDC 的历史事件和已知模式

## 诊断方法论
1. **理解告警**：解析告警指标、阈值、触发时间、关联实体
2. **构建假设**：基于症状列举可能的根因（至少 2-3 个）
3. **逐一验证**：对每个假设，调用工具采集证据
4. **排除/确认**：基于数据排除不符合的假设，保留一致的
5. **深入分析**：对最可能的根因进一步收集证据
6. **得出结论**：输出根因、置信度、影响范围
7. **提出修复**：如有修复方案，以结构化格式提出

## 输出格式
每一步你必须输出：
- **思考**：当前观察到什么、推理过程、下一步计划
- **行动**：调用工具 (tool_call) 或得出结论 (conclude) 或提出修复 (remediate)

## 已知 AIDC 信息
{topology_summary}

## 此 AIDC 历史模式
{known_patterns}

## 相关知识
{knowledge_context}

## 相似历史事件
{similar_incidents}
"""
```

### 4.5 多假设检验模式

SRE Agent 的诊断遵循 **假设-检验** 模式：

```
告警: vLLM P95 延迟 > 500ms

Step 1 [Think]: 分析告警，P95 延迟超标，可能原因：
  假设 A: GPU 资源争用（其他进程占用 GPU）        → 查 GPU 利用率
  假设 B: 网络延迟/丢包（RDMA 异常）              → 查网络指标
  假设 C: KV Cache 耗尽（模型内存不足）            → 查 vLLM 内存指标
  假设 D: 热节流（GPU 温度过高降频）               → 查温度

Step 2 [Act]: tool_call("get_gpu_metrics", {node: "gpu-1-1"})
Step 3 [Observe]: GPU-0 利用率 98%, GPU-1 利用率 35%
  → GPU-0 异常高，与推理服务不一致（推理用 GPU-1）
  → 有不明进程占用 GPU-0? 但推理在 GPU-1 为何延迟?

Step 4 [Think]: GPU-0 被占用可能导致 PCIe 总线争用影响 GPU-1
  → 需要验证：查进程列表 + PCIe 拓扑
  假设 A 升级为主要嫌疑

Step 5 [Act]: tool_call("get_gpu_processes", {node: "gpu-1-1"})
         (P0 修复：使用专用 get_gpu_processes 工具替代 read_system_log)
Step 6 [Observe]: {processes: [{pid: 12847, name: "gpu-burn", gpu_uuid: "GPU-0", memory_mb: 40960}]}
  → 发现 gpu-burn 进程占用 GPU-0
  → 假设 A 确认：GPU 资源争用

Step 7 [Think]: 检查是否有其他贡献因素
Step 8 [Act]: tool_call("get_thermal_status", {node: "gpu-1-1"})
Step 9 [Observe]: GPU-0 温度 82°C（正常），GPU-1 温度 71°C（正常）
  → 假设 D 排除

Step 10 [Conclude]:
  根因: GPU 资源争用 — gpu-burn 进程占用 GPU-0，导致 PCIe 带宽争用
  置信度: 0.92
  影响: vLLM 推理服务 P95 延迟从 120ms 升至 650ms
  修复建议: 终止 gpu-burn 进程 + 设置 GPU 独占策略
```

### 4.6 LLM 集成细节

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 模型 | MiniMax-2.1（主）| 通过 `langchain-openai` 的 OpenAI-compatible 协议接入 |
| 备选模型 | Claude（复杂场景）| 通过 `langchain-anthropic` 接入，需要更强推理能力时切换 |
| 温度 | 0.3（诊断）/ 0.1（修复计划）| 低温度保证稳定性 |
| 最大步数 | 20（默认，可配置）| LangGraph `should_continue` 条件边控制 |
| 工具格式 | LangChain `@tool` + `bind_tools()` | 自动生成 JSON Schema，兼容 Claude Code skills |
| 超时 | 单步 60s，总计 600s | `asyncio.wait_for` 在 agent_node 和 diagnose 两级控制 |
| 降级策略 | LLM 不可用时退化为规则检查 | ThresholdEngine 仍可独立运行 |
| 安全护栏 | NeMo Guardrails `RunnableRails(passthrough=True)` | 透传 tool calling，同时执行输入/输出/执行安全检查 |

### 4.7 DiagnosisResult 输出模型

```python
class Hypothesis(BaseModel):
    """诊断假设"""
    description: str                            # 假设描述
    status: Literal["testing", "confirmed", "eliminated"]
    evidence_for: list[str]                     # 支持证据
    evidence_against: list[str]                 # 反对证据
    confidence: float                           # 0.0 ~ 1.0

class DiagnosisResult(BaseModel):
    """诊断结论"""
    root_cause: str                             # 根因描述
    root_cause_layer: Literal["hardware", "network", "os", "platform", "service"]
    root_cause_entities: list[str]              # 关联的 Ontology 实体 ID
    confidence: float                           # 总体置信度 0.0 ~ 1.0
    hypotheses: list[Hypothesis]                # 所有假设及其验证状态
    propagation_chain: list[PropagationStep]    # 故障传播链
    impact_summary: str                         # 影响摘要
    affected_services: list[str]                # 受影响服务列表
    recommended_fix: RemediationPlan | None     # 推荐修复方案
    triage_priority: Literal["P0", "P1", "P2", "P3"]

class PropagationStep(BaseModel):
    """故障传播链中的一步"""
    entity_id: str
    entity_type: str
    metric: str
    value_before: float | str
    value_after: float | str
    description: str
```

---

## 5. 工具注册表（Tool Registry）

### 5.1 工具架构

工具采用 **双层设计**：
1. **ToolRegistry**（内部管理层）：维护工具元数据、安全级别、Channel 绑定
2. **LangChain `@tool`**（LLM 接口层）：SREAgent 在初始化时将 `read_only` 工具包装为 LangChain `@tool`，通过 `llm.bind_tools()` 注册到 LLM

工具按 `safety_level` 分为三级：

| 安全级别 | 说明 | LLM 可调用？ | 审批 |
|----------|------|-------------|------|
| `read_only` | 只读数据采集（指标、日志、状态） | 是（通过 `bind_tools`） | 无需审批 |
| `write_confirm` | 写操作（重启 Pod、修改配置） | 否（仅 schema 描述附加到 system prompt） | 需要人工确认或自动审批 |
| `write_blocked` | 高危操作（节点关机、删除 namespace） | 否 | 硬编码拦截 |

> **Review P1-5 修复**：原设计中 LLM 在诊断阶段看不到 write 工具的 schema，无法生成正确的
> RemediationPlan。现在 write 工具的描述列表作为系统提示词附加信息提供给 LLM（只读参考），
> LLM 可以基于描述生成修复计划，但无法直接调用。

```python
from langchain_core.tools import tool as langchain_tool

@dataclass
class ToolDefinition:
    """工具定义 — 兼容 Claude Code skills 格式"""
    name: str                                   # 工具名称
    description: str                            # 自然语言描述（LLM 可读）
    category: str                               # metrics | logs | k8s | network | bmc | ...
    safety_level: Literal["read_only", "write_confirm", "write_blocked"]
    input_schema: dict                          # JSON Schema
    output_schema: dict                         # JSON Schema
    channel: str                                # 使用的 Channel

class ToolRegistry:
    """工具注册表：管理所有可用工具 + 生成 LangChain @tool 包装"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}

    def register(self, tool: ToolDefinition, handler: Callable) -> None:
        self._tools[tool.name] = tool
        self._handlers[tool.name] = handler

    def get_langchain_tools(self, safety_level: str = "read_only") -> list:
        """将指定安全级别的工具包装为 LangChain @tool 列表"""
        lc_tools = []
        for name, tool_def in self._tools.items():
            if tool_def.safety_level != safety_level:
                continue
            handler = self._handlers[name]
            # 动态创建 LangChain tool（保留原始 docstring 和 schema）
            wrapped = langchain_tool(handler)
            wrapped.name = name
            wrapped.description = tool_def.description
            lc_tools.append(wrapped)
        return lc_tools

    def get_tool_descriptions(self, safety_level: str) -> str:
        """生成工具描述文本（用于附加到 system prompt，不注册为可调用工具）"""
        lines = []
        for name, t in self._tools.items():
            if t.safety_level != safety_level:
                continue
            lines.append(f"- {name}: {t.description}")
            lines.append(f"  参数: {json.dumps(t.input_schema, ensure_ascii=False)}")
        return "\n".join(lines)

    async def execute(self, name: str, params: dict,
                      safety_level: str = "read_only") -> dict:
        tool = self._tools[name]
        if tool.safety_level == "write_blocked":
            raise SafetyViolationError(f"Tool {name} is blocked")
        if tool.safety_level == "write_confirm" and safety_level == "read_only":
            raise SafetyViolationError(f"Tool {name} requires write permission")
        return await self._handlers[name](**params)
```

### 5.2 只读诊断工具（safety_level: read_only）

#### 指标类工具 (Metrics)

| 工具名 | 输入 | 输出 | Channel |
|--------|------|------|---------|
| `query_prometheus` | `promql: str, start: str?, end: str?, step: str?` | `{values: list[{timestamp, value}]}` | Prometheus |
| `get_pod_metrics` | `pod: str, namespace: str` | `{cpu_usage, memory_usage, gpu_usage}` | K8s |
| `get_node_metrics` | `node: str` | `{cpu_pct, memory_pct, disk_io, network_io}` | K8s |
| `get_gpu_metrics` | `node: str` | `{per_gpu: [{index, util_pct, mem_used_mb, mem_total_mb, temp_c, power_w}]}` | SSH |
| `get_inference_latency` | `service: str, percentile: int?` | `{p50_ms, p95_ms, p99_ms, qps, error_rate}` | Prometheus |
| `get_gpu_processes` | `node: str` | `{processes: list[{pid, name, gpu_uuid, memory_mb}]}` | SSH |

> **P0 新增**：`get_gpu_processes` 是专用的 GPU 进程查询工具，调用
> `nvidia-smi --query-compute-apps=pid,name,gpu_uuid,used_memory`。
> 原设计中 Demo 1 Step 5 错误地使用 `read_system_log(command="nvidia-smi ...")`，
> 但 `read_system_log` 不支持 `command` 参数。新增此工具避免开放任意命令执行。

```python
# 示例：get_gpu_processes 实现（P0 新增）
async def get_gpu_processes(node: str) -> dict:
    """查询指定节点上的 GPU 计算进程"""
    output = await ssh_channel.run_command(
        node, "nvidia-smi --query-compute-apps=pid,name,gpu_uuid,used_memory "
              "--format=csv,noheader,nounits")
    processes = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        pid, name, gpu_uuid, memory = line.split(", ")
        processes.append({
            "pid": int(pid), "name": name.strip(),
            "gpu_uuid": gpu_uuid.strip(), "memory_mb": float(memory),
        })
    return {"processes": processes}

# 示例：get_gpu_metrics 实现
async def get_gpu_metrics(node: str) -> dict:
    """通过 SSH 执行 nvidia-smi 获取 GPU 指标"""
    output = await ssh_channel.run_command(
        node, "nvidia-smi --query-gpu=index,utilization.gpu,"
              "memory.used,memory.total,temperature.gpu,power.draw "
              "--format=csv,noheader,nounits")
    gpus = []
    for line in output.strip().split("\n"):
        idx, util, mem_used, mem_total, temp, power = line.split(", ")
        gpus.append({
            "index": int(idx), "util_pct": float(util),
            "mem_used_mb": float(mem_used), "mem_total_mb": float(mem_total),
            "temp_c": float(temp), "power_w": float(power),
        })
    return {"per_gpu": gpus}
```

#### 日志类工具 (Logs)

| 工具名 | 输入 | 输出 | Channel |
|--------|------|------|---------|
| `read_pod_logs` | `pod: str, namespace: str, tail: int?, since: str?` | `{lines: list[str]}` | K8s |
| `search_logs` | `pattern: str, namespace: str, time_range: str?` | `{matches: list[{pod, line, timestamp}]}` | K8s |
| `read_system_log` | `node: str, log_path: str, tail: int?` | `{lines: list[str]}` | SSH |
| `get_bmc_sel` | `node: str, count: int?` | `{entries: list[{timestamp, severity, message}]}` | Redfish |
| `get_dmesg` | `node: str, filter: str?` | `{lines: list[str]}` | SSH |

#### K8s 状态工具

| 工具名 | 输入 | 输出 | Channel |
|--------|------|------|---------|
| `get_pods` | `namespace: str, labels: dict?, status: str?` | `{pods: list[{name, node, phase, restarts, age}]}` | K8s |
| `get_pod_events` | `pod: str, namespace: str` | `{events: list[{type, reason, message, timestamp}]}` | K8s |
| `get_node_conditions` | `node: str` | `{conditions: dict[str, str]}` | K8s |
| `get_deployments` | `namespace: str` | `{deployments: list[{name, replicas, ready, available}]}` | K8s |
| `describe_pod` | `pod: str, namespace: str` | `{spec, status, conditions, containers}` | K8s |
| `get_pvc_status` | `namespace: str` | `{pvcs: list[{name, capacity, access_mode, phase}]}` | K8s |

#### 网络诊断工具

| 工具名 | 输入 | 输出 | Channel |
|--------|------|------|---------|
| `check_rdma_status` | `node: str` | `{devices: list[{name, state, port_state, link_layer}]}` | SSH |
| `get_pfc_counters` | `node: str, interface: str` | `{rx_pause: dict, tx_pause: dict}` | SSH |
| `get_switch_port_status` | `switch: str, port: str` | `{status, speed, errors, pfc_config, ecn_config}` | Switch |
| `get_ecn_config` | `switch: str` | `{interfaces: list[{port, ecn_enabled, marking_threshold}]}` | Switch |
| `check_nic_errors` | `node: str, interface: str` | `{rx_errors, tx_errors, rx_dropped, tx_dropped, crc_errors}` | SSH |
| `ping_check` | `source: str, dest: str, count: int?` | `{loss_pct, avg_ms, max_ms}` | SSH |

```python
# 示例：check_rdma_status 实现
async def check_rdma_status(node: str) -> dict:
    output = await ssh_channel.run_command(node, "ibstat")
    devices = []
    for dev in parse_ibstat(output):
        devices.append({
            "name": dev["ca_name"],
            "state": dev["state"],           # "Active" | "Down" | "Init"
            "port_state": dev["phys_state"],  # "LinkUp" | "Polling" | "Disabled"
            "link_layer": dev["link_layer"],  # "Ethernet" (RoCEv2) | "InfiniBand"
            "rate_gbps": dev["rate"],
        })
    return {"devices": devices}
```

#### BMC 工具

| 工具名 | 输入 | 输出 | Channel |
|--------|------|------|---------|
| `get_bmc_health` | `node: str` | `{status, firmware, power_state, errors}` | Redfish |
| `get_thermal_status` | `node: str` | `{temperatures: list, fans: list}` | Redfish |
| `get_power_status` | `node: str` | `{power_state, power_consumed_watts}` | Redfish |
| `get_pcie_errors` | `node: str` | `{devices: list[{bus, correctable, uncorrectable}]}` | Redfish |

#### 平台工具

| 工具名 | 输入 | 输出 | Channel |
|--------|------|------|---------|
| `get_inference_service_status` | `name: str` | `{replicas, ready, endpoint, model}` | CubeStudio |
| `get_pipeline_status` | `pipeline_id: int` | `{status, tasks, progress}` | CubeStudio |
| `get_celery_queue_depth` | — | `{queues: dict[str, int]}` | CubeStudio |

#### 拓扑工具

| 工具名 | 输入 | 输出 | Channel |
|--------|------|------|---------|
| `query_topology` | `entity_type: str, filters: dict?` | `{entities: list[dict]}` | Ontology |
| `get_blast_radius` | `entity_id: str` | `{affected: dict[str, {entity, hops}]}` | Ontology |
| `get_entity_detail` | `entity_id: str` | `{type, data, relationships}` | Ontology |
| `get_neighbors` | `entity_id: str, relation: str?` | `{neighbors: list}` | Ontology |

#### 知识与记忆工具

| 工具名 | 输入 | 输出 | Channel |
|--------|------|------|---------|
| `search_knowledge` | `query: str, category: str?, top_k: int?` | `{chunks: list[{content, source, score}]}` | Knowledge |
| `search_runbook` | `symptom: str` | `{runbooks: list[{title, steps, source}]}` | Knowledge |
| `search_similar_incidents` | `description: str, top_k: int?` | `{incidents: list[{id, root_cause, resolution, similarity}]}` | Memory |

### 5.3 写工具（safety_level: write_confirm）

所有写工具在执行前通过 WAL 记录回滚命令。

#### K8s 修复

| 工具名 | 输入 | 回滚 | 审批 |
|--------|------|------|------|
| `restart_pod` | `pod, namespace` | 无需（K8s 自动重建） | auto_approve |
| `scale_deployment` | `name, namespace, replicas` | 恢复原 replicas | human_confirm |
| `cordon_node` | `node` | `uncordon` | human_confirm |
| `uncordon_node` | `node` | — | auto_approve |
| `evict_pod` | `pod, namespace` | 无需 | human_confirm |

#### 网络修复

| 工具名 | 输入 | 回滚 | 审批 |
|--------|------|------|------|
| `reset_switch_port` | `switch, port` | — | human_confirm |
| `configure_pfc` | `switch, port, enable, priorities` | 恢复原配置 | human_confirm |
| `configure_ecn` | `switch, port, params` | 恢复原配置 | human_confirm |
| `reset_nic` | `node, interface` | — | human_confirm |

#### BMC 修复

| 工具名 | 输入 | 回滚 | 审批 |
|--------|------|------|------|
| `set_fan_override` | `node, fan_index, pwm` | 恢复自动控制 | human_confirm |
| `power_cycle_node` | `node` | — | human_confirm |
| `clear_sel_log` | `node` | — | auto_approve |

#### OS / 服务修复

| 工具名 | 输入 | 回滚 | 审批 |
|--------|------|------|------|
| `kill_process` | `node, pid_or_name` | — | human_confirm |
| `restart_service` | `node, service_name` | — | human_confirm |
| `reset_gpu` | `node, gpu_index` | — | human_confirm |
| `clear_disk_cache` | `node` | — | auto_approve |

#### 平台修复

| 工具名 | 输入 | 回滚 | 审批 |
|--------|------|------|------|
| `redeploy_service` | `name` | — | human_confirm |
| `update_service_replicas` | `name, replicas` | 恢复原 replicas | human_confirm |
| `restart_celery_worker` | — | — | auto_approve |

---

## 6. Channel 层

### 6.1 共享 Channel（与 fault-injector / load-simulator 统一）

以下 Channel 实现与 fault-injector、load-simulator 完全共用，放在独立 `channels/` 包中：

```python
class BaseChannel(ABC):
    """Channel 基类 — 统一 dry_run 和 WAL 集成"""

    def __init__(self, dry_run: bool = False,
                 wal: RollbackJournal | None = None):
        self.dry_run = dry_run
        self.wal = wal

    async def execute(self, action: str, params: dict,
                      recovery_action: str | None = None,
                      recovery_params: dict | None = None) -> ChannelResult:
        # 1. 禁止操作检查
        if self._is_forbidden(action, params):
            raise SafetyViolationError(f"{action} is forbidden")

        # 2. 写操作 → WAL 记录
        if recovery_action and self.wal:
            self.wal.record(action, recovery_action, recovery_params)

        # 3. dry_run → 仅日志
        if self.dry_run:
            logger.info(f"[DRY-RUN] {action}: {params}")
            return ChannelResult(success=True, dry_run=True)

        return await self._execute_impl(action, params)

    @abstractmethod
    async def _execute_impl(self, action: str, params: dict) -> ChannelResult:
        pass
```

| Channel | 关键方法 | 来源 |
|---------|----------|------|
| **SSHChannel** | `run_command()`, `run_stress_ng()`, `run_tc_netem()` | fault-injector |
| **RedfishChannel** | `authenticate()`, `get_thermal()`, `set_fan_control()`, `reset_system()` | fault-injector |
| **SwitchChannel** | `ssh_cli_execute()`, `shutdown_port()`, `netconf_edit()` | fault-injector |
| **K8sChannel** | `get_pods()`, `delete_pod()`, `scale_deployment()`, `exec_command()` | fault-injector |
| **CubeStudioChannel** | `create_inference_service()`, `deploy_service()`, `get_service_status()` | load-simulator |
| **PrometheusChannel** | `query_instant()`, `query_range()`, `collect_baseline()` | 共享 |

### 6.2 SRE Agent 新增 Channel

#### LogChannel — 统一日志访问

```python
class LogChannel:
    """统一日志访问：K8s Pod 日志 + 系统日志 + Cube Studio 审计日志"""

    def __init__(self, k8s: K8sChannel, ssh: SSHChannel,
                 cube_studio: CubeStudioChannel):
        self.k8s = k8s
        self.ssh = ssh
        self.cube_studio = cube_studio

    async def read_pod_logs(self, pod: str, namespace: str,
                            tail: int = 200, since: str | None = None
                            ) -> list[str]:
        """读取 K8s Pod 日志"""
        return await self.k8s.get_pod_logs(pod, namespace,
                                           tail_lines=tail, since_time=since)

    async def search_pod_logs(self, pattern: str, namespace: str,
                              pod_selector: str | None = None) -> list[dict]:
        """跨 Pod 搜索日志（正则匹配）"""
        pods = await self.k8s.get_pods(namespace=namespace,
                                       label_selector=pod_selector)
        results = []
        for pod in pods:
            logs = await self.read_pod_logs(pod["name"], namespace)
            for line in logs:
                if re.search(pattern, line):
                    results.append({"pod": pod["name"], "line": line})
        return results

    async def read_system_log(self, node: str, log_path: str = "/var/log/syslog",
                              tail: int = 100) -> list[str]:
        """通过 SSH 读取节点系统日志

        P0 安全修复：log_path 使用白名单 + shlex.quote() 防命令注入。
        """
        import shlex
        ALLOWED_LOG_PATHS = {
            "/var/log/syslog", "/var/log/messages", "/var/log/kern.log",
            "/var/log/dmesg", "/var/log/auth.log", "/var/log/gpu-manager.log",
        }
        # 路径白名单校验
        if log_path not in ALLOWED_LOG_PATHS:
            raise SafetyViolationError(
                f"log_path {log_path!r} not in allowed list: {ALLOWED_LOG_PATHS}")
        # 即使白名单也 quote，防御纵深
        output = await self.ssh.run_command(
            node, f"tail -n {int(tail)} {shlex.quote(log_path)}")
        return output.strip().split("\n")

    async def read_dmesg(self, node: str, filter_str: str | None = None
                         ) -> list[str]:
        """读取内核日志

        P0 安全修复：filter_str 使用 shlex.quote() 防命令注入。
        """
        import shlex
        cmd = "dmesg --time-format iso"
        if filter_str:
            # 限制 filter_str 长度并 quote
            if len(filter_str) > 100:
                raise SafetyViolationError("filter_str too long (max 100 chars)")
            cmd += f" | grep -i {shlex.quote(filter_str)}"
        output = await self.ssh.run_command(node, cmd)
        return output.strip().split("\n")
```

#### AlertChannel — 告警源集成

```python
class AlertChannel:
    """告警源集成：Prometheus Alertmanager + Webhook 接收"""

    def __init__(self, alertmanager_url: str):
        self.client = httpx.AsyncClient(base_url=alertmanager_url)

    async def get_active_alerts(self, filter_labels: dict | None = None
                                ) -> list[Alert]:
        """获取当前活跃告警"""
        resp = await self.client.get("/api/v2/alerts",
                                     params={"filter": self._build_filter(filter_labels)})
        return [Alert.model_validate(a) for a in resp.json()]

    async def get_alert_history(self, alert_name: str,
                                lookback: str = "24h") -> list[Alert]:
        """获取告警历史"""
        # 通过 Prometheus query 获取告警触发历史
        pass

    async def silence_alert(self, alert_id: str, duration: str,
                            comment: str) -> str:
        """静默告警（修复期间避免告警风暴）"""
        resp = await self.client.post("/api/v2/silences", json={
            "matchers": [{"name": "alertname", "value": alert_id}],
            "startsAt": datetime.now().isoformat(),
            "endsAt": (datetime.now() + parse_duration(duration)).isoformat(),
            "comment": comment,
            "createdBy": "sre-agent",
        })
        return resp.json()["silenceID"]

class Alert(BaseModel):
    """告警数据结构

    P1 修复：统一为 Pydantic BaseModel（原 @dataclass 与 model_validate() 和
    IncidentRecord(BaseModel) 中的 alert: Alert 字段不兼容）。
    """
    alert_name: str
    severity: Literal["critical", "warning", "info"]
    labels: dict[str, str]                      # {instance, job, namespace, ...}
    annotations: dict[str, str]                 # {summary, description, ...}
    starts_at: datetime
    ends_at: datetime | None = None
    fingerprint: str
    status: Literal["firing", "resolved"]
```

#### OntologyChannel — 拓扑 CRUD

```python
class OntologyChannel:
    """数字孪生 CRUD 操作"""

    def __init__(self, ontology: OntologyGraph):
        self.ontology = ontology

    async def query(self, entity_type: str,
                    filters: dict | None = None) -> list[dict]:
        return self.ontology.find_entities(entity_type, filters)

    async def get_blast_radius(self, entity_id: str) -> dict:
        return self.ontology.get_blast_radius(entity_id)

    async def get_path(self, from_id: str, to_id: str) -> list[str] | None:
        return self.ontology.get_path(from_id, to_id)

    async def refresh_entity(self, entity_id: str) -> dict:
        """实时刷新实体状态（重新从源系统采集）"""
        # 根据实体类型调用对应 discovery 方法
        pass
```

#### KnowledgeBaseChannel — 知识检索

```python
class KnowledgeBaseChannel:
    """知识库 RAG 检索接口"""

    def __init__(self, store: KnowledgeStore):
        self.store = store

    async def search(self, query: str, category: str | None = None,
                     top_k: int = 5) -> list[KnowledgeChunk]:
        return await self.store.search(query, category=category, top_k=top_k)

    async def search_runbook(self, symptom: str) -> list[Runbook]:
        return await self.store.search_runbooks(symptom)
```

---

## 7. 修复引擎（Remediation Engine）

### 7.1 架构原则

修复引擎是**确定性执行器**，不包含 LLM 调用。LLM 在 SRE Agent 的 ReAct 循环中生成 `RemediationPlan`，修复引擎负责安全执行。

```
SRE Agent (ReAct)                    修复引擎 (确定性)
┌──────────────┐                    ┌──────────────────────┐
│ 诊断根因      │ ──RemediationPlan──→│ 审批门控              │
│ 生成修复方案  │                    │ ↓                    │
│              │                    │ WAL 记录              │
│              │                    │ ↓                    │
│              │                    │ 灰度执行 (Canary)     │
│              │                    │ ↓                    │
│              │                    │ 逐步验证              │
│              │                    │ ↓                    │
│              │ ←RemediationResult─│ 结果/回滚             │
└──────────────┘                    └──────────────────────┘
```

### 7.2 修复计划数据模型

```python
class RemediationStep(BaseModel):
    """修复步骤"""
    step_id: int
    description: str                            # 人类可读描述
    tool: str                                   # 工具名
    params: dict                                # 工具参数
    rollback_tool: str | None                   # 回滚工具名
    rollback_params: dict | None                # 回滚参数
    verification: VerificationConfig            # 验证配置
    timeout: int = 60                           # 超时（秒）

class VerificationConfig(BaseModel):
    """步骤验证配置"""
    method: Literal["promql", "tool_call", "wait"]
    query: str | None = None                    # PromQL 查询
    tool: str | None = None                     # 验证工具
    tool_params: dict | None = None
    success_condition: str                      # "value < 500" 或 "status == 'Running'"
    wait_seconds: int = 30                      # 验证前等待时间

class CanaryConfig(BaseModel):
    """灰度部署配置"""
    enabled: bool = True
    target_percentage: float = 0.1              # 首批 10%
    monitor_duration: int = 120                 # 监控窗口（秒）
    success_criteria: list[str]                 # ["p95_latency < 500ms", "error_rate < 1%"]
    max_batches: int = 3                        # 最多分 3 批
    auto_rollback_on_regression: bool = True

class RemediationPlan(BaseModel):
    """完整修复计划"""
    plan_id: str
    root_cause: str
    description: str                            # 修复方案描述
    steps: list[RemediationStep]
    canary: CanaryConfig | None = None
    estimated_impact: str                       # 预估影响
    confidence: float                           # SRE Agent 的置信度
    priority: Literal["P0", "P1", "P2"]

class RemediationResult(BaseModel):
    """修复结果"""
    plan_id: str
    success: bool
    steps_completed: int
    steps_total: int
    failed_step: RemediationStep | None
    rolled_back: bool
    verification_results: list[dict]
    duration_seconds: int
```

### 7.3 灰度部署（Canary）

```python
class CanaryExecutor:
    """灰度部署执行器"""

    async def execute_with_canary(self, plan: RemediationPlan,
                                   targets: list[str]) -> RemediationResult:
        canary = plan.canary
        if not canary or not canary.enabled:
            return await self._execute_all(plan, targets)

        # 分批执行
        batch_size = max(1, int(len(targets) * canary.target_percentage))
        batches = [targets[i:i+batch_size]
                   for i in range(0, len(targets), batch_size)]

        for batch_idx, batch in enumerate(batches[:canary.max_batches]):
            logger.info(f"Canary batch {batch_idx+1}/{len(batches)}: "
                        f"targets={batch}")

            # 对当前批次执行修复
            for target in batch:
                result = await self._execute_for_target(plan, target)
                if not result.success:
                    if canary.auto_rollback_on_regression:
                        await self.wal.recover_all()
                    return RemediationResult(success=False, rolled_back=True, ...)

            # 监控窗口：等待并验证
            logger.info(f"Monitoring canary for {canary.monitor_duration}s...")
            await asyncio.sleep(canary.monitor_duration)

            # 检查成功标准
            for criterion in canary.success_criteria:
                met = await self._check_criterion(criterion)
                if not met:
                    logger.warning(f"Canary criterion failed: {criterion}")
                    if canary.auto_rollback_on_regression:
                        await self.wal.recover_all()
                    return RemediationResult(success=False, rolled_back=True, ...)

            logger.info(f"Canary batch {batch_idx+1} passed, expanding...")

        return RemediationResult(success=True, ...)
```

### 7.4 审批门控

```python
class ApprovalGate:
    """修复审批门控"""

    # 自动审批：低风险操作
    AUTO_APPROVE = {
        "restart_pod", "clear_disk_cache", "clear_sel_log",
        "uncordon_node", "restart_celery_worker",
    }

    # 人工确认：中风险操作
    HUMAN_CONFIRM = {
        "scale_deployment", "cordon_node", "configure_pfc",
        "configure_ecn", "reset_switch_port", "reset_nic",
        "kill_process", "restart_service", "reset_gpu",
        "set_fan_override", "power_cycle_node",
        "redeploy_service", "update_service_replicas",
    }

    # 硬编码拦截：高危操作
    BLOCKED = {
        "delete_namespace", "delete_etcd", "factory_reset_bmc",
        "factory_reset_switch", "format_disk",
    }

    async def request_approval(self, plan: RemediationPlan) -> ApprovalResult:
        tools_used = {step.tool for step in plan.steps}

        # 检查是否包含被拦截的操作
        blocked = tools_used & self.BLOCKED
        if blocked:
            return ApprovalResult(approved=False,
                                 reason=f"Blocked operations: {blocked}")

        # 检查是否全部可自动审批
        needs_human = tools_used & self.HUMAN_CONFIRM
        if not needs_human:
            return ApprovalResult(approved=True, method="auto")

        # 需要人工确认 → 推送到 GUI / CLI
        return await self._request_human_approval(plan, needs_human)

    async def _request_human_approval(self, plan: RemediationPlan,
                                       needs_human: set) -> ApprovalResult:
        """通过 WebSocket 推送审批请求到 GUI"""
        approval_request = {
            "plan_id": plan.plan_id,
            "description": plan.description,
            "steps": [s.model_dump() for s in plan.steps],
            "needs_confirmation": list(needs_human),
            "confidence": plan.confidence,
        }
        # 等待用户响应（超时 300s）
        # P0 修复：asyncio.Queue.get() 不支持 timeout 参数，
        # 改用 asyncio.wait_for 包装
        response = await asyncio.wait_for(
            self.approval_queue.get(), timeout=300)
        return ApprovalResult(
            approved=response["approved"],
            method="human",
            approver=response.get("user"),
        )
```

### 7.5 WAL 回滚集成

与 fault-injector 相同的 WAL 模式：

```python
class RemediationEngine:
    """修复引擎：确定性执行 + WAL + 灰度

    P1-4 修复：构造函数显式注入 PrometheusChannel 依赖。
    """

    def __init__(self, tool_registry: ToolRegistry,
                 approval_gate: ApprovalGate,
                 wal: RollbackJournal,
                 prometheus: PrometheusChannel):
        self.tools = tool_registry
        self.approval = approval_gate
        self.wal = wal
        self.prometheus = prometheus                # P1-4: 显式注入
        self.canary = CanaryExecutor(wal=wal)

    async def execute(self, plan: RemediationPlan) -> RemediationResult:
        # 1. 审批
        approval = await self.approval.request_approval(plan)
        if not approval.approved:
            return RemediationResult(success=False,
                                    reason=f"Approval denied: {approval.reason}")

        # 2. 逐步执行
        completed = 0
        try:
            for step in plan.steps:
                # WAL 记录（写前日志）
                if step.rollback_tool:
                    self.wal.record(
                        fault_id=f"{plan.plan_id}-step-{step.step_id}",
                        recover_action=step.rollback_tool,
                        recover_params=step.rollback_params,
                    )

                # 执行
                result = await self.tools.execute(
                    step.tool, step.params, safety_level="write_confirm")

                # 验证
                await asyncio.sleep(step.verification.wait_seconds)
                verified = await self._verify(step.verification)
                if not verified:
                    logger.error(f"Step {step.step_id} verification failed, rolling back")
                    await self.wal.recover_all()
                    return RemediationResult(
                        success=False, failed_step=step,
                        rolled_back=True, steps_completed=completed)

                completed += 1

        except Exception as e:
            logger.error(f"Remediation failed: {e}, rolling back")
            await self.wal.recover_all()
            return RemediationResult(success=False, rolled_back=True,
                                    steps_completed=completed)

        return RemediationResult(success=True, steps_completed=completed,
                                steps_total=len(plan.steps))

    async def _verify(self, config: VerificationConfig) -> bool:
        """验证修复步骤结果

        P0 安全修复：替换 eval() 为安全的白名单条件求值器。
        原实现使用 eval(config.success_condition)，success_condition 来自
        LLM 生成的 RemediationPlan，存在任意代码执行风险。
        """
        if config.method == "promql":
            value = await self.prometheus.query_instant(config.query)
            return safe_eval_condition(config.success_condition, {"value": value})
        elif config.method == "tool_call":
            result = await self.tools.execute(config.tool, config.tool_params,
                                             safety_level="read_only")
            return safe_eval_condition(config.success_condition, {"result": result})
        elif config.method == "wait":
            await asyncio.sleep(config.wait_seconds)
            return True


# ─── P0 安全修复：白名单条件求值器（替代 eval）───

import operator as op
import re

SAFE_OPERATORS = {
    "<": op.lt, ">": op.gt, "<=": op.le, ">=": op.ge,
    "==": op.eq, "!=": op.ne,
}
# 匹配 "field op literal" 三元组，如 "value < 500", "status == 'Running'"
CONDITION_PATTERN = re.compile(
    r"^(\w+(?:\.\w+)*)\s*(<=?|>=?|[!=]=)\s*(.+)$"
)

def safe_eval_condition(condition: str, variables: dict) -> bool:
    """安全的条件求值：仅支持 'field op literal' 格式。

    支持的条件格式：
      "value < 500"
      "status == 'Running'"
      "error_rate < 0.01"
      "result.status == 'healthy'"

    不支持任意 Python 表达式、函数调用、import 等。
    """
    match = CONDITION_PATTERN.match(condition.strip())
    if not match:
        raise ValueError(f"Invalid condition format: {condition!r}. "
                         f"Expected 'field op literal', e.g. 'value < 500'")

    field_path, operator_str, literal_str = match.groups()
    op_func = SAFE_OPERATORS.get(operator_str)
    if not op_func:
        raise ValueError(f"Unsupported operator: {operator_str!r}")

    # 解析字段值（支持嵌套如 result.status）
    value = variables
    for key in field_path.split("."):
        if isinstance(value, dict):
            value = value[key]
        else:
            value = getattr(value, key)

    # 解析字面量
    literal_str = literal_str.strip()
    if literal_str.startswith(("'", '"')) and literal_str.endswith(("'", '"')):
        literal_value = literal_str[1:-1]
    else:
        try:
            literal_value = int(literal_str)
        except ValueError:
            try:
                literal_value = float(literal_str)
            except ValueError:
                literal_value = literal_str

    return op_func(value, literal_value)
```

---

## 8. 知识库（Knowledge Base）

### 8.1 RAG 架构

```
文档源                          摄入 Pipeline                      运行时检索
┌──────────────────┐           ┌─────────────────┐              ┌──────────────┐
│ 硬件手册 (PDF)    │──→        │ PDF → 文本提取   │              │ SRE Agent    │
│ RDMA/RoCEv2 规范  │    ┌──→  │ Markdown → 分块  │──Embedding──→│ search_      │
│ vLLM 文档         │    │     │ YAML → 结构化    │              │ knowledge()  │
│ K8s 文档          │────┘     │ 512 token chunks │              │      ↓       │
│ Cube Studio 文档  │          │ 64 token overlap │              │ top-k 语义   │
│ 运维 Runbook      │          └─────────────────┘              │ 检索         │
│ 厂商 KB 文章      │                    │                      └──────────────┘
└──────────────────┘                    ▼
                             ┌─────────────────┐
                             │ ChromaDB / Milvus│
                             │ 向量存储          │
                             │ · collection per │
                             │   category       │
                             │ · metadata 过滤  │
                             └─────────────────┘
```

### 8.2 知识摄入

```python
class KnowledgeIngestor:
    """文档摄入 Pipeline"""

    def __init__(self, store: KnowledgeStore, chunker: TextChunker):
        self.store = store
        self.chunker = chunker

    async def ingest_pdf(self, path: str, category: str) -> int:
        """PDF → 文本 → 分块 → 嵌入 → 存储"""
        text = extract_text_from_pdf(path)
        chunks = self.chunker.chunk(text, chunk_size=512, overlap=64)
        for chunk in chunks:
            await self.store.add(
                content=chunk.text,
                metadata={
                    "source": path,
                    "category": category,
                    "page": chunk.page,
                },
            )
        return len(chunks)

    async def ingest_markdown(self, path: str, category: str) -> int:
        """Markdown → 按标题分块 → 嵌入 → 存储"""
        sections = split_markdown_by_headers(path)
        for section in sections:
            chunks = self.chunker.chunk(section.text, chunk_size=512, overlap=64)
            for chunk in chunks:
                await self.store.add(
                    content=chunk.text,
                    metadata={
                        "source": path,
                        "category": category,
                        "section": section.header,
                    },
                )
        return sum(len(self.chunker.chunk(s.text)) for s in sections)

    async def ingest_runbook(self, path: str) -> None:
        """结构化 Runbook（YAML）→ 直接索引"""
        runbook = Runbook.model_validate(yaml.safe_load(open(path)))
        await self.store.add_runbook(runbook)
```

### 8.3 Runbook 格式

```yaml
# runbooks/vllm-p95-high.yaml
name: "vLLM P95 延迟过高"
category: "inference"
symptoms:
  - "vLLM P95 latency > 500ms"
  - "inference request queue growing"
  - "GPU utilization abnormally high or low"
diagnosis_steps:
  - description: "检查 GPU 利用率和显存"
    tool: "get_gpu_metrics"
    expected: "GPU util 60-90% 正常，>95% 可能资源争用"
  - description: "检查推理服务 Pod 状态"
    tool: "get_pods"
    params: {namespace: "service", labels: {app: "vllm"}}
  - description: "检查网络延迟"
    tool: "check_nic_errors"
    expected: "无 CRC 错误，无大量丢包"
  - description: "检查温度"
    tool: "get_thermal_status"
    expected: "GPU 温度 < 85°C"
fix_steps:
  - condition: "GPU 资源争用（其他进程占用）"
    action: "终止争用进程"
    tool: "kill_process"
    verification: "P95 恢复到 < 500ms"
  - condition: "GPU 温度过高导致降频"
    action: "调整风扇策略"
    tool: "set_fan_override"
    verification: "温度降低，P95 恢复"
  - condition: "网络丢包导致重传"
    action: "检查并修复交换机端口/ECN 配置"
    tool: "configure_ecn"
tags: ["vllm", "latency", "gpu", "inference"]
```

### 8.4 知识存储

```python
class KnowledgeStore:
    """知识库存储 — ChromaDB"""

    def __init__(self, persist_dir: str = "./knowledge_db"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name="sre_knowledge",
            metadata={"hnsw:space": "cosine"},
        )

    async def add(self, content: str, metadata: dict) -> None:
        doc_id = hashlib.sha256(content.encode()).hexdigest()[:16]
        self.collection.add(
            documents=[content],
            metadatas=[metadata],
            ids=[doc_id],
        )

    async def search(self, query: str, category: str | None = None,
                     top_k: int = 5) -> list[KnowledgeChunk]:
        where = {"category": category} if category else None
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where,
        )
        return [
            KnowledgeChunk(
                content=doc,
                source=meta.get("source", ""),
                category=meta.get("category", ""),
                score=1 - dist,  # ChromaDB 返回距离，转为相似度
            )
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

@dataclass
class KnowledgeChunk:
    content: str
    source: str
    category: str
    score: float
```

---

## 9. 记忆系统（Memory System）

### 9.1 设计目标

需求："保证每个 AIDC 的运维越用越懂所处理的 AIDC"。

记忆系统为每个 AIDC 维护独立的记忆库，包含三类记忆：

| 记忆类型 | 内容 | 用途 |
|----------|------|------|
| 事件记忆 | 历史事件完整记录（告警、诊断过程、根因、修复、结果） | 相似事件快速定位 |
| 模式记忆 | 从多次事件中提取的关联模式 | 系统提示词增强 |
| 配置记忆 | AIDC 专属基线值、安全阈值、自定义规则 | 诊断参考值 |

### 9.2 事件记忆

```python
class IncidentRecord(BaseModel):
    """完整事件记录"""
    incident_id: str                            # UUID
    aidc_id: str                                # AIDC 实例标识
    timestamp: datetime                         # 事件发生时间
    alert: Alert                                # 原始告警
    symptoms: list[str]                         # 症状列表
    diagnosis_trace: list[dict]                 # 完整思考过程
    root_cause: str                             # 根因
    root_cause_layer: str                       # 故障层
    root_cause_entities: list[str]              # 关联的 Ontology 实体 ID
    hypotheses_tested: list[Hypothesis]         # 测试过的假设
    remediation_applied: RemediationPlan | None # 执行的修复方案
    outcome: Literal["resolved", "partially_resolved", "failed", "escalated"]
    resolution_time_seconds: int                # 解决耗时
    engineer_feedback: str | None               # 工程师反馈
    tags: list[str]                             # 标签
```

### 9.3 模式记忆

```python
class LearnedPattern(BaseModel):
    """从多次事件中学习到的关联模式"""
    pattern_id: str
    aidc_id: str
    symptom_signature: list[str]                # 触发症状组合
    root_cause: str                             # 通常根因
    effective_fix: str                          # 有效修复方法
    occurrence_count: int                       # 出现次数
    confidence: float                           # 置信度 (0.0 ~ 1.0)
    first_seen: datetime
    last_seen: datetime
    example_incidents: list[str]                # 关联事件 ID

    def should_suggest(self) -> bool:
        """是否应该主动建议此模式"""
        return self.occurrence_count >= 3 and self.confidence >= 0.7
```

### 9.4 记忆存储与检索

```python
class MemoryStore:
    """记忆存储 — aiosqlite per AIDC

    P1-10 修复：sqlite3 替换为 aiosqlite 异步库，避免阻塞事件循环。
    ChromaDB 同步调用包裹在 asyncio.to_thread() 中。
    """

    def __init__(self, aidc_id: str, db_dir: str = "./memory"):
        self.aidc_id = aidc_id
        self.db_dir = db_dir
        self.db_path = f"{db_dir}/{aidc_id}.db"
        self._db: aiosqlite.Connection | None = None
        # 向量索引用于语义搜索（ChromaDB 仍为同步，通过 to_thread 调用）
        self.vector_index = chromadb.PersistentClient(
            path=f"{db_dir}/{aidc_id}_vectors")
        self.incidents_collection = self.vector_index.get_or_create_collection(
            "incidents")

    async def connect(self) -> None:
        """异步初始化数据库连接"""
        self._db = await aiosqlite.connect(self.db_path)
        await self._init_tables()

    async def record_incident(self, session: DiagnosisSession) -> str:
        """记录完整事件"""
        record = IncidentRecord(
            incident_id=str(uuid4()),
            aidc_id=self.aidc_id,
            timestamp=datetime.now(),
            alert=session.alert,
            symptoms=self._extract_symptoms(session),
            diagnosis_trace=[s.to_dict() for s in session.trace.steps],
            root_cause=session.diagnosis.root_cause,
            root_cause_layer=session.diagnosis.root_cause_layer,
            root_cause_entities=session.diagnosis.root_cause_entities,
            hypotheses_tested=session.diagnosis.hypotheses,
            remediation_applied=session.proposed_plan,
            outcome=session.outcome,
            resolution_time_seconds=session.duration_seconds,
            engineer_feedback=None,
            tags=[],
        )
        self._save_to_db(record)

        # 同时写入向量索引（用于语义搜索）
        self.incidents_collection.add(
            documents=[f"{record.root_cause} | {' '.join(record.symptoms)}"],
            metadatas=[{"incident_id": record.incident_id,
                        "root_cause": record.root_cause}],
            ids=[record.incident_id],
        )

        # 更新模式记忆
        await self._update_patterns(record)
        return record.incident_id

    async def search_similar(self, symptoms: dict,
                             top_k: int = 3) -> list[IncidentRecord]:
        """语义搜索相似历史事件"""
        query = " ".join(f"{k}={v}" for k, v in symptoms.items())
        results = self.incidents_collection.query(
            query_texts=[query], n_results=top_k)
        incident_ids = results["ids"][0]
        return [self._load_from_db(iid) for iid in incident_ids]

    async def get_known_patterns(self) -> list[LearnedPattern]:
        """获取所有高置信度模式"""
        patterns = self._load_all_patterns()
        return [p for p in patterns if p.should_suggest()]

    async def _update_patterns(self, record: IncidentRecord) -> None:
        """从新事件中提取/更新模式"""
        symptom_sig = sorted(record.symptoms)
        existing = self._find_pattern_by_symptoms(symptom_sig)
        if existing:
            existing.occurrence_count += 1
            existing.last_seen = record.timestamp
            # P1-9 修复：根据修复结果区分置信度更新方向
            # 原实现无论成功/失败都 +0.1，导致失败模式置信度虚高
            if record.outcome == "resolved":
                existing.confidence = min(1.0, existing.confidence + 0.15)
                existing.example_incidents.append(record.incident_id)
            elif record.outcome == "failed":
                existing.confidence = max(0.0, existing.confidence - 0.1)
            # partially_resolved / escalated 不调整
            self._save_pattern(existing)
        else:
            pattern = LearnedPattern(
                pattern_id=str(uuid4()),
                aidc_id=self.aidc_id,
                symptom_signature=symptom_sig,
                root_cause=record.root_cause,
                effective_fix=record.remediation_applied.description
                    if record.remediation_applied else "",
                occurrence_count=1,
                confidence=0.3,
                first_seen=record.timestamp,
                last_seen=record.timestamp,
                example_incidents=[record.incident_id],
            )
            self._save_pattern(pattern)
```

### 9.5 记忆增强诊断

记忆系统如何加速诊断：

```
新告警到达
    ↓
1. 搜索事件记忆：是否有相似历史事件？
    → 有 (similarity > 0.85): "历史事件 INC-042 的根因是 ECN 配置错误，
       修复方法：corrected_ecn_threshold on sw-200g"
    → 注入 SRE Agent 系统提示词，优先验证此假设

2. 搜索模式记忆：是否匹配已知模式？
    → 匹配 (confidence > 0.7): "此 AIDC 的 gpu-1-3 节点有反复热问题，
       根因通常是 Fan-2 降速"
    → 注入系统提示词，提示优先检查温度

3. 读取配置记忆：此 AIDC 的基线值
    → "此 AIDC 的 vLLM P95 正常基线: 120-180ms"
    → 诊断时有明确的对比参考
```

---

## 10. Skills 框架（Claude Code 即插即用）

### 10.1 设计目标

需求："考虑兼容 anthropic claude code skills"。

采用 **完全复刻 Claude Code skills 机制** 的即插即用设计：

| 特性 | 说明 |
|------|------|
| 目录约定 | `.claude/skills/**/SKILL.md` + `scripts/` + `references/` |
| 即插即用 | 拷贝/更新 skills 目录即生效，无需重新编译/部署 |
| 固定工具数 | Agent 通过 **4 个固定 tool** 发现/加载/执行 skills，不随 skill 数量变化 |
| 按需加载 | 默认只给模型 skills 摘要；需要时再加载 SKILL.md → references → scripts |
| 热更新 | `SkillRegistry` 周期性重新扫描 `**/SKILL.md`，无需重启 |
| 安全管控 | allow/deny/ask 策略 + 路径防逃逸 + 超时 + 输出截断 + 低权限执行 |

### 10.2 Skill 目录约定

兼容 Claude Code 的 `SKILL.md` 格式，支持多根目录：

```
# 内置 Skills（随代码库分发）
sre_agent/skills/builtin/
  vllm-diagnosis/
    SKILL.md                    # 核心：技能描述 + 系统提示词 + 示例
    scripts/
      check_gpu_contention.sh   # 可执行诊断脚本
      parse_vllm_logs.py
    references/
      vllm-tuning-guide.md      # 参考文档
      gpu-pcie-topology.json

  rdma-diagnosis/
    SKILL.md
    scripts/
      check_pfc_storm.sh
    references/
      rdma-troubleshoot.md

# 用户自定义 Skills（可热加载）
sre_agent/skills/custom/
  my-custom-diag/
    SKILL.md
    scripts/...

# 全局 Skills（可选，跨项目共享）
~/.claude/skills/
  shared-network-check/
    SKILL.md
    ...
```

**Skill 发现规则**：
- 递归扫描所有根目录下的 `**/SKILL.md`
- `skill_id` = `SKILL.md` 所在目录的相对路径（如 `vllm-diagnosis`、`subdir/my-skill`）
- 可通过 SKILL.md frontmatter `name` 字段覆盖 skill_id

### 10.3 SKILL.md 格式

```markdown
---
name: sre:vllm-diagnosis
description: 诊断 vLLM 推理服务延迟问题
version: "1.0"
---

# vLLM 推理延迟诊断

## 适用场景
- vLLM P95/P99 延迟超标
- 推理 QPS 下降
- GPU 利用率异常

## 诊断方法论

### 常见根因
1. GPU 资源争用（其他进程占用 GPU 或 PCIe 带宽）
2. GPU 热节流（温度 > 85°C 导致降频）
3. 网络问题（RDMA 异常、丢包、MTU 不匹配）
4. KV Cache 内存不足（OOM 导致请求排队）
5. 平台级瓶颈（Gunicorn Worker 饱和、DB 连接池耗尽）

### 推荐诊断流程
1. 先查延迟指标确认问题范围（哪些 percentile 受影响）
2. 查 GPU 指标（利用率、显存、温度）→ 可执行 `scripts/check_gpu_contention.sh`
3. 查网络指标（RDMA 状态、PFC、NIC 错误）
4. 查 Pod/Node 状态
5. 交叉验证，排除假设

### 关键工具
- `get_gpu_metrics` — GPU 利用率、显存、温度
- `get_gpu_processes` — GPU 进程列表
- `get_inference_latency` — 推理 P50/P95/P99
- `get_thermal_status` — 温度和散热
- `check_rdma_status` — RDMA 设备状态
- `query_prometheus` — 自定义 PromQL

### 可执行脚本
- `scripts/check_gpu_contention.sh` — 检查 GPU 争用（传入 node 参数）
- `scripts/parse_vllm_logs.py` — 分析 vLLM 日志中的 OOM/timeout 模式

### 参考文档
- `references/vllm-tuning-guide.md` — vLLM 性能调优指南
- `references/gpu-pcie-topology.json` — PCIe 拓扑参考

## 示例诊断 Trace

```
告警: vLLM P95 > 500ms
Step 1: get_inference_latency → P50=120ms, P95=680ms
Step 2: get_gpu_metrics → GPU-0 util=98%, GPU-1 util=72%
Step 3: get_gpu_processes → gpu-burn 占用 GPU-0
结论: GPU 资源争用，gpu-burn 进程占用 GPU-0，导致 PCIe 带宽争用
```
```

### 10.4 运行时固定的 4 个工具（核心架构）

Agent 通过固定的 4 个 LangChain `@tool` 与 skills 交互。**新增 skill 不会新增 tool**，
无需修改 Agent 代码或重新部署：

| 工具 | 作用 | 上下文影响 |
|------|------|-----------|
| `list_skills()` | 列出所有可用技能的摘要（ID + name + 短描述） | 极小（仅摘要） |
| `load_skill(skill_id)` | 加载指定 skill 的完整 SKILL.md 内容 | 中等（按需加载） |
| `read_skill_ref(skill_id, path)` | 读取 skill 的 references/ 下文件 | 中等（按需读取） |
| `run_skill(skill_id, script, args)` | 执行 skill 的 scripts/ 下脚本 | 取决于脚本输出 |

```python
from langchain_core.tools import tool
from typing import Any, Dict, List, Optional

# ─── 这 4 个 tool 注册到 LangGraph，永远不变 ───

@tool
def list_skills() -> List[Dict[str, Any]]:
    """列出所有可用的 SRE 诊断技能（仅摘要，不加载全文）。
    返回每个 skill 的 id、名称、描述。
    使用此工具发现适合当前问题的 skill。"""
    skills = registry.list()
    return [
        {
            "skill_id": s.skill_id,
            "name": s.name,
            "description": s.description,
            "has_scripts": s.scripts_dir().exists(),
            "has_references": s.references_dir().exists(),
        }
        for s in skills
    ]

@tool
def load_skill(skill_id: str) -> Dict[str, Any]:
    """加载指定 skill 的完整 SKILL.md 内容。
    包含诊断方法论、推荐步骤、可用脚本和参考文档列表。
    仅在需要某个 skill 的详细指导时调用。"""
    s = registry.get(skill_id)
    if not s:
        return {"ok": False, "error": "skill_not_found"}
    return {
        "ok": True,
        "skill_id": s.skill_id,
        "name": s.name,
        "skill_md": s.read_skill_md(),
        "scripts": [p.name for p in s.scripts_dir().iterdir()]
            if s.scripts_dir().exists() else [],
        "references": [p.name for p in s.references_dir().iterdir()]
            if s.references_dir().exists() else [],
    }

@tool
def read_skill_ref(skill_id: str, path: str) -> Dict[str, Any]:
    """读取 skill 的 references/ 下的参考文件内容。
    path 是相对于 references/ 的路径，如 'vllm-tuning-guide.md'。"""
    s = registry.get(skill_id)
    if not s:
        return {"ok": False, "error": "skill_not_found"}
    try:
        p = registry.resolve_in_skill(s, "references", path)
        if not p.exists():
            return {"ok": False, "error": "ref_not_found", "path": path}
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > 200_000:
            content = content[:200_000] + "\n...[truncated]"
        return {"ok": True, "path": path, "content": content}
    except ValueError as e:
        return {"ok": False, "error": "invalid_path", "detail": str(e)}

@tool
def run_skill(
    skill_id: str, script: str,
    args: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """执行 skill 的 scripts/ 下的脚本。
    script: 相对于 scripts/ 的路径，如 'check_gpu_contention.sh'
    args: 结构化参数 dict，脚本可通过环境变量 SKILL_ARGS_JSON 读取
    timeout_sec: 超时秒数（默认 60s）"""
    s = registry.get(skill_id)
    if not s:
        return {"ok": False, "error": "skill_not_found"}
    # ── 权限策略检查 ──
    decision = policy.check(skill_id, script, args)
    if decision == "deny":
        return {"ok": False, "error": "policy_denied",
                "detail": f"Script {script} in {skill_id} is denied by policy"}
    if decision == "ask":
        return {"ok": False, "error": "approval_required",
                "detail": f"Script {script} requires human approval"}
    # ── 执行 ──
    try:
        p = registry.resolve_in_skill(s, "scripts", script)
        if not p.exists() or p.is_dir():
            return {"ok": False, "error": "script_not_found"}
        result = executor.run_script(
            script_path=p, args=args or {},
            cwd=s.base_dir, timeout_sec=timeout_sec)
        result.update({"skill_id": skill_id, "script": script})
        return result
    except Exception as e:
        return {"ok": False, "error": "execution_failed", "detail": str(e)}
```

### 10.5 SkillRegistry — 热发现与路径安全

```python
import os, re, time, hashlib
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

@dataclass
class Skill:
    skill_id: str
    name: str
    description: str
    base_dir: Path
    skill_md_path: Path
    skill_md_hash: str
    frontmatter: dict

    def read_skill_md(self) -> str:
        data = self.skill_md_path.read_bytes()[:2_000_000]
        return data.decode("utf-8", errors="replace")

    def scripts_dir(self) -> Path:
        return self.base_dir / "scripts"

    def references_dir(self) -> Path:
        return self.base_dir / "references"

class SkillRegistry:
    """
    兼容 Claude Code 目录布局的运行时 Skill 注册表。

    - 扫描多个根目录下的 **/SKILL.md
    - 周期性热刷新（refresh_interval_sec）
    - 路径防逃逸（resolve_in_skill）
    """

    def __init__(self, roots: list[Path], refresh_interval_sec: int = 10):
        self.roots = [Path(r).expanduser().resolve() for r in roots]
        self.refresh_interval_sec = refresh_interval_sec
        self._last_refresh = 0.0
        self._skills: dict[str, Skill] = {}

    def refresh(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_refresh) < self.refresh_interval_sec:
            return
        self._last_refresh = now
        skills: dict[str, Skill] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for skill_md in root.rglob("SKILL.md"):
                try:
                    base_dir = skill_md.parent.resolve()
                    md_bytes = skill_md.read_bytes()[:2_000_000]
                    md_text = md_bytes.decode("utf-8", errors="replace")
                    fm, body = self._parse_frontmatter(md_text)
                    rel = base_dir.relative_to(root)
                    skill_id = str(rel).replace("\\", "/")
                    name = fm.get("name") or base_dir.name
                    desc = fm.get("description") or self._first_line(body) or ""
                    h = hashlib.sha256(md_bytes).hexdigest()
                    skills[skill_id] = Skill(
                        skill_id=skill_id, name=name,
                        description=desc[:240], base_dir=base_dir,
                        skill_md_path=skill_md, skill_md_hash=h,
                        frontmatter=fm,
                    )
                except Exception:
                    continue  # skip broken skills
        self._skills = skills

    def list(self) -> list[Skill]:
        self.refresh()
        return sorted(self._skills.values(), key=lambda x: x.skill_id)

    def get(self, skill_id: str) -> Skill | None:
        self.refresh()
        return self._skills.get(skill_id)

    def resolve_in_skill(self, skill: Skill, subdir: str, rel_path: str) -> Path:
        """解析 scripts/ 或 references/ 下的相对路径，防止路径逃逸。"""
        rel = Path(rel_path)
        if rel.is_absolute():
            raise ValueError("absolute paths not allowed")
        target = (skill.base_dir / subdir / rel).resolve()
        root = (skill.base_dir / subdir).resolve()
        if not str(target).startswith(str(root) + os.sep) and target != root:
            raise ValueError("path traversal blocked")
        return target

    @staticmethod
    def _parse_frontmatter(md: str) -> tuple[dict, str]:
        m = _FRONTMATTER_RE.match(md)
        if not m:
            return {}, md
        fm = {}
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
        return fm, md[m.end():]

    @staticmethod
    def _first_line(s: str) -> str:
        for line in s.splitlines():
            t = line.strip()
            if t:
                return t
        return ""
```

### 10.6 SkillExecutor — 安全脚本执行

```python
import os, json, subprocess, time
from pathlib import Path

class SkillExecutor:
    """
    安全执行 skill scripts/ 下的脚本。

    安全措施：
    - 仅执行 resolve_in_skill 验证过的路径
    - 超时控制（默认 60s）
    - 输出截断（默认 12000 字符）
    - 环境变量白名单（不泄露完整环境）
    - 参数通过 SKILL_ARGS_JSON 环境变量传递（非 shell 拼接）
    """

    def __init__(self, default_timeout_sec: int = 60,
                 max_output_chars: int = 12000,
                 env_allowlist: list[str] | None = None):
        self.default_timeout_sec = default_timeout_sec
        self.max_output_chars = max_output_chars
        self.env_allowlist = env_allowlist or [
            "PATH", "HOME", "USER", "LANG", "LC_ALL"]

    def run_script(self, script_path: Path, args: dict,
                   cwd: Path, timeout_sec: int | None = None) -> dict:
        timeout = timeout_sec or self.default_timeout_sec
        cmd = self._build_cmd(script_path)
        env = {k: os.environ.get(k, "") for k in self.env_allowlist}
        env["SKILL_ARGS_JSON"] = json.dumps(args, ensure_ascii=False)

        start = time.time()
        try:
            p = subprocess.run(
                cmd, cwd=str(cwd), env=env,
                capture_output=True, text=True, timeout=timeout)
            dur_ms = int((time.time() - start) * 1000)
            stdout = self._truncate(p.stdout or "")
            stderr = self._truncate(p.stderr or "")
            data = self._extract_result_json(stdout, cwd)
            return {
                "ok": p.returncode == 0,
                "returncode": p.returncode,
                "stdout": stdout, "stderr": stderr,
                "data": data,
                "duration_ms": dur_ms,
            }
        except subprocess.TimeoutExpired:
            dur_ms = int((time.time() - start) * 1000)
            return {
                "ok": False, "error": "timeout",
                "duration_ms": dur_ms,
                "timeout_sec": timeout,
            }

    def _build_cmd(self, script_path: Path) -> list[str]:
        suffix = script_path.suffix.lower()
        if suffix in (".sh", ".bash"):
            return ["bash", str(script_path)]
        if suffix == ".py":
            return ["python3", str(script_path)]
        if os.access(script_path, os.X_OK):
            return [str(script_path)]
        return ["bash", str(script_path)]

    def _truncate(self, s: str) -> str:
        if len(s) <= self.max_output_chars:
            return s
        return s[:self.max_output_chars] + f"\n...[truncated {len(s)-self.max_output_chars} chars]"

    def _extract_result_json(self, stdout: str, cwd: Path) -> dict | None:
        """提取结构化结果：stdout 中的 RESULT_JSON={...} 或 cwd/output.json"""
        for line in reversed(stdout.splitlines()[-50:]):
            if line.startswith("RESULT_JSON="):
                try:
                    return json.loads(line[len("RESULT_JSON="):].strip())
                except Exception:
                    return None
        out_file = cwd / "output.json"
        if out_file.exists():
            try:
                return json.loads(out_file.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None
```

### 10.7 执行权限策略（Policy Gate）

```python
from typing import Literal

class SkillPolicy:
    """
    Skill 脚本执行的权限策略。

    支持三种决策：
    - allow: 允许执行（默认，内置 skills）
    - deny: 拒绝执行（黑名单或危险操作）
    - ask: 需要人工审批

    可按 skill_id pattern、environment（dev/prod）、脚本后缀分级。
    """

    def __init__(self, default: Literal["allow", "deny", "ask"] = "allow",
                 rules: list[dict] | None = None):
        self.default = default
        self.rules = rules or []
        # 默认规则示例：
        # [
        #   {"pattern": "builtin/*", "action": "allow"},
        #   {"pattern": "custom/*", "action": "ask"},
        #   {"pattern": "danger-*", "action": "deny"},
        # ]

    def check(self, skill_id: str, script: str,
              args: dict | None = None) -> Literal["allow", "deny", "ask"]:
        import fnmatch
        for rule in self.rules:
            pattern = rule.get("pattern", "")
            if fnmatch.fnmatch(skill_id, pattern):
                return rule.get("action", self.default)
            if fnmatch.fnmatch(f"{skill_id}/{script}", pattern):
                return rule.get("action", self.default)
        return self.default
```

### 10.8 LangGraph 集成 — 固定图结构

Skill 的 4 个 tool 与 SRE Agent 的诊断 tool 一起注册到 LangGraph，**图结构不随 skills 变化**：

```python
from skills.runtime.registry import SkillRegistry
from skills.runtime.executor import SkillExecutor
from skills.runtime.tools import build_skill_tools
from skills.runtime.policy import SkillPolicy

# ── 初始化（应用启动时执行一次）──
registry = SkillRegistry(
    roots=[
        Path("./sre_agent/skills/builtin"),      # 内置 skills
        Path("./sre_agent/skills/custom"),        # 自定义 skills
        Path("~/.claude/skills"),                  # 全局 skills
    ],
    refresh_interval_sec=10,
)

executor = SkillExecutor(default_timeout_sec=60, max_output_chars=12000)

policy = SkillPolicy(
    default="allow",
    rules=[
        {"pattern": "builtin/*", "action": "allow"},
        {"pattern": "custom/*", "action": "ask"},     # 自定义 skills 需确认
    ],
)

skill_tools = build_skill_tools(registry, executor, policy)

# ── 合并到 LangGraph Agent ──
# SRE 诊断工具 + Skill 工具 一起 bind
all_tools = sre_read_only_tools + skill_tools  # [query_prometheus, ...] + [list_skills, ...]
llm_with_tools = llm.bind_tools(all_tools)

# LangGraph 图结构不变，只是 ToolNode 包含了 skill tools
tool_node = ToolNode(all_tools)
```

**关键设计点**：
- `list_skills` / `load_skill` / `read_skill_ref` / `run_skill` 这 4 个 tool 是**固定的**
- 新增 / 删除 / 更新 skill 只需修改 skills 目录，无需改代码
- `registry.refresh()` 在每次 agent_node 调用前自动触发，实现热更新
- `run_skill` 执行前先经过 `policy.check()` 权限门控

### 10.9 Agent 使用 Skills 的提示词策略

在 system prompt 中指导 Agent 按需加载 skills（避免上下文膨胀）：

```
## 技能系统 (Skills)

你拥有一套可扩展的诊断技能库。使用流程：
1. 先调用 list_skills() 查看有哪些可用技能
2. 根据当前问题选择最相关的 skill，调用 load_skill(skill_id) 加载详细指导
3. 按照 SKILL.md 中的诊断方法论和推荐步骤进行诊断
4. 需要参考资料时调用 read_skill_ref(skill_id, path)
5. 需要执行自动化检查时调用 run_skill(skill_id, script, args)

注意：
- 不要一次加载多个 skill（避免上下文膨胀）
- 优先使用 skill 推荐的诊断步骤和工具组合
- 脚本执行结果可作为诊断证据
```

### 10.10 内置 Skills

| Skill ID | 名称 | 用途 | scripts | references |
|----------|------|------|---------|------------|
| `vllm-diagnosis` | vLLM 推理延迟诊断 | GPU 争用/热节流/网络/KV Cache | `check_gpu_contention.sh` | `vllm-tuning-guide.md` |
| `rdma-diagnosis` | RDMA/RoCEv2 问题诊断 | PFC 风暴/ECN/链路 flap | `check_pfc_storm.sh` | `rdma-troubleshoot.md` |
| `gpu-health` | GPU 硬件健康检查 | ECC/PCIe/温度/功耗 | `gpu_ecc_check.py` | — |
| `network-diagnosis` | 通用网络问题诊断 | 端口/LLDP/MTU/丢包 | `port_scan.sh` | — |
| `storage-diagnosis` | 存储 I/O 问题诊断 | PVC/磁盘/latency | — | — |
| `platform-health` | Cube Studio 平台健康检查 | API/Worker/DB/队列 | — | — |

### 10.11 自定义 Skill 创建

工程师可以将成功的诊断 trace 保存为新的自定义 Skill（SKILL.md 格式）：

```python
class SkillCreator:
    """从诊断 trace 自动生成 SKILL.md"""

    async def create_from_trace(self, session: DiagnosisSession,
                                 name: str, description: str) -> Path:
        """将诊断会话转化为 SKILL.md 目录结构"""
        skill_dir = Path(f"sre_agent/skills/custom/{name}")
        skill_dir.mkdir(parents=True, exist_ok=True)

        # 生成 SKILL.md
        tools_used = set()
        steps_md = []
        for item in session.trace.steps:
            if isinstance(item, ThinkingStep) and item.tool_name:
                tools_used.add(item.tool_name)
                steps_md.append(
                    f"Step: {item.tool_name}({item.tool_params}) "
                    f"→ {item.thought or ''}")
            elif isinstance(item, Observation):
                steps_md.append(f"Observe: {item.tool} → (result)")

        md_content = f"""---
name: {name}
description: {description}
version: "1.0"
created_from: {session.session_id}
---

# {description}

## 适用场景
- {session.alert.alert_name}

## 根因
{session.diagnosis.root_cause}

## 关键工具
{chr(10).join(f'- `{t}`' for t in sorted(tools_used))}

## 诊断 Trace
```
{chr(10).join(steps_md)}
```
"""
        (skill_dir / "SKILL.md").write_text(md_content, encoding="utf-8")
        return skill_dir
```

### 10.12 生产安全清单

| 安全措施 | 实现 | 状态 |
|----------|------|------|
| 路径防逃逸 | `resolve_in_skill()` — resolve + prefix 校验 | ✓ |
| 执行超时 | `subprocess.run(timeout=)` 默认 60s | ✓ |
| 输出截断 | `max_output_chars=12000` | ✓ |
| 环境变量白名单 | 仅传递 PATH/HOME/USER/LANG | ✓ |
| 参数传递 | `SKILL_ARGS_JSON` 环境变量（非 shell 拼接） | ✓ |
| 权限策略 | `SkillPolicy` allow/deny/ask + pattern 匹配 | ✓ |
| 低权限执行 | 建议：K8s Job/sidecar 或非 root 用户 | 推荐 |
| 审计日志 | skill_id/script/args/returncode/duration 写入 audit.jsonl | 推荐 |
| 容器隔离 | 高危脚本在隔离容器执行（CPU/mem 限制） | 长期 |

---

## 11. Demo 场景

### 11.1 Demo 1: vLLM Latency P95 不达标

#### 场景设定

```
前提：使用 fault-injector 在推理节点注入 GPU 争用
      GPU-0 上运行 gpu-burn 进程，推理服务使用 GPU-1

告警：Prometheus alert "VLLMLatencyP95High"
      vllm_request_duration_seconds{quantile="0.95"} > 0.5
      持续 5 分钟
```

#### Agent 完整诊断 Trace

```
[14:00:00] ┌─ Alert 接收 ─────────────────────────────────────────────┐
           │ alert: VLLMLatencyP95High                                │
           │ severity: critical                                       │
           │ labels: {service="vllm-deepseek", namespace="service"}   │
           │ value: 0.68s (阈值: 0.5s)                               │
           └──────────────────────────────────────────────────────────┘

[14:00:01] ── 构建初始上下文 ──
           · 从 Ontology 获取 vllm-deepseek 服务拓扑:
             InferenceService(vllm-deepseek) → Pod(vllm-xxx-abc123)
               → Node(gpu-1-1) → GPU-0, GPU-1
               → NIC(enp65s0f0) → SwitchPort(sw-200g:HGE1/0/1)
           · 从记忆检索: 无相似历史事件
           · 从知识库检索: vLLM 性能调优文档 (3 chunks)

[14:00:02] ── Step 1 [Think] ──
           "收到 vLLM P95 延迟告警 (680ms > 500ms)。需要确认问题范围并
            列举可能根因。常见原因：
            假设 A: GPU 资源争用（优先级高，最常见）
            假设 B: 网络问题（RDMA 异常、丢包）
            假设 C: KV Cache 内存不足
            假设 D: GPU 热节流
            先查看延迟分布和 GPU 指标。"

[14:00:02] ── Step 2 [Act] ── tool_call
           → get_inference_latency(service="vllm-deepseek")

[14:00:03] ── Step 2 [Observe] ──
           {p50_ms: 145, p95_ms: 680, p99_ms: 1250, qps: 12.3, error_rate: 0.02}
           "P50 正常 (145ms)，P95/P99 明显偏高 → 尾延迟问题，非全面性能下降"

[14:00:03] ── Step 3 [Act] ── tool_call
           → get_gpu_metrics(node="gpu-1-1")

[14:00:04] ── Step 3 [Observe] ──
           {per_gpu: [
             {index: 0, util_pct: 97.2, mem_used_mb: 75800, mem_total_mb: 81920,
              temp_c: 78, power_w: 285},
             {index: 1, util_pct: 68.5, mem_used_mb: 45200, mem_total_mb: 81920,
              temp_c: 72, power_w: 220}
           ]}

[14:00:04] ── Step 4 [Think] ──
           "GPU-0 利用率 97.2%（异常高），GPU-1 利用率 68.5%（正常推理范围）。
            推理服务应该在 GPU-1 上，但 GPU-0 被什么进程占用了？
            GPU-0 高占用可能通过 PCIe 总线争用影响 GPU-1。
            → 假设 A 升级为主要嫌疑
            → 需要查看 GPU-0 上的进程"

[14:00:05] ── Step 5 [Act] ── tool_call
           → get_gpu_processes(node="gpu-1-1")
           (P0 修复：使用专用 get_gpu_processes 工具，替代原错误的 read_system_log)

[14:00:05] ── Step 5 [Observe] ──
           {processes: [
             {pid: 12847, name: "gpu-burn", gpu_uuid: "GPU-0", memory_mb: 74500},
             {pid: 15923, name: "python3", gpu_uuid: "GPU-1", memory_mb: 44800}
           ]}
           "确认: gpu-burn 进程 (PID 12847) 占用 GPU-0 全部显存和算力"

[14:00:06] ── Step 6 [Think] ──
           "根因基本确认：gpu-burn 进程占用 GPU-0，导致 PCIe 总线争用。
            但需要排除其他可能的贡献因素。检查温度（排除假设 D）。"

[14:00:06] ── Step 7 [Act] ── tool_call
           → get_thermal_status(node="gpu-1-1")

[14:00:07] ── Step 7 [Observe] ──
           {temperatures: [{name: "GPU-0", reading: 78, threshold: 90},
                           {name: "GPU-1", reading: 72, threshold: 90},
                           {name: "CPU", reading: 62, threshold: 95}],
            fans: [{name: "Fan-1", speed_rpm: 8500, status: "OK"},
                   {name: "Fan-2", speed_rpm: 8200, status: "OK"}]}
           "温度正常，未触发热节流 → 假设 D 排除"

[14:00:07] ── Step 8 [Act] ── tool_call
           → check_nic_errors(node="gpu-1-1", interface="enp65s0f0")

[14:00:08] ── Step 8 [Observe] ──
           {rx_errors: 0, tx_errors: 0, rx_dropped: 2, tx_dropped: 0, crc_errors: 0}
           "网络无异常 → 假设 B 排除"

[14:00:08] ── Step 9 [Conclude] ──
           根因: GPU 资源争用
           层级: hardware
           置信度: 0.94
           详细: gpu-burn 进程 (PID 12847) 占用 GPU-0 (97.2% util, 74.5GB/80GB VRAM),
                 导致 PCIe 总线带宽争用，影响相邻 GPU-1 上的 vLLM 推理服务。
                 表现为 P95/P99 尾延迟升高，P50 基本不受影响。
           影响范围: vllm-deepseek 推理服务所有请求
           假设验证:
             A: GPU 争用 ✓ confirmed (gpu-burn on GPU-0)
             B: 网络问题 ✗ eliminated (NIC errors = 0)
             C: KV Cache ✗ not tested (GPU-1 mem 45.2/80GB 充足)
             D: 热节流 ✗ eliminated (GPU temp 78°C < 90°C threshold)

[14:00:08] ── Step 10 [Remediate] ──
           修复计划:
             Step 1: kill_process(node="gpu-1-1", pid_or_name="gpu-burn")
                     验证: GPU-0 util < 10% (5s 后)
             Step 2: 等待 60s 让 PCIe 带宽恢复正常
             Step 3: 验证 get_inference_latency → P95 < 500ms
           灰度: 不适用（单节点修复）
           置信度: 0.94

[14:00:09] ── 审批门控 ──
           "kill_process" 需要 human_confirm
           → 推送到 GUI 等待工程师确认

[14:00:15] ── 工程师确认 ── ✓ Approved by admin

[14:00:15] ── 修复执行 ──
           Step 1: kill_process(node="gpu-1-1", pid_or_name="gpu-burn")
           WAL: recorded (无需回滚)
           执行: SSH → kill -9 12847 ✓
           验证 (5s后): GPU-0 util = 3.2% ✓

           Step 2: 等待 60s...

           Step 3: 验证 P95
           get_inference_latency(service="vllm-deepseek")
           → {p50_ms: 125, p95_ms: 310, p99_ms: 480}
           → P95 = 310ms < 500ms ✓

[14:01:20] ── 修复完成 ──
           结果: 成功
           P95: 680ms → 310ms (降低 54%)
           诊断耗时: 8s
           修复耗时: 65s (含等待)
           总耗时: 80s

[14:01:20] ── 记忆记录 ──
           事件写入 memory/aidc-001.db
           模式更新: (vLLM P95 高 + GPU-0 util 异常) → GPU 争用
```

### 11.2 Demo 2: RDMA RoCEv2 异常

#### 场景设定

```
前提：使用 fault-injector 注入 ECN 配置错误
      交换机 sw-200g 的 ECN marking threshold 设为异常低值

告警：自定义监控检测到 NCCL AllReduce 超时
      训练 Pipeline pytorch-training 任务失败
```

#### Agent 完整诊断 Trace

```
[15:00:00] ┌─ Alert 接收 ─────────────────────────────────────────────┐
           │ alert: NCCLTimeout                                       │
           │ severity: critical                                       │
           │ labels: {pipeline="pytorch-training", namespace="pipeline"}│
           │ annotations: {summary="NCCL AllReduce timeout after 300s"}│
           └──────────────────────────────────────────────────────────┘

[15:00:01] ── 构建初始上下文 ──
           · Ontology 查询:
             Pipeline(pytorch-training) → Pod(pytorch-worker-0, pytorch-worker-1)
               → Node(gpu-1-1, gpu-1-2)
               → NIC(enp65s0f0) [RDMA capable, RoCEv2]
               → SwitchPort(sw-200g:HGE1/0/1, sw-200g:HGE1/0/2)
               → Switch(sw-200g)
           · 记忆检索: 无相似事件（首次出现）
           · 知识库: RDMA/RoCEv2 故障排查文档 (4 chunks)

[15:00:02] ── Step 1 [Think] ──
           "NCCL AllReduce 超时，涉及分布式训练的 GPU 间通信。
            此环境使用 RoCEv2 over 200GbE。可能原因：
            假设 A: PFC 死锁（pause frame 风暴导致流量停滞）
            假设 B: ECN 配置错误（标记阈值不当导致过度降速）
            假设 C: 链路 flap（物理层间歇断连）
            假设 D: MTU 不匹配（jumbo frame 设置不一致）
            先检查 RDMA 设备状态和 PFC 计数器。"

[15:00:02] ── Step 2 [Act] ── tool_call (并行)
           → check_rdma_status(node="gpu-1-1")
           → check_rdma_status(node="gpu-1-2")

[15:00:03] ── Step 2 [Observe] ──
           gpu-1-1: {devices: [{name: "mlx5_0", state: "Active",
                     port_state: "LinkUp", link_layer: "Ethernet", rate_gbps: 200}]}
           gpu-1-2: {devices: [{name: "mlx5_0", state: "Active",
                     port_state: "LinkUp", link_layer: "Ethernet", rate_gbps: 200}]}
           "两端 RDMA 设备状态正常，链路 Up → 假设 C (link flap) 可能性降低"

[15:00:03] ── Step 3 [Act] ── tool_call (并行)
           → get_pfc_counters(node="gpu-1-1", interface="enp65s0f0")
           → get_pfc_counters(node="gpu-1-2", interface="enp65s0f0")

[15:00:04] ── Step 3 [Observe] ──
           gpu-1-1: {rx_pause: {3: 4521890}, tx_pause: {3: 12045}}
           gpu-1-2: {rx_pause: {3: 4518234}, tx_pause: {3: 11987}}
           "PFC priority 3 的 rx_pause 计数极高 (>450 万)！
            大量 PFC pause frame 被接收，说明上游（交换机）持续发送 pause。
            → 假设 A (PFC 风暴) 可能性高
            但需要检查为什么交换机发这么多 pause — 可能是 ECN 问题导致"

[15:00:04] ── Step 4 [Act] ── tool_call
           → get_switch_port_status(switch="sw-200g", port="HGE1/0/1")

[15:00:05] ── Step 4 [Observe] ──
           {status: "up", speed: "200Gbps", errors: {input: 0, output: 0, crc: 0},
            pfc_config: {enabled: true, priorities: [3]},
            ecn_config: {enabled: true, marking_threshold: 100}}
           "ECN marking threshold = 100 bytes — 这个值异常低！
            正常应该是 ~150KB-1MB。100 bytes 会导致几乎所有包都被 ECN 标记，
            触发发送端大量降速，最终引发 PFC pause 风暴。"

[15:00:05] ── Step 5 [Act] ── tool_call
           → get_ecn_config(switch="sw-200g")

[15:00:06] ── Step 5 [Observe] ──
           {interfaces: [
             {port: "HGE1/0/1", ecn_enabled: true, marking_threshold: 100},
             {port: "HGE1/0/2", ecn_enabled: true, marking_threshold: 100},
             {port: "HGE1/0/3", ecn_enabled: true, marking_threshold: 150000},
             ...
           ]}
           "端口 1 和 2 的 ECN 阈值 = 100 bytes（异常），
            其他端口 = 150000 bytes（正常）。
            → 仅影响 gpu-1-1 和 gpu-1-2 连接的端口"

[15:00:06] ── Step 6 [Think] ──
           "根因确认：ECN marking threshold 被错误设为 100 bytes（应为 ~150KB）。
            这导致交换机对几乎所有数据包标记 ECN CE，NIC 收到后大量降速，
            触发 PFC pause 风暴，最终 RDMA 传输超时 → NCCL AllReduce 失败。

            假设验证：
            A: PFC 风暴 → 确认（rx_pause 450 万），但这是表象不是根因
            B: ECN 配置错误 → 确认！这是根因（threshold 100 vs 正常 150000）
            C: 链路 flap → 排除（LinkUp, 无 CRC 错误）
            D: MTU 不匹配 → 未测试（但 ECN 已足以解释全部症状）"

[15:00:07] ── Step 7 [Conclude] ──
           根因: ECN 配置错误
           层级: network
           置信度: 0.96
           传播链:
             1. sw-200g:HGE1/0/{1,2} ECN threshold=100B (异常)
             2. → 几乎所有 RDMA 包被 ECN CE 标记
             3. → NIC mlx5_0 收到 CE 后触发 DCQCN 降速
             4. → 缓冲区积压，交换机发送 PFC pause (450 万帧)
             5. → RDMA 传输吞吐降至接近零
             6. → NCCL AllReduce 超时 (>300s)
             7. → pytorch-training Pipeline 失败

[15:00:07] ── Step 8 [Remediate] ──
           修复计划:
             Step 1: configure_ecn(switch="sw-200g", port="HGE1/0/1",
                     params={marking_threshold: 150000})
                     回滚: 恢复为 100（虽然是错误值，但是修复前的状态）
                     验证: get_ecn_config → threshold == 150000
             Step 2: configure_ecn(switch="sw-200g", port="HGE1/0/2",
                     params={marking_threshold: 150000})
                     验证: get_ecn_config → threshold == 150000
             Step 3: 等待 30s 让 PFC 计数器稳定
             Step 4: 验证 PFC pause 增长停止
             Step 5: 重新提交训练 Pipeline
           灰度:
             batch 1: 先修复 HGE1/0/1，观察 60s
             batch 2: 修复 HGE1/0/2
           置信度: 0.96

[15:00:08] ── 审批门控 ──
           "configure_ecn" 需要 human_confirm
           → 推送到 GUI

[15:00:12] ── 工程师确认 ── ✓ Approved

[15:00:12] ── 灰度执行 ──
           Canary batch 1: configure_ecn(sw-200g, HGE1/0/1, threshold=150000)
           WAL: recorded (rollback → threshold=100)
           执行: Switch CLI → "interface HGE1/0/1; qos wred ecn ..." ✓
           监控 60s: PFC rx_pause 增长停止 ✓

           Canary batch 2: configure_ecn(sw-200g, HGE1/0/2, threshold=150000)
           执行: ✓

           验证 (30s后):
           get_pfc_counters(gpu-1-1) → rx_pause 停止增长 ✓
           check_rdma_status → Active, LinkUp ✓

[15:01:45] ── 修复完成 ──
           结果: 成功
           PFC pause: 增长停止
           诊断耗时: 7s
           修复耗时: 93s (含灰度监控)
           后续: 工程师手动重新提交训练 Pipeline

[15:01:45] ── 记忆记录 ──
           事件写入 memory/aidc-001.db
           新模式: (NCCL timeout + PFC pause 激增) → ECN 配置错误
```

---

## 12. 配置 Schema

### 12.1 完整 YAML 配置

```yaml
# sre-agent-config.yaml

# ─── 全局配置 ───
global:
  aidc_id: "aidc-001"                          # AIDC 实例标识
  cube_studio_url: "http://cube-studio.example.com"
  auth:
    method: "jwt"
    username: "admin"
    jwt_password: "${JWT_SECRET}"
  prometheus_url: "http://prometheus-k8s.monitoring:9090"
  alertmanager_url: "http://alertmanager.monitoring:9093"
  log_level: "INFO"

# ─── Agent 配置 ───
agent:
  llm:
    provider: "minimax"                        # minimax | openai-compatible
    model: "minimax-2.1"
    api_base: "https://api.minimax.chat/v1"
    api_key_env: "MINIMAX_API_KEY"
    max_tokens: 8192
    temperature: 0.3
  fallback_llm:                                # 备选 LLM（主 LLM 不可用时）
    provider: "openai-compatible"
    model: "claude-sonnet-4-20250514"
    api_base: "https://api.anthropic.com/v1"
    api_key_env: "ANTHROPIC_API_KEY"
  max_steps: 20                                # 单次诊断最大推理步数（LangGraph should_continue 控制）
  step_timeout: 60                             # 单步超时（asyncio.wait_for 在 agent_node 控制）
  total_timeout: 600                           # 总超时（asyncio.wait_for 在 diagnose 控制）
  max_concurrent_diagnoses: 5                  # P2-8 新增：全局并发诊断数限制
  guardrails_config_dir: "./sre_agent/guardrails"  # NeMo Guardrails 配置目录
  langgraph_checkpoint_db: "./data/langgraph_checkpoints.db"  # LangGraph checkpoint 持久化

# ─── NeMo Agent Toolkit 配置（新增）───
nat:
  workflow_config: "./sre_agent/nat/workflow.yml"
  profiling:
    enabled: true                              # 是否启用 profiling
    output_dir: "./data/nat_profiles"
  evaluation:
    enabled: false                             # 默认关闭，按需开启
    dataset: "./sre_agent/nat/eval_dataset.jsonl"

# ─── Channel 连接配置 ───
channels:
  ssh:
    pool_size: 10
    command_timeout: 60
    connect_timeout: 10
    retry_count: 2
    key_file: "~/.ssh/id_rsa"
  redfish:
    request_timeout: 30
    session_refresh_interval: 600
    verify_ssl: true                               # P2-3 修复：默认开启 TLS 校验
  switch:
    cli_timeout: 30
    netconf_timeout: 30
    netconf_port: 830
  kubernetes:
    kubeconfig: "~/.kube/config"
    request_timeout: 30
    namespaces:
      backend: "infra"
      pipeline: "pipeline"
      service: "service"
      notebook: "jupyter"
  prometheus:
    timeout: 15
  cube_studio:
    timeout: 30
    retry_count: 3

# ─── 数字孪生 / 拓扑发现 ───
ontology:
  db_path: "./data/ontology.db"
  auto_refresh_interval: 3600                  # 自动刷新间隔（秒）
  discovery:
    bmc_ip_ranges:
      - "10.0.100.1-10.0.100.20"
      - "10.1.100.1-10.1.100.10"
    switches:
      - name: "sw-200g"
        type: "H3C S9855-24B8D"
        management: { host: "10.0.200.1", protocol: "ssh",
                      user: "admin", password: "${SWITCH_PASSWORD}" }
      - name: "sw-100g"
        type: "H3C S6850-56HF"
        management: { host: "10.0.200.2", protocol: "ssh",
                      user: "admin", password: "${SWITCH_PASSWORD}" }
    k8s_clusters:
      main:
        kubeconfig: "${channels.kubernetes.kubeconfig}"  # P2-10 修复：引用 channels 配置，避免重复

# ─── 知识库 ───
knowledge_base:
  persist_dir: "./data/knowledge_db"
  embedding_model: "text-embedding-3-small"    # 或本地模型
  sources:
    - path: "./knowledge/hardware/"
      category: "hardware"
    - path: "./knowledge/rdma/"
      category: "rdma"
    - path: "./knowledge/vllm/"
      category: "vllm"
    - path: "./knowledge/k8s/"
      category: "k8s"
    - path: "./knowledge/runbooks/"
      category: "runbook"

# ─── 记忆系统 ───
memory:
  db_dir: "./data/memory"
  vector_index_dir: "./data/memory_vectors"
  pattern_min_occurrences: 3                   # 模式最少出现次数
  pattern_min_confidence: 0.7                  # 模式最低置信度

# ─── 修复引擎 ───
remediation:
  wal_dir: "./data/wal"
  canary:
    enabled: true
    default_target_percentage: 0.1
    default_monitor_duration: 120
    auto_rollback: true
  approval:
    auto_approve_timeout: 300                  # 人工审批超时（秒）
    default_policy: "human_confirm"            # auto_approve | human_confirm
  safety:
    excluded_nodes: []
    dry_run: false
    max_concurrent_remediations: 2

# ─── Skills（Claude Code 兼容即插即用）───
skills:
  roots:                                         # 多根目录扫描
    - "./sre_agent/skills/builtin"               # 内置 skills
    - "./sre_agent/skills/custom"                # 自定义 skills
    - "~/.claude/skills"                         # 全局 skills（可选）
  refresh_interval_sec: 10                       # 热扫描间隔
  executor:
    default_timeout_sec: 60                      # 脚本执行超时
    max_output_chars: 12000                      # 输出截断限制
    env_allowlist: ["PATH", "HOME", "USER", "LANG", "LC_ALL"]
  policy:
    default: "allow"                             # 默认策略
    rules:
      - pattern: "builtin/*"
        action: "allow"
      - pattern: "custom/*"
        action: "ask"                            # 自定义 skills 需确认

# ─── 监控 ───
monitor:
  scrape_interval: 15                          # 指标采集间隔（秒）
  alert_check_interval: 10                     # 告警检查间隔（秒）

# ─── Server ───
server:
  host: "0.0.0.0"
  port: 8080
  cors_origins: ["http://localhost:3000"]
```

### 12.2 Pydantic 配置模型

```python
class AgentLLMConfig(BaseModel):
    provider: Literal["minimax", "openai-compatible"]
    model: str
    api_base: HttpUrl
    api_key_env: str                            # 环境变量名
    max_tokens: int = 8192
    temperature: float = 0.3

class AgentConfig(BaseModel):
    llm: AgentLLMConfig
    fallback_llm: AgentLLMConfig | None = None
    max_steps: int = 20
    step_timeout: int = 60
    total_timeout: int = 600

class SkillExecutorConfig(BaseModel):
    default_timeout_sec: int = 60
    max_output_chars: int = 12000
    env_allowlist: list[str] = ["PATH", "HOME", "USER", "LANG", "LC_ALL"]

class SkillPolicyRule(BaseModel):
    pattern: str
    action: Literal["allow", "deny", "ask"]

class SkillPolicyConfig(BaseModel):
    default: Literal["allow", "deny", "ask"] = "allow"
    rules: list[SkillPolicyRule] = []

class SkillsConfig(BaseModel):
    roots: list[str]                                # 多根目录
    refresh_interval_sec: int = 10
    executor: SkillExecutorConfig = SkillExecutorConfig()
    policy: SkillPolicyConfig = SkillPolicyConfig()

class SREAgentConfig(BaseModel):
    global_: GlobalConfig = Field(alias="global")
    agent: AgentConfig
    channels: ChannelsConfig
    ontology: OntologyConfig
    knowledge_base: KnowledgeConfig
    memory: MemoryConfig
    remediation: RemediationConfig
    skills: SkillsConfig
    nat: NATConfig | None = None                    # NeMo Agent Toolkit
    monitor: MonitorConfig
    server: ServerConfig

    model_config = ConfigDict(populate_by_name=True)
```

---

## 13. CLI & API 接口

### 13.1 CLI（Click）

```bash
# ─── 诊断 ───
# 从告警 JSON 触发诊断
sre-agent diagnose --config config.yaml --alert '{"alert_name": "VLLMLatencyP95High", ...}'

# 从 Alertmanager webhook 监听（持续运行）
sre-agent watch --config config.yaml

# ─── 拓扑发现 ───
# 全量发现
sre-agent discover --config config.yaml

# 增量刷新
sre-agent discover --config config.yaml --refresh-only

# 查看当前拓扑摘要
sre-agent topology --config config.yaml

# ─── 对话 ───
# 交互式对话模式
sre-agent chat --config config.yaml

# ─── 知识管理 ───
# 导入知识文档
sre-agent knowledge ingest --config config.yaml --path ./docs/ --category hardware

# 搜索知识
sre-agent knowledge search --config config.yaml --query "RoCEv2 ECN 配置"

# ─── 记忆 ───
# 查看事件历史
sre-agent memory incidents --config config.yaml --last 10

# 查看已学习的模式
sre-agent memory patterns --config config.yaml

# ─── 修复 ───
# 回滚最近的修复
sre-agent rollback --config config.yaml --session <session_id>

# 恢复中断的会话
sre-agent --resume <session_id>

# ─── 状态 ───
# 查看活跃会话
sre-agent status --config config.yaml

# ─── Server ───
# 启动 API 服务
sre-agent serve --config config.yaml

# ─── 验证 ───
# 配置验证 + 连通性检查
sre-agent validate --config config.yaml
```

### 13.2 REST API（FastAPI）

```python
# server.py
app = FastAPI(title="AIDC Auto SRE Agent")

@app.post("/api/diagnose")
async def diagnose(alert: Alert) -> DiagnosisSession:
    """触发诊断"""
    session = await sre_agent.diagnose(alert)
    return session

@app.post("/api/remediate/{session_id}/approve")
async def approve_remediation(session_id: str,
                               approval: ApprovalInput) -> RemediationResult:
    """审批修复计划"""
    return await remediation_engine.approve_and_execute(session_id, approval)

@app.post("/api/remediate/{session_id}/rollback")
async def rollback(session_id: str) -> RollbackResult:
    """回滚修复"""
    return await remediation_engine.rollback(session_id)

@app.get("/api/ontology")
async def get_topology(entity_type: str | None = None) -> dict:
    """获取拓扑"""
    return ontology.find_entities(entity_type)

@app.get("/api/ontology/{entity_id}/blast-radius")
async def get_blast_radius(entity_id: str) -> dict:
    """获取故障影响半径"""
    return ontology.get_blast_radius(entity_id)

@app.post("/api/discover")
async def trigger_discovery() -> dict:
    """触发拓扑发现"""
    return await discovery_agent.discover_all()

@app.get("/api/memory/incidents")
async def list_incidents(last: int = 10) -> list[IncidentRecord]:
    """查看事件历史"""
    return await memory.list_recent(last)

@app.get("/api/memory/patterns")
async def list_patterns() -> list[LearnedPattern]:
    """查看已学习的模式"""
    return await memory.get_known_patterns()

@app.post("/api/chat")
async def chat(message: ChatMessage) -> ChatResponse:
    """对话接口"""
    return await sre_agent.chat(message)

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> DiagnosisSession:
    """获取诊断会话详情"""
    return session_store.get(session_id)

@app.get("/api/sessions/{session_id}/trace")
async def get_trace(session_id: str) -> ThinkingTrace:
    """获取思考过程"""
    return session_store.get(session_id).trace
```

### 13.3 WebSocket 接口

```python
@app.websocket("/ws/thinking-trace/{session_id}")
async def thinking_trace_ws(websocket: WebSocket, session_id: str):
    """实时思考过程推送"""
    await websocket.accept()
    async for step in session_store.subscribe_trace(session_id):
        await websocket.send_json(step.to_dict())

@app.websocket("/ws/alerts")
async def alerts_ws(websocket: WebSocket):
    """实时告警推送"""
    await websocket.accept()
    async for alert in alert_channel.subscribe():
        await websocket.send_json(alert.model_dump())

@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    """对话式交互 WebSocket"""
    await websocket.accept()
    while True:
        data = await websocket.receive_json()
        message = ChatMessage.model_validate(data)
        async for chunk in sre_agent.chat_stream(message):
            await websocket.send_json({"type": "chunk", "content": chunk})
```

---

## 14. GUI 设计

### 14.1 页面结构

```
┌─────────────────────────────────────────────────────────────────┐
│  AIDC Auto SRE Agent                          [aidc-001] [admin]│
├──────┬──────────────────────────────────────────────────────────┤
│      │                                                          │
│ 导航  │  内容区                                                  │
│      │                                                          │
│ 📊   │  ┌─────────────────────────────────────────────────────┐ │
│ 拓扑  │  │ 当前视图内容                                        │ │
│      │  │                                                     │ │
│ 🔔   │  │                                                     │ │
│ 告警  │  │                                                     │ │
│      │  │                                                     │ │
│ 🔍   │  │                                                     │ │
│ 诊断  │  │                                                     │ │
│      │  │                                                     │ │
│ 🔧   │  │                                                     │ │
│ 修复  │  │                                                     │ │
│      │  │                                                     │ │
│ 💬   │  │                                                     │ │
│ 对话  │  │                                                     │ │
│      │  │                                                     │ │
│ 📚   │  └─────────────────────────────────────────────────────┘ │
│ 知识  │                                                          │
│      │                                                          │
│ 🧠   │                                                          │
│ 记忆  │                                                          │
│      │                                                          │
└──────┴──────────────────────────────────────────────────────────┘
```

### 14.2 核心页面

#### 拓扑视图（Dashboard）
- **D3.js 力导向图**：展示 AIDC 拓扑（节点、GPU、交换机、端口、服务）
- 实体按层级着色：物理层 (蓝)、网络层 (绿)、平台层 (橙)、服务层 (紫)
- 点击实体：展开详情面板（属性、关联实体、历史事件）
- 故障时高亮影响半径（红色边框 + 脉冲动画）
- 右上角：活跃告警计数、最近事件摘要

#### 诊断视图
- **左侧面板**：思考过程 Timeline
  - 每个 ThinkingStep 显示为卡片：思考文本 + 行动类型
  - 每个 Observation 显示为折叠块：工具名 + 返回数据摘要
  - 实时追加（WebSocket 推送）
- **右侧面板**：假设树
  - 树形展示所有假设及其状态（testing / confirmed / eliminated）
  - 每个假设下列出支持/反对证据
- **底部**：最终结论卡片（根因、置信度、影响范围、推荐修复）

#### 修复视图
- **修复计划审批**：显示 RemediationPlan 的每个步骤
  - 工具名、参数、预期结果、回滚方案
  - Approve / Reject 按钮
- **灰度进度**：进度条展示 canary batch 执行状态
  - 每个 batch：目标列表、执行状态、验证结果
  - 监控窗口倒计时
- **回滚控制**：一键回滚按钮 + WAL 条目列表

#### 对话视图
- Chat 界面（类 ChatGPT）
- 消息类型：用户文本、Agent 回答、工具调用结果（折叠）、修复建议（高亮）
- 上下文感知：自动带入当前 AIDC 拓扑和最近告警
- 支持引用历史事件和知识库内容

#### 知识管理
- 文档列表（按分类）
- 上传新文档
- 搜索界面（语义搜索 + 结果预览）
- Runbook 编辑器

#### 记忆视图
- **事件时间线**：按时间展示所有历史事件
- **模式列表**：已学习的模式，包括置信度、出现次数、最近一次
- **统计面板**：MTTR 趋势、常见根因分布、自动修复成功率

### 14.3 技术选型

| 组件 | 技术 |
|------|------|
| 框架 | React 18 + TypeScript |
| UI 库 | Ant Design 5 |
| 拓扑图 | D3.js force-directed graph |
| 图表 | ECharts / Plotly |
| 状态管理 | Zustand |
| WebSocket | native WebSocket API |
| 构建 | Vite |

---

## 15. 安全与权限

### 15.1 多层安全模型

```
┌──────────────────────────────────────────────────────────┐
│ 层级 0: NeMo Guardrails（声明式安全护栏，新增）            │
│ · Input Rails: 防 prompt injection、话题约束、PII 过滤    │
│ · Execution Rails: 工具 I/O 验证（check tool input/output）│
│ · Output Rails: 防泄密、幻觉检测、敏感信息脱敏            │
│ · Dialog Rails: 拒绝破坏性操作请求                        │
├──────────────────────────────────────────────────────────┤
│ 层级 1: 工具级 (Tool Registry + LangChain bind_tools)     │
│ · LLM 仅绑定 read_only 工具（bind_tools）                │
│ · write 工具 schema 仅作为 system prompt 参考             │
│ · write_blocked 工具不暴露给 LLM                          │
├──────────────────────────────────────────────────────────┤
│ 层级 2: Channel 级                                        │
│ · FORBIDDEN_OPERATIONS 硬编码拦截                          │
│ · Shell 参数 shlex.quote() + 路径白名单（P0 修复）        │
│ · 与 fault-injector 共享同一禁止列表                       │
├──────────────────────────────────────────────────────────┤
│ 层级 3: 修复引擎级                                         │
│ · WAL 写前日志（fsync）                                    │
│ · 审批门控（auto / human_confirm / blocked）               │
│ · 灰度执行（canary → monitor → expand/rollback）          │
│ · 条件求值使用白名单操作符（禁止 eval，P0 修复）           │
├──────────────────────────────────────────────────────────┤
│ 层级 4: 架构级                                             │
│ · LLM 永远不直接执行写操作                                 │
│ · 修复引擎是确定性代码，无 LLM 调用                        │
│ · LangGraph max_steps + asyncio.wait_for 超时控制          │
└──────────────────────────────────────────────────────────┘
```

### 15.2 禁止操作（FORBIDDEN_OPERATIONS）

与 fault-injector 共享同一禁止列表：

| Channel | 禁止操作 | 原因 |
|---------|----------|------|
| Redfish | BMC 恢复出厂设置 | 不可逆 |
| Redfish | 修改 BMC 网口 IP | 可能导致 BMC 不可达 |
| K8s | 删除 namespace | 级联删除所有资源 |
| K8s | 删除 etcd 数据 | 集群不可恢复 |
| SSH | `rm -rf /` 类命令 | 全盘删除 |
| SSH | 修改 SSH 服务配置 | 可能导致节点不可达 |
| Switch | 删除管理 VLAN | 交换机不可达 |
| Switch | 恢复出厂设置 | 所有配置丢失 |

### 15.3 审计日志

```python
class AuditLog(BaseModel):
    timestamp: datetime
    session_id: str
    action: str                                 # tool_call | approve | reject | rollback
    tool: str | None
    params: dict | None
    user: str                                   # agent | admin | engineer
    result: Literal["success", "failed", "blocked"]
    details: str

# 所有操作写入 audit.jsonl（append-only）
```

### 15.4 角色权限

| 角色 | 查看诊断 | 查看拓扑 | 审批修复 | 执行修复 | 管理知识库 | 管理记忆 |
|------|---------|---------|---------|---------|-----------|---------|
| viewer | ✓ | ✓ | — | — | — | — |
| operator | ✓ | ✓ | ✓ | ✓ | — | — |
| admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

---

## 16. 对话接口

### 16.1 设计目标

需求："提供对话功能，让现场工程师能得到 agent 的专业支持"。

对话接口不是简单的 chatbot，而是**具有完整工具访问能力的 SRE 助手**。工程师可以用自然语言请求 Agent 执行诊断、查询拓扑、搜索知识。

### 16.2 对话模式（LangGraph）

> **架构变更**：原实现使用手写 tool_call 循环，收到 tool_call 后仅执行工具但
> 未重新向 LLM 续推理（P1-8），导致多步工具链中断。现改为 LangGraph StateGraph，
> 自动处理 tool_call → observation → re-think 循环。
>
> **P2-7 修复**：对话历史设置 max_history=50，超出后截断。

```python
from collections import deque

class ConversationalAgent:
    """对话式 SRE 助手 — 基于 LangGraph + NeMo Guardrails

    P1-8 修复：使用 LangGraph 标准 tool calling loop，自动续推理。
    P2-7 修复：对话历史限制为 max_history 轮。
    """

    MAX_HISTORY = 50                            # 最大历史轮数

    def __init__(self, sre_agent: SREAgent, ontology: OntologyGraph,
                 knowledge: KnowledgeStore, memory: MemoryStore):
        self.sre_agent = sre_agent
        self.ontology = ontology
        self.knowledge = knowledge
        self.memory = memory
        self.conversations: dict[str, deque[dict]] = {}
        self._graph = self._build_chat_graph()

    def _build_chat_graph(self) -> StateGraph:
        """构建对话 LangGraph（复用 SRE Agent 的 guarded LLM + tools）"""
        read_only_tools = self.sre_agent.lc_tools
        llm_with_tools = self.sre_agent.llm.bind_tools(read_only_tools)

        rails_config = RailsConfig.from_path("./sre_agent/guardrails")
        guardrails = RunnableRails(config=rails_config, passthrough=True)

        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            MessagesPlaceholder(variable_name="messages"),
        ])
        guarded_llm = prompt | (guardrails | llm_with_tools)

        async def agent_node(state: MessagesState) -> dict:
            response = await guarded_llm.ainvoke(state["messages"])
            return {"messages": [response]}

        tool_node = ToolNode(read_only_tools)

        graph = StateGraph(MessagesState)
        graph.add_node("agent", agent_node)
        graph.add_node("tools", tool_node)
        graph.set_entry_point("agent")
        graph.add_conditional_edges("agent", tools_condition)
        graph.add_edge("tools", "agent")
        return graph.compile()

    async def chat(self, user_id: str, message: str) -> AsyncIterator[str]:
        """处理用户消息，流式返回回答"""
        history = self.conversations.setdefault(
            user_id, deque(maxlen=self.MAX_HISTORY * 2))  # *2: user+assistant
        history.append({"role": "user", "content": message})

        # 构建上下文
        context = await self._build_chat_context(user_id)

        # 运行 LangGraph 对话图
        result = await self._graph.ainvoke({
            "messages": [
                {"role": "system", "content": self._chat_system_prompt(context)},
                *list(history),
            ]
        })

        # 提取最终 assistant 回复
        final_message = result["messages"][-1]
        full_response = final_message.content if hasattr(final_message, 'content') else str(final_message)
        history.append({"role": "assistant", "content": full_response})
        yield full_response

    def _chat_system_prompt(self, context: dict) -> str:
        return f"""
你是 AIDC 智算数据中心的 SRE 专家助手。你可以帮助现场工程师：
- 诊断系统问题
- 查询 AIDC 拓扑和设备状态
- 搜索运维知识库和历史事件
- 解释告警含义和推荐操作
- 提供运维最佳实践建议

当前 AIDC 概况：
{context['topology_summary']}

最近告警：
{context['recent_alerts']}

已知模式：
{context['known_patterns']}

注意：你只能执行只读操作。如果工程师需要修复操作，
请引导使用 diagnose 命令或 GUI 的修复流程。
"""
```

### 16.3 对话示例

```
工程师: gpu-1-3 节点最近是不是经常温度过高？

Agent:  让我查一下 gpu-1-3 的温度历史。

        [tool_call: query_prometheus(
          promql='DCGM_FI_DEV_GPU_TEMP{node="gpu-1-3"}',
          start="-7d", end="now", step="1h")]

        过去 7 天 gpu-1-3 的 GPU 温度数据如下：
        - GPU-0: 平均 76°C，最高 88°C（超过 85°C 共 3 次）
        - GPU-1: 平均 72°C，最高 81°C（正常）

        [tool_call: search_similar_incidents(
          description="gpu-1-3 temperature high")]

        记忆系统中有 1 条相关记录：
        - INC-028 (3 天前): gpu-1-3 GPU-0 温度 87°C 导致推理延迟上升，
          根因是 Fan-2 转速下降（5200 RPM vs 正常 8500 RPM）。
          修复方法：set_fan_override 临时提速，已提交硬件工单。

        综合来看，gpu-1-3 的 GPU-0 确实存在反复过热问题，主要与 Fan-2
        性能下降有关。建议：
        1. 检查 Fan-2 硬件工单进展
        2. 考虑将 gpu-1-3 的高负载任务临时迁移到其他节点
        3. 如果 Fan-2 短期无法更换，设置持续风扇提速策略

工程师: 能帮我看看当前 Fan-2 的转速吗？

Agent:  [tool_call: get_thermal_status(node="gpu-1-3")]

        当前 gpu-1-3 散热状态：
        - Fan-1: 8500 RPM ✓ 正常
        - Fan-2: 5100 RPM ⚠ 偏低（正常应 > 8000 RPM）
        - GPU-0 温度: 79°C（接近警戒线）
        - GPU-1 温度: 71°C ✓ 正常

        Fan-2 转速仍然偏低。如果需要临时提速，可以通过 GUI 的修复流程
        执行 set_fan_override 操作，或者运行：
        sre-agent diagnose --alert '{"alert_name":"FanSpeedLow","labels":{"node":"gpu-1-3","fan":"Fan-2"}}'
```

### 16.4 对话特性

| 特性 | 说明 |
|------|------|
| 上下文感知 | 自动携带 AIDC 拓扑摘要、最近告警、已知模式 |
| 多轮记忆 | 维护会话历史，支持追问 |
| 工具调用 | 可直接查询指标、日志、拓扑、知识库 |
| 流式输出 | WebSocket 逐 token 推送 |
| 安全边界 | 对话模式仅允许 read_only 工具，修复引导至正式流程 |
| 升级转交 | Agent 判断超出能力时，输出完整上下文供人工接管 |

---

## 17. NeMo Guardrails 配置

### 17.1 Guardrails 目录结构

```
sre_agent/guardrails/
├── config.yml              # 主配置：模型、活跃 rails、通用指令
├── rails/
│   ├── input.co            # 输入护栏（防注入、话题约束）
│   ├── output.co           # 输出护栏（防泄密、脱敏）
│   ├── execution.co        # 执行护栏（工具 I/O 验证）
│   └── dialog.co           # 对话护栏（拒绝破坏性操作）
├── actions.py              # 自定义 NeMo Actions
└── prompts.yml             # 安全检查提示词模板
```

### 17.2 config.yml

```yaml
models:
  - type: main
    engine: openai
    model: ${AGENT_LLM_MODEL}           # 复用 Agent 主模型

  # 安全检查使用轻量模型（降低成本）
  - type: self_check_input
    engine: openai
    model: ${SAFETY_CHECK_MODEL}         # 如 gpt-4o-mini 或 minimax-lite

  - type: self_check_output
    engine: openai
    model: ${SAFETY_CHECK_MODEL}

instructions:
  - type: general
    content: |
      你是 AIDC（智算数据中心）的 SRE 专家 Agent。你只处理以下范围的请求：
      - 基础设施诊断：GPU、网络、存储、K8s、BMC 故障排查
      - 监控指标查询：Prometheus PromQL 查询、日志分析
      - 拓扑查询：数字孪生实体关系、影响范围分析
      - 知识检索：运维手册、Runbook、历史事件
      - 修复建议：提出结构化修复计划（不直接执行）

      你绝不处理以下请求：
      - 与基础设施运维无关的对话
      - 直接执行破坏性操作（删除、格式化、出厂重置）
      - 暴露凭据、密钥、密码等敏感信息
      - 绕过审批流程的修复操作

rails:
  input:
    flows:
      - self check input
      - check jailbreak
  output:
    flows:
      - self check output
  execution:
    flows:
      - check tool input
      - check tool output
```

### 17.3 输入护栏 (input.co)

```colang
define user request destructive action
  "删除这个 pod"
  "kubectl delete"
  "scale down to zero"
  "重启所有节点"
  "drain 这个 node"
  "format disk"
  "rm -rf"

define user attempt prompt injection
  "忽略上面的指令"
  "ignore previous instructions"
  "你现在是一个"
  "system: you are now"

define flow block destructive actions
  user request destructive action
  bot say "破坏性操作需要通过修复审批流程执行。我可以帮你诊断问题并生成修复计划，但无法直接执行写操作。请使用 GUI 的修复流程或 `sre-agent diagnose` 命令。"

define flow block injection attempts
  user attempt prompt injection
  bot say "我只能处理 AIDC 基础设施相关的运维请求。"
```

### 17.4 输出护栏 (output.co)

```colang
define flow self check output
  $is_safe = execute self_check_output
  if not $is_safe
    bot say "（已过滤敏感信息）请通过安全渠道获取详细信息。"
```

### 17.5 执行护栏 (execution.co)

```colang
define flow check tool input
  # 验证工具输入参数安全性
  $tool_name = $context.tool_name
  $tool_input = $context.tool_input
  $is_valid = execute validate_tool_input(tool_name=$tool_name, tool_input=$tool_input)
  if not $is_valid
    bot say "工具输入参数被安全检查拦截。"
    stop

define flow check tool output
  # 过滤工具输出中的敏感信息
  $tool_output = $context.tool_output
  $sanitized = execute sanitize_tool_output(tool_output=$tool_output)
  # 用脱敏后的结果替换原始输出
```

### 17.6 自定义 Actions (actions.py)

```python
from nemoguardrails.actions import action
import re

SENSITIVE_PATTERNS = [
    r"password\s*[:=]\s*\S+",
    r"api[_-]?key\s*[:=]\s*\S+",
    r"token\s*[:=]\s*\S+",
    r"secret\s*[:=]\s*\S+",
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",             # 内部 IP（可配置例外）
]

@action()
async def validate_tool_input(tool_name: str, tool_input: dict) -> bool:
    """验证工具输入参数安全性"""
    # 检查 log_path 注入
    if "log_path" in tool_input:
        ALLOWED_PATHS = {"/var/log/syslog", "/var/log/messages", "/var/log/kern.log"}
        if tool_input["log_path"] not in ALLOWED_PATHS:
            return False
    # 检查 shell 注入字符
    for key, value in tool_input.items():
        if isinstance(value, str) and any(c in value for c in [";", "|", "&", "`", "$("]):
            return False
    return True

@action()
async def sanitize_tool_output(tool_output: str) -> str:
    """脱敏工具输出中的敏感信息"""
    sanitized = tool_output
    for pattern in SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)
    return sanitized
```

---

## 18. NeMo Agent Toolkit 集成

### 18.1 编排方案决策：LangGraph 编排 + NAT 生产基础设施

**决策结论：LangGraph 负责 Agent 编排逻辑，NAT 仅作为生产基础设施层（profiling/evaluation/deployment）。不使用 NAT YAML 的 `react_agent` 进行编排。**

#### 对比分析

| 维度 | NAT YAML `react_agent` | LangGraph `StateGraph` | SRE Agent 需求 |
|------|------------------------|------------------------|----------------|
| **自定义状态** | 仅 `messages` 列表 | 任意 TypedDict（alert, topology, patterns 等） | 需要复杂诊断状态 (**LangGraph**) |
| **条件路由** | 不支持 | `add_conditional_edges` 任意分支 | 诊断→修复→审批多路径 (**LangGraph**) |
| **Human-in-the-loop** | 不支持 | `interrupt_before/after` 原生支持 | 修复审批门控 (**LangGraph**) |
| **并行执行** | 不支持 | `Send()` API 原生支持 | 多节点并行诊断 (**LangGraph**) |
| **Checkpoint 持久化** | 不支持 | `AsyncSqliteSaver` / `PostgresSaver` | 长时诊断断点续传 (**LangGraph**) |
| **工具绑定** | YAML 声明式 | `bind_tools()` + `ToolNode` | 40+ 工具动态注册 (**LangGraph**) |
| **Profiling** | 内置 profiler（token 效率、步骤耗时） | 无（需外部工具） | 开发调优 (**NAT**) |
| **Evaluation** | 内置 evaluator（准确率、轨迹评估） | 无（需 LangSmith 等） | CI/CD 回归测试 (**NAT**) |
| **NIM 部署** | 内置 NIM 优化 | 无 | GPU 推理部署 (**NAT**) |

#### 不采用 NAT YAML 编排的原因

1. **状态不足**：NAT `react_agent` 仅维护 `messages` 列表，无法承载 SRE Agent 所需的结构化状态（alert、topology_summary、similar_incidents、known_patterns、thinking_trace 等）。
2. **无条件路由**：SRE Agent 需要 `diagnose → should_continue → [tools|remediate|end]` 的多路条件分支，NAT 的 `react_agent` 只有固定的 `LLM → Tool → LLM` 循环。
3. **无审批门控**：修复操作需要 human-in-the-loop 审批（`interrupt_before`），NAT 不支持。
4. **无 checkpoint**：诊断可能耗时数分钟，需要断点续传和状态持久化，NAT 不支持。
5. **NVIDIA 官方定位**：NAT 官方文档将自身定位为"生产基础设施层"，推荐与 LangGraph 等框架搭配使用，而非替代。

#### 架构分层

```
┌─────────────────────────────────────────────────┐
│  NAT 生产基础设施层                                │
│  · Profiling: 逐步耗时/token 效率/瓶颈分析         │
│  · Evaluation: 诊断准确率/轨迹评估/RAG 质量         │
│  · Optimization: 提示词优化/超参调优                │
│  · Deployment: NIM 模型部署优化                     │
├─────────────────────────────────────────────────┤
│  NeMo Guardrails 安全层                           │
│  · Input/Output/Execution/Dialog Rails            │
├─────────────────────────────────────────────────┤
│  LangGraph 编排层 ← Agent 核心逻辑在此             │
│  · StateGraph + 条件路由 + ToolNode                │
│  · Human-in-the-loop 审批门控                      │
│  · AsyncSqliteSaver checkpoint 持久化              │
│  · 自定义 SREAgentState（alert/topology/patterns） │
├─────────────────────────────────────────────────┤
│  LangChain 工具层                                  │
│  · @tool 装饰器 + bind_tools()                     │
│  · Channel 抽象 (SSH/K8s/Prometheus/...)           │
└─────────────────────────────────────────────────┘
```

### 18.2 NAT 集成方式：包裹 LangGraph Agent

NAT 不替代 LangGraph 编排，而是 **包裹** LangGraph Agent 进行 profiling 和 evaluation：

```python
# sre_agent/nat/nat_wrapper.py
from nvidia_nat import AgentRunner, EvalRunner, ProfilerConfig

class NATWrappedSREAgent:
    """NAT 包裹层：不改变 Agent 逻辑，仅添加 profiling/evaluation 能力。"""

    def __init__(self, sre_agent: SREAgent):
        self.sre_agent = sre_agent  # LangGraph Agent（§4.2）

    async def run_with_profiling(self, alert: Alert) -> dict:
        """开发阶段：带 profiling 的诊断运行。"""
        profiler = AgentRunner(
            agent_fn=self._agent_fn,
            profiler_config=ProfilerConfig(
                track_tokens=True,
                track_latency=True,
                track_tool_calls=True,
                output_dir="data/nat_profiles/"
            )
        )
        result = await profiler.arun(input={"alert": alert.model_dump()})
        return result

    async def _agent_fn(self, input: dict) -> dict:
        """将 LangGraph Agent 暴露为 NAT 可调用的函数。"""
        alert = Alert.model_validate(input["alert"])
        result = await self.sre_agent.diagnose(alert)
        return result.model_dump() if result else {}
```

### 18.3 NAT 评估配置

```python
# sre_agent/nat/evaluation.py
from nvidia_nat import EvalRunner, EvalConfig, EvalMetric

eval_config = EvalConfig(
    agent_fn=nat_wrapper._agent_fn,
    dataset_path="sre_agent/nat/eval_dataset.jsonl",
    metrics=[
        EvalMetric.TOOL_CALL_ACCURACY,   # 工具调用是否正确
        EvalMetric.TRAJECTORY_MATCH,     # 诊断轨迹是否合理
        EvalMetric.ANSWER_RELEVANCE,     # 根因结论是否相关
        EvalMetric.TOKEN_EFFICIENCY,     # token 使用效率
    ],
    concurrency=4,
)

async def run_evaluation():
    runner = EvalRunner(config=eval_config)
    report = await runner.arun()
    print(f"准确率: {report.accuracy:.2%}")
    print(f"平均 token: {report.avg_tokens}")
    print(f"平均延迟: {report.avg_latency_ms:.0f}ms")
    return report
```

### 18.4 评估数据集格式

```jsonl
{"input": {"alert": {"alert_name": "VLLMLatencyP95High", "node": "gpu-1-1"}}, "expected_root_cause": "GPU 资源争用（gpu-burn 进程）", "expected_tools": ["get_gpu_metrics", "get_gpu_processes"], "expected_severity": "high"}
{"input": {"alert": {"alert_name": "NCCLTimeout", "node": "gpu-2-1"}}, "expected_root_cause": "ECN 配置错误", "expected_tools": ["check_rdma_status", "get_pfc_counters", "get_ecn_config"], "expected_severity": "critical"}
```

### 18.5 CLI 使用

```bash
# Profiling：分析单个告警的诊断性能
python -m sre_agent.nat.nat_wrapper profile \
    --alert '{"alert_name": "VLLMLatencyP95High", "node": "gpu-1-1"}'

# Evaluation：批量评估诊断准确率（用于 CI/CD）
python -m sre_agent.nat.evaluation run \
    --dataset sre_agent/nat/eval_dataset.jsonl \
    --output data/nat_reports/eval_report.json

# 查看 profiling 报告
cat data/nat_profiles/profiling_report.txt
```

---

## 附录 A: 需求覆盖矩阵

| 需求 | 对应章节 | 状态 |
|------|----------|------|
| 1. 自动检测 AIDC 拓扑（BMC/Switch/Host/Platform） | 3.5 DiscoveryAgent | ✓ |
| 2. 数字孪生（Palantir Ontology 风格） | 3.1-3.4 Ontology 模型 | ✓ |
| 3a. 根因分析 + 试验→验证→排查循环 | 4.2 LangGraph ReAct Agent | ✓ |
| 3b. 灰度发布 fix | 7.3 CanaryExecutor | ✓ |
| 3c. 显示 Agent 思考过程 | 4.3 ThinkingTrace + WebSocket | ✓ |
| 3d. 工具对接 log/telemetry/alert/platform/OS | 5.2 只读工具集 (40+ 工具) | ✓ |
| 4. 兼容 Claude Code skills（即插即用） | 10.1-10.12 Skills 框架（SKILL.md + 4 固定 tool + 热加载） | ✓ 重构 |
| 5. 知识库集成 | 8.1-8.4 RAG 知识库 | ✓ |
| 6. 记忆库（越用越懂） | 9.1-9.5 记忆系统 | ✓ |
| 7. 对话功能 | 16.1-16.4 对话接口 (LangGraph) | ✓ |
| 8. GUI 展示 | 14.1-14.3 GUI 设计 | ✓ |
| 9. Agent 安全护栏 | 17.1-17.6 NeMo Guardrails | ✓ 新增 |
| 10. Agent 可观测与调优 | 18.1-18.5 NeMo Agent Toolkit（包裹层，非编排） | ✓ 重构 |
| Demo 1: vLLM P95 诊断修复 | 11.1 完整 Trace (get_gpu_processes 修正) | ✓ |
| Demo 2: RDMA RoCEv2 诊断修复 | 11.2 完整 Trace | ✓ |
| 集成 fault-injector 智能部分 | 4.7 DiagnosisResult (扩展) | ✓ |
| 集成 load-simulator 智能部分 | 5.2 (ThresholdEngine 集成) | ✓ |

## 附录 B: 与 fault-injector / load-simulator 共享组件

| 组件 | 模块路径 | 共享方式 |
|------|----------|----------|
| SSHChannel | `channels/ssh.py` | 同一代码库 |
| RedfishChannel | `channels/redfish.py` | 同一代码库 |
| SwitchChannel | `channels/switch.py` | 同一代码库 |
| K8sChannel | `channels/k8s.py` | 同一代码库 |
| CubeStudioChannel | `channels/cube_studio.py` | 同一代码库 |
| PrometheusChannel | `channels/prometheus.py` | 同一代码库 |
| RollbackJournal (WAL) | `remediation/wal.py` | 同一代码库 |
| SafetyGuard | `safety/guard.py` | 同一代码库 |
| FORBIDDEN_OPERATIONS | `safety/forbidden.py` | 同一代码库 |
| Pydantic Config 基类 | `config.py` | 同一代码库 |
| DiagnosisResult | `agent/models.py` | 扩展自 fault-injector |
| ThresholdEngine | `tools/threshold.py` | 扩展自 load-simulator |

## 附录 C: 设计评审问题解决记录

> 本附录合并了 `review.md` 和 `AIDC-auto-SRE-review.md` 中的评审发现，
> 并记录每个问题的解决状态。

### P0 — 已解决

| # | 问题 | 解决方式 | 文档位置 |
|---|------|---------|----------|
| P0-1 | `eval()` 远程代码执行：`_verify()` 使用 `eval(success_condition)` | 替换为 `safe_eval_condition()` 白名单操作符求值器 | §7.5 `_verify()` |
| P0-2 | SSH 命令注入：`log_path`、`filter_str` 未 shell escape | `shlex.quote()` + 路径白名单 | §6.2 LogChannel |
| P0-3 | API/WebSocket 缺少鉴权 | 增加安全层级 0: NeMo Guardrails input/output rails | §15.1, §17 |
| P0-4 | `asyncio.Queue.get(timeout=300)` 不支持 timeout | 改为 `asyncio.wait_for(queue.get(), timeout=300)` | §7.4 ApprovalGate |
| P0-5 | Demo 1 工具调用不匹配（`read_system_log` 无 `command` 参数） | 新增 `get_gpu_processes` 专用工具，修正 Demo Trace | §5.2, §4.5, §11.1 |

### P1 — 已解决

| # | 问题 | 解决方式 | 文档位置 |
|---|------|---------|----------|
| P1-1 | `get_blast_radius()` BFS 方向错误 | 双向遍历 out_edges + in_edges，按关系类型决定方向 | §3.4 OntologyGraph |
| P1-2 | `RemediationResult` 返回值不一致 | 统一 Result schema（待实现时完善） | §7.2 |
| P1-3 | Alert 混用 `@dataclass` 和 Pydantic | 统一为 `BaseModel` | §6.2 AlertChannel |
| P1-4 | `_verify()` 使用 `self.prometheus` 但未注入 | 构造函数显式注入 `PrometheusChannel` | §7.5 RemediationEngine |
| P1-5 | LLM 看不到 write 工具 schema | write 工具描述附加到 system prompt（只读参考） | §4.2, §5.1 |
| P1-6 | ReAct timeout 未实现 | `asyncio.wait_for` 在 agent_node（步级）和 diagnose（会话级）双层控制 | §4.2 |
| P1-7 | `gather(return_exceptions=True)` 后未检查结果 | 待实现时增加错误聚合和数据新鲜度标注 | §3.5 |
| P1-8 | 对话工具调用循环不完整 | 改用 LangGraph StateGraph 自动处理 tool_call → re-think 循环 | §16.2 |
| P1-9 | 置信度更新不区分成功/失败 | resolved: +0.15, failed: -0.1 | §9.4 MemoryStore |
| P1-10 | 同步库（sqlite3、ChromaDB）阻塞事件循环 | sqlite3 → aiosqlite；ChromaDB 通过 asyncio.to_thread() | §3.4, §9.4 |

### P2 — 已解决或计划

| # | 问题 | 解决方式 | 状态 |
|---|------|---------|------|
| P2-1 | SQLite + NetworkX 并发安全 | aiosqlite 异步层（已做），锁和事务边界（待实现） | 部分解决 |
| P2-2 | 思考轨迹泄露敏感信息 | NeMo Guardrails output rails 脱敏 | ✓ 已解决 |
| P2-3 | `verify_ssl: false` 默认不安全 | 改为 `verify_ssl: true` | ✓ 已解决 |
| P2-4 | 知识库文档 ID 哈希碰撞 | 待实现时增加 source/category/version 维度 | 计划中 |
| P2-5 | `infer_topology()` 空实现 | 待实现时补充 hostname 匹配逻辑 | 计划中 |
| P2-6 | ThinkingStep/Observation 缺 `to_dict()` | 显式实现 `to_dict()` 方法（基于 `dataclasses.asdict`） | ✓ 已解决 |
| P2-7 | 对话历史无限增长 | `deque(maxlen=MAX_HISTORY*2)` 限制为 50 轮 | ✓ 已解决 |
| P2-8 | 无 LLM 调用速率限制 | 新增 `max_concurrent_diagnoses` 配置项 | ✓ 已解决 |
| P2-9 | `CanaryConfig.success_criteria` 解析未定义 | 与 P0-1 统一使用 `safe_eval_condition()` | ✓ 已解决 |
| P2-10 | kubeconfig 出现两处 | discovery 引用 channels 配置 | ✓ 已解决 |

### 跨组件问题

| # | 问题 | 解决方式 | 状态 |
|---|------|---------|------|
| 跨-1 | fault-injector ↔ load-simulator 联动接口不一致 | 推荐统一为"调用方读取 `report/report.json`"方案 | 见 fault-injector / load-simulator 文档 |
| 跨-2 | 三组件鉴权落地程度不一 | auto-SRE 通过 NeMo Guardrails 补齐安全层 | ✓ 已解决 |
| 跨-3 | 条件表达式求值各自实现 | 统一使用 `safe_eval_condition()` | ✓ 已解决 |

### 已确认的设计亮点（保留）

- "自由诊断、受控修复" 架构边界清晰，ReAct 循环 vs 确定性引擎的分离合理。
- Demo 场景（11.1、11.2）的 10 步诊断 Trace 足够具体，可直接用于 Demo 脚本编写。
- 记忆系统的三层设计（事件/模式/配置）与系统提示词的集成方式实用。
- WAL + 灰度 + 审批门控的三层修复安全设计与 fault-injector 保持一致。
- Skills 框架采用 YAML 格式打包工具 + 提示词 + 示例，扩展性好。
- **新增**：LangGraph 提供标准化的 Agent 状态管理和 checkpoint 持久化。
- **新增**：NeMo Guardrails 提供声明式安全护栏，比手写安全检查更系统化。
- **新增**：NeMo Agent Toolkit 提供开箱即用的 profiling 和 evaluation。

### 长期产品级优化

1. **Ontology 实时同步**：K8s Informer watch 替代定时全量刷新，保持 Pod 级别的实时拓扑。
2. **多 LLM 路由**：按诊断复杂度和成本自动选择 LLM（简单告警用轻量模型，复杂根因用强模型）。LangChain 原生支持 fallback chains。
3. **可观测性**：Agent 自身的 SLI/SLO（诊断成功率、MTTR、LLM 调用耗时/成功率），通过 NeMo Agent Toolkit profiling 持续监控。
4. **联邦记忆**：多 AIDC 间共享匿名化模式记忆（A 站学到的模式可加速 B 站诊断）。
5. **Runbook 自动生成**：从高置信度 LearnedPattern 自动生成 Runbook YAML，形成知识闭环。
6. **LangGraph 高级特性**：Human-in-the-loop 节点、子图复用、并行工具调用、流式输出。
7. **Guardrails 增强**：Colang 2.0 迁移、自定义安全评估模型微调、多语言 rail 支持。
