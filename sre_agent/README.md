# AIDC Auto-SRE Agent

## 1. What This Agent Is

`sre_agent` 是一个面向 AIDC / AI 平台场景的 Auto-SRE 智能体服务。

它把告警、拓扑、知识库、历史记忆、诊断推理和修复执行串成一条完整链路，目标不是只“回答问题”，而是帮助平台团队更快地：

- 发现异常
- 理解影响范围
- 形成诊断结论
- 生成可执行修复计划
- 在审批和安全约束下推进修复
- 记录过程并支持回滚

从代码结构上看，它当前已经具备一套可运行的 Web 服务形态：

- FastAPI API
- WebSocket 实时事件流
- 前端控制台
- LLM 驱动的诊断 Agent
- 修复执行与审批链路

## 2. Core Value

这个 agent 的核心价值不只是“接入一个大模型”，而是把 SRE 场景里的关键上下文组织起来，形成一个可操作的运行时系统。

### 2.1 面向真实运维对象

它不是孤立聊天机器人，而是围绕真实运行对象工作：

- alerts
- Kubernetes workloads
- GPU / 节点 / 网络 / BMC
- ontology topology
- historical incidents
- knowledge documents and runbooks

### 2.2 面向完整处置闭环

它覆盖的不是单点能力，而是告警到修复的闭环：

- 告警采集与展示
- 诊断会话
- thinking trace
- 影响面分析
- 修复计划生成
- 审批、灰度、执行、验证、回滚

### 2.3 面向可审计和可演进

它不是一次性 demo 代码，已经明显朝产品化方向组织：

- JWT / RBAC
- REST + WS API
- 审计日志
- memory / pattern persistence
- knowledge ingestion and retrieval
- topology discovery
- deployment / Docker / Kubernetes / Helm support

## 3. Main Capabilities

根据当前代码和文档，这个 agent 的主要能力可以概括为以下几类。

### 3.0 Fault Injection And Drill Validation

虽然 `fault_injector` 是仓库中的独立模块，但从当前工程关系看，它并不是完全无关的旁支，而是 Auto-SRE 体系里的“故障演练与验证层”。

相关入口和文档包括：

- [fault_injector/cli.py](/home/kevin/project/cube-studio/fault_injector/cli.py)
- [fault_injector/scenarios/registry.py](/home/kevin/project/cube-studio/fault_injector/scenarios/registry.py)
- [fault_injector/docs/architecture.md](/home/kevin/project/cube-studio/fault_injector/docs/architecture.md)

它的核心价值是：

- 主动注入故障而不是只被动等待真实故障
- 对诊断链路、修复链路、监控基线做演练验证
- 为 SRE Agent 提供更贴近真实场景的验证样本和证据

从当前代码看，故障注入能力主要包括：

- `run`、`recover`、`resume`、`validate-config`、`list-scenarios` CLI
- 基于 orchestrator 的标准执行相位：`init`、`baseline`、`inject`、`observe`、`recover`、`verify`、`report`
- 基于安全护栏和 rollback journal 的可恢复执行
- 分层 fault agents：hardware / os / platform / service / monitor
- 多种场景注册与调度

当前已注册的场景可以分成两大类：

- vLLM 延迟类
- RDMA 异常类

按注册表，已经覆盖例如：

- `gpu_contention`
- `network_jitter`
- `storage_io_interference`
- `platform_cascade`
- `os_resource_pressure`
- `thermal_throttling`
- `pfc_deadlock`
- `ecn_misconfiguration`
- `rdma_load_imbalance`
- `rdma_link_flap`
- `roce_mtu_mismatch`
- `rdma_qos_downgrade`

因此如果从完整价值链来看，这个项目不只是“诊断与修复 agent”，也是一个包含“故障注入、演练、恢复验证”的 Auto-SRE 组合体系。

### 3.1 Alert-Driven Diagnosis

核心入口在后端应用与路由层：

- [server.py](/home/kevin/project/cube-studio/sre_agent/server.py)
- [api/routes.py](/home/kevin/project/cube-studio/sre_agent/api/routes.py)

这部分负责：

- 告警快照与会话管理
- 诊断会话启动
- thinking trace 推送
- Web API 和 WebSocket 暴露

