# sre_agent/models 设计与实现说明

## 1. 目标

`sre_agent/models` 是 `sre_agent` 的共享数据契约层，负责定义系统内核心对象的结构、校验规则、序列化格式和跨模块边界。

该目录应只放：

- Pydantic `BaseModel`
- `Enum` / `Literal` 等类型约束
- 少量纯数据辅助方法，例如 `to_dict()`、`model_dump()` 相关封装

该目录不应放：

- Agent 编排逻辑
- Tool 调用逻辑
- Channel 实现
- 数据库存取逻辑
- Remediation 执行逻辑
- 前端展示层逻辑

## 2. 作用

`models` 的核心作用是统一以下模块之间的数据接口：

- `sre_agent/agent/`
- `sre_agent/api/`
- `sre_agent/remediation/`
- `sre_agent/memory/`
- `sre_agent/ontology/`
- WebSocket 推送
- 前端类型映射

这样做有几个直接收益：

- 避免各模块自行拼接 `dict`
- API、WebSocket、Agent 状态使用同一份 schema
- 便于多人并行开发时先冻结字段定义
- 便于做 contract test、序列化测试和回归测试

根据协作计划，`sre_agent/models/*` 是需要优先冻结的共享契约之一，也是其他后端模块启动开发的前置条件。

## 3. 建议文件划分

### 3.1 `common.py`

放通用基础模型和错误契约，例如：

- `ErrorCode`
- `SREError`
- `SREResponse[T]`
- 通用状态枚举
- 通用 ID / 时间戳字段封装

作用：

- 统一 REST API 返回格式
- 统一错误码和错误结构
- 防止不同模块返回不一致的错误字段

设计依据：

- 设计文档要求所有 API 返回经过 Pydantic 模型
- 禁止直接返回 `dict`
- 使用 `SREResponse[T]` 统一包装成功和失败结果

### 3.2 `alert.py`

放告警相关模型，例如：

- `Alert`
- `AlertSeverity`
- `AlertStatus`

作用：

- 统一告警入口格式
- 作为 `AlertChannel`、`DiagnosisSession`、事件记忆的共同输入
- 保证 Alertmanager / API / 内部流程使用同一结构

设计依据：

- 设计文档明确要求 `Alert` 统一为 Pydantic `BaseModel`
- 协作计划要求优先冻结 model 字段定义

### 3.3 `diagnosis.py`

放诊断阶段核心模型，例如：

- `Hypothesis`
- `PropagationStep`
- `RankedRootCause`
- `DiagnosisResult`
- `DiagnosisSession`

作用：

- 表达单根因和多候选根因诊断结果
- 作为 `SREAgent` 输出和 `LoopOrchestrator` 输入
- 统一会话生命周期状态

设计重点：

- 支持 `diagnosis_certainty`: `confirmed | probable | ambiguous`
- 支持 `ranked_candidates`
- 支持把诊断与后续循环修复验证打通

### 3.4 `remediation.py`

放修复计划和验证相关模型，例如：

- `VerificationCondition`
- `VerificationConfig`
- `CanaryCondition`
- `CanaryConfig`
- `RemediationStep`
- `RemediationPlan`
- `RemediationResult`

作用：

- 把 LLM 输出的修复建议约束为结构化 schema
- 给 `PlanValidator`、审批流、WAL、Canary、执行器使用
- 避免字符串解析式条件判断

设计重点：

- `VerificationCondition` 和 `CanaryCondition` 使用结构化字段，而不是字符串表达式
- `RemediationPlan` 是修复执行的标准输入契约
- `RemediationResult` 是修复执行的标准输出契约

### 3.5 `ontology.py`

放数字孪生拓扑相关共享模型，例如：

- `Rack`
- `Node`
- `GPUInfo`
- `NICInfo`
- `Switch`
- `SwitchPort`
- `Relationship`
- 其他实体和关系模型

作用：

- 给 `DiscoveryAgent`、Ontology 存储、Blast Radius 计算、告警关联聚合共用
- 统一实体 ID、实体类型和关系定义
- 避免发现、查询、展示三套模型不一致

注意：

- `sre_agent/ontology/` 目录负责图存储、查询和扫描逻辑
- `sre_agent/models/ontology.py` 只负责共享实体 schema

### 3.6 `memory.py`

放记忆系统共享模型，例如：

- `IncidentRecord`
- `LearnedPattern`
- `ConfigMemory` 或类似配置记忆模型

作用：

- 统一事件记忆、模式记忆、配置记忆的存储和返回结构
- 给 `memory/store.py`、`agent/`、API 和前端共用

设计重点：

- `IncidentRecord` 记录完整事件闭环
- `LearnedPattern` 记录从历史事件中抽取的模式
- 模式记忆可带时间衰减能力，例如 `effective_confidence()`

### 3.7 `events.py`

放实时事件和 WebSocket 契约，例如：

- `EventType`
- `WSEvent`
- 如有需要可进一步拆分具体 payload model

作用：

- 统一思考流、工具调用、修复进度、错误、完成事件的推送格式
- 给 WebSocket 服务端和前端消费端共用

设计重点：

- 固定 `schema_version`
- 固定事件类型枚举
- 所有实时推送事件使用统一封装

## 4. 建议边界

`sre_agent/models` 应保持“薄模型、强约束”的风格。

适合放入该目录的内容：

- 字段定义
- 枚举定义
- 基础校验
- 默认值
- 轻量级格式化方法

不适合放入该目录的内容：

- `diagnose()`、`execute()`、`search()` 等业务方法
- HTTP/SSH/K8s/Prometheus 调用
- SQLite / PostgreSQL / ChromaDB / Qdrant 读写
- LangGraph 状态流转逻辑
- Prompt 拼接
- 审批、回滚、灰度执行细节

## 5. 结论

一句话总结：

`sre_agent/models` 负责定义“系统里所有核心对象长什么样、如何校验、如何序列化”，是 `agent`、`api`、`remediation`、`memory`、`ontology`、WebSocket 和前端共享的统一 schema 层。

建议最终目录形态如下：

```text
sre_agent/models/
├── common.py
├── alert.py
├── diagnosis.py
├── remediation.py
├── ontology.py
├── memory.py
└── events.py
```

## 6. 参考文档

- `AIDC-auto-SRE.md`
- `aidc-auto-sre-plan.md`
- `team-colabration-developement-plan.md`

说明：

- 用户提到的 `aidc-auto-src-plan.md`，仓库中的实际文件名为 `aidc-auto-sre-plan.md`
- 用户提到的 `team-colabration-development-plan.md`，仓库中的实际文件名为 `team-colabration-developement-plan.md`