### 3.2 LLM-Based Diagnostic Agent

核心入口在：

- [agent/conversational.py](/home/kevin/project/cube-studio/sre_agent/agent/conversational.py)
- [agent/graph.py](/home/kevin/project/cube-studio/sre_agent/agent/graph.py)
- [agent/nodes.py](/home/kevin/project/cube-studio/sre_agent/agent/nodes.py)

从现有实现和进度文档看，这部分已经形成了一个基于 LangGraph 的诊断推理闭环，用于：

- 收集上下文
- 调用工具
- 组织推理过程
- 输出诊断结论

### 3.3 Tool-Driven Runtime Inspection

工具注册与运行时通道是这个项目非常关键的一层：

- [runtime/channels.py](/home/kevin/project/cube-studio/sre_agent/runtime/channels.py)
- [docs/tool-catalog.md](/home/kevin/project/cube-studio/sre_agent/docs/tool-catalog.md)

当前工具能力覆盖：

- Prometheus 查询
- Kubernetes 只读与写操作
- SSH / GPU / 网络诊断
- Ontology 查询
- Memory 检索
- Remediation 执行

这使得 agent 的回答具备“证据驱动”的特征，而不是纯语言生成。

### 3.4 Topology And Blast Radius

相关模块包括：

- [ontology/graph.py](/home/kevin/project/cube-studio/sre_agent/ontology/graph.py)
- [topology](/home/kevin/project/cube-studio/sre_agent/topology)
- [cli.py](/home/kevin/project/cube-studio/sre_agent/cli.py)

这部分提供：

- 静态与动态拓扑发现
- 实体关系组织
- blast radius 查询
- 拓扑状态同步

对平台类故障来说，这一层决定了 agent 能不能把“一个告警”解释成“一个影响面”。

### 3.5 Knowledge And Memory

相关模块包括：

- [knowledge](/home/kevin/project/cube-studio/sre_agent/knowledge)
- [memory](/home/kevin/project/cube-studio/sre_agent/memory)

这部分负责：

- 知识文档入库和搜索
- 运行手册辅助
- 历史 incident 检索
- pattern 学习和复用

它的价值在于让诊断不只是看当前瞬时状态，也能参考历史经验和沉淀知识。

### 3.6 Remediation Workflow

相关模块包括：

- [remediation](/home/kevin/project/cube-studio/sre_agent/remediation)
- [safety](/home/kevin/project/cube-studio/sre_agent/safety)

从代码组织上看，这部分已经包含：

- plan generation
- approval gate
- canary execution
- WAL / journal
- rollback
- validation

这意味着它不是止步于“给建议”，而是具备进入执行链路的工程骨架。

### 3.7 Web Console

前端目录：

- [frontend](/home/kevin/project/cube-studio/sre_agent/frontend)

前端当前承载的主要界面价值：

- 告警视图
- 诊断过程展示
- thinking trace
- remediation 进度展示
- knowledge / skills / topology 等辅助页面

## 4. Key Entry Points

### 4.1 CLI Entry

- [__main__.py](/home/kevin/project/cube-studio/sre_agent/__main__.py)
- [cli.py](/home/kevin/project/cube-studio/sre_agent/cli.py)

常见 CLI 能力包括：

- `serve`
- `discover`
- `topology`
- `memory incidents`
- `memory patterns`
- `knowledge ingest`
- `knowledge search`

### 4.2 Local Web Startup

- [scripts/start_frontend_backend.py](/home/kevin/project/cube-studio/sre_agent/scripts/start_frontend_backend.py)

这是本地一键启动入口，会同时启动：

- backend
- frontend

### 4.3 Container Startup

- [docker/run_web_stack.py](/home/kevin/project/cube-studio/sre_agent/docker/run_web_stack.py)
- [docker/container_entrypoint.sh](/home/kevin/project/cube-studio/sre_agent/docker/container_entrypoint.sh)

这部分负责容器化环境下的启动，不改变本地手动运行路径。

## 5. Repository Areas Worth Knowing

| Path | Purpose |
| --- | --- |
| [agent](/home/kevin/project/cube-studio/sre_agent/agent) | LLM diagnosis graph and conversation logic |
| [api](/home/kevin/project/cube-studio/sre_agent/api) | REST and WebSocket routes plus middleware |
| [auth](/home/kevin/project/cube-studio/sre_agent/auth) | JWT and RBAC |
| [knowledge](/home/kevin/project/cube-studio/sre_agent/knowledge) | Knowledge ingestion, retrieval, storage |
| [memory](/home/kevin/project/cube-studio/sre_agent/memory) | Incident history and learned patterns |
| [ontology](/home/kevin/project/cube-studio/sre_agent/ontology) | Entity graph and topology model |
| [remediation](/home/kevin/project/cube-studio/sre_agent/remediation) | Approval, execution, canary, rollback, validation |
| [runtime](/home/kevin/project/cube-studio/sre_agent/runtime) | Tool channel bootstrap and health model |
| [frontend](/home/kevin/project/cube-studio/sre_agent/frontend) | Web UI |
| [scripts](/home/kevin/project/cube-studio/sre_agent/scripts) | Startup helpers, demos, probes, sync utilities |
| [deploy](/home/kevin/project/cube-studio/sre_agent/deploy) | SOP, FAQ, release checklist, Kubernetes, Helm |
| [docker](/home/kevin/project/cube-studio/sre_agent/docker) | Dockerfiles, image build, container run scripts |
| [docs](/home/kevin/project/cube-studio/sre_agent/docs) | Progress, tool catalog, contracts, acceptance notes |

## 6. Deployment Documentation Index

如果目标是部署或交付，这几份文档建议按顺序看。

### 6.1 First Read

- [deploy/SOP.md](/home/kevin/project/cube-studio/sre_agent/deploy/SOP.md)

用途：

- 解释主机手动运行、Docker 运行、Kubernetes 运行、Helm 运行的标准步骤

### 6.2 Troubleshooting

- [deploy/FAQ.md](/home/kevin/project/cube-studio/sre_agent/deploy/FAQ.md)

用途：

- 快速排查本地、容器、Kubernetes、Helm 常见问题

### 6.3 Release Execution

- [deploy/RELEASE_CHECKLIST.md](/home/kevin/project/cube-studio/sre_agent/deploy/RELEASE_CHECKLIST.md)

用途：

- 规范化每次构建、推送、部署、验收、回滚准备的检查动作

### 6.4 Automated Build And Release

- [docs/gitlab/PIPELINE_RUNBOOK.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md)
- [docs/gitlab/README.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/README.md)

用途：

- 解释 `sre_agent` 分支专用 GitLab 流水线的工作流、阶段与 job 职责
- 说明基础镜像、业务镜像、preview、验证、snapshot 发布、手动 release promotion 的规则
- 提供 GitLab 变量、schedule、Runner 要求和镜像 tag 规范

### 6.5 Raw Kubernetes Manifests

- [deploy/k8s](/home/kevin/project/cube-studio/sre_agent/deploy/k8s)

用途：

- 使用 `Deployment + Service + ConfigMap + Secret` 直接部署

### 6.6 Helm Chart

- [deploy/helm/sre-agent-web](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web)

用途：

- 在多环境下以参数化方式安装或升级服务

## 7. Recommended Reading Order For New Team Members

推荐新人按这个顺序熟悉项目：

1. 先读本页 README，建立整体认知
2. 再看 [deploy/SOP.md](/home/kevin/project/cube-studio/sre_agent/deploy/SOP.md)，理解运行和交付方式
3. 再看 [docs/current_progress.md](/home/kevin/project/cube-studio/sre_agent/docs/current_progress.md)，了解当前实现边界
4. 再看 [docs/tool-catalog.md](/home/kevin/project/cube-studio/sre_agent/docs/tool-catalog.md)，理解 agent 实际可调用的工具
5. 最后按职责深入相应模块，例如 `agent/`、`remediation/`、`ontology/`、`frontend/`

## 8. Current Delivery Posture

从当前代码与部署产物看，这个 agent 已经不是单纯原型，而是一套“可本地运行、可容器化、可进入云原生环境”的服务。

当前支持的运行方式：

- host manual run
- Docker image and container run
- raw Kubernetes manifests
- Helm chart deployment

当前最适合的云原生基础形态仍然是：

- `Deployment + Service`

当环境数量和参数组合变多时，再优先使用 Helm 做统一管理。
