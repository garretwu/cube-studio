# Frontend 接口规范说明

## 1. 文档目的

本文档用于说明 `frontend` 项目当前已经引用的接口契约，覆盖以下内容：

- 前端实际请求的 HTTP API
- Diagnosis 模块预留的 WebSocket 契约
- 本地开发阶段使用的 mock 数据结构与返回逻辑
- 按功能模块整理的页面、状态层与后端接口映射关系

本文档面向前端、后端、联调和测试同学，作为当前接口面的统一说明。

## 2. 范围与状态

### 2.1 项目形态

`frontend` 目录本身是一个独立的 Vite + React + TypeScript 前端应用，但它不是“纯静态前端”。它的页面逻辑是按照后端 API 契约来编写的，只是在本地开发时默认启用了浏览器侧 MSW mock。

### 2.2 运行模式

| 模式 | 条件 | 数据来源 |
| --- | --- | --- |
| Mock 开发模式 | `import.meta.env.DEV === true` 且 `VITE_USE_MSW !== "false"` | `src/mocks/handlers.ts` |
| 后端联调模式 | `VITE_USE_MSW === "false"` 或非开发环境部署 | 真实后端 API |

### 2.3 通用请求约定

| 项目 | 说明 |
| --- | --- |
| HTTP 客户端 | `axios` |
| Base URL | `import.meta.env.VITE_API_BASE_URL ?? ""` |
| 超时时间 | `10000 ms` |
| 公共请求头 | `x-trace-id: sre-ui-${Date.now()}` |
| 错误处理 | Axios 错误统一归一化为 `Error(message)` |

### 2.4 WebSocket 开关

Diagnosis 页面包含 WebSocket 客户端，但只有在以下条件成立时才会启用：

```text
VITE_WS_ENABLED === "true" && Boolean(session?.session_id)
```

当前前端中没有 WebSocket 的 mock 实现。

## 3. 接口总览

### 3.1 前端已引用的 HTTP 接口

| 功能模块 | 方法 | 路径 | 前端调用方 | 是否有 Mock |
| --- | --- | --- | --- | --- |
| Topology | `GET` | `/api/ontology` | `topologyStore` / Topology 页面 | 是 |
| Alerts | `GET` | `/api/alerts` | `alertStore` / Alerts 页面 | 是 |
| Diagnosis | `GET` | `/api/diagnosis/session/current` | `diagnosisStore` / Diagnosis 页面 | 是 |
| Remediation | `GET` | `/api/remediation/overview` | `remediationStore` / Remediation 页面 | 是 |
| Remediation | `POST` | `/api/remediation/:sessionId/approve` | `remediationStore.submitApproval` | 是 |
| Diagnosis | `POST` | `/api/chat` | `diagnosisStore.sendMessage` / Diagnosis 页面内对话 | 是 |
| Knowledge | `GET` | `/api/knowledge/search` | Knowledge 页面搜索动作 | 是 |
| Knowledge | `GET` | `/api/knowledge/documents` | Knowledge 页面初始化加载 | 是 |
| Memory | `GET` | `/api/memory/incidents` | Memory 页面 | 是 |
| Memory | `GET` | `/api/memory/patterns` | Memory 页面 | 是 |
| Memory | `GET` | `/api/memory/baseline` | Memory 页面 | 是 |
| Skills | `GET` | `/api/skills` | Skills 页面 | 是 |
| Chat History | `GET` | `/api/chat/history` | 当前 UI 未使用 | 是 |

### 3.2 前端已引用的 WebSocket 接口

| 功能模块 | 协议 | 路径模式 | 前端调用方 | 默认是否启用 |
| --- | --- | --- | --- | --- |
| Diagnosis Thinking Trace | `ws` / `wss` | `/ws/thinking-trace/:sessionId` | Diagnosis 页面通过 `useWebSocket` | 否 |

## 4. 模块级接口说明

### 4.1 Topology 模块

#### 4.1.1 功能范围

Topology 页面使用该模块完成以下展示：

- 拓扑图节点
- 拓扑图边关系
- 活跃告警数
- 最近事件摘要
- 分组树视图

#### 4.1.2 前端调用链

| 层级 | 文件 |
| --- | --- |
| 页面 | `src/pages/Topology.tsx` |
| 状态 | `src/store/topologyStore.ts` |
| 图渲染组件 | `src/components/TopologyGraph.tsx` |

#### 4.1.3 接口

```http
GET /api/ontology
```

#### 4.1.4 响应契约

```ts
{
  nodes: OntologyNode[];
  edges: OntologyEdge[];
  active_alerts: number;
  recent_events: string[];
}
```

`OntologyNode`

```ts
type OntologyNode = {
  id: string;
  entity_type: string;
  name?: string | null;
  properties: Record<string, unknown>;
  status?: string | null;
  updated_at: string;
};
```

`OntologyEdge`

```ts
type OntologyEdge = {
  source_id: string;
  target_id: string;
  relation: string;
  properties: Record<string, unknown>;
};
```

#### 4.1.5 Mock 数据来源

- `src/mocks/data.ts`
  - `topologyNodes`
  - `topologyEdges`
- `src/mocks/handlers.ts`

#### 4.1.6 Mock 逻辑

| 项目 | 逻辑 |
| --- | --- |
| 延迟 | `120 ms` |
| `nodes` | 直接返回 `topologyNodes` |
| `edges` | 直接返回 `topologyEdges` |
| `active_alerts` | 通过 `alerts.filter(alert => alert.status === "firing").length` 动态计算 |
| `recent_events` | 返回固定的两个事件字符串 |

#### 4.1.7 当前 Mock 拓扑结构

当前 mock 中建模的是一个简化版 AIDC 拓扑：

- `rack-1`
- `node-gpu-01`
- `gpu-01`
- `sw-01`
- `svc-vllm`

关系示例：

- node `part_of` rack
- gpu `part_of` node
- service `hosted_on` node
- node `connected_to` switch

### 4.2 Alerts 模块

#### 4.2.1 功能范围

Alerts 页面使用该模块完成以下展示：

- 告警流表格
- 告警聚类分组
- 基于严重级别的筛选

#### 4.2.2 前端调用链

| 层级 | 文件 |
| --- | --- |
| 页面 | `src/pages/Alerts.tsx` |
| 状态 | `src/store/alertStore.ts` |

#### 4.2.3 接口

```http
GET /api/alerts
```

#### 4.2.4 响应契约

```ts
{
  alerts: Alert[];
  clusters: AlertCluster[];
}
```

`Alert`

```ts
type Alert = {
  alert_name: string;
  severity: "critical" | "warning" | "info";
  labels: Record<string, string>;
  annotations: Record<string, string>;
  starts_at: string;
  ends_at?: string | null;
  fingerprint: string;
  status: "firing" | "resolved" | "silenced";
  source?: string | null;
};
```

`AlertCluster`

```ts
type AlertCluster = {
  cluster_id: string;
  summary: string;
  severity: "critical" | "warning" | "info";
  alerts: string[];
};
```

#### 4.2.5 Mock 数据来源

- `alerts`
- `alertClusters`

#### 4.2.6 Mock 逻辑

| 项目 | 逻辑 |
| --- | --- |
| 延迟 | `100 ms` |
| `alerts` | 直接返回静态数组 |
| `clusters` | 直接返回静态数组 |
| 前端筛选 | 严重级别筛选在前端完成，请求本身不带查询参数 |

### 4.3 Diagnosis 模块

#### 4.3.1 功能范围

Diagnosis 页面使用该模块完成以下展示：

- 当前诊断会话状态
- 假设树
- 结论卡片
- 推理时间线
- 可选的实时流式推理轨迹

#### 4.3.2 前端调用链

| 层级 | 文件 |
| --- | --- |
| 页面 | `src/pages/Diagnosis.tsx` |
| 状态 | `src/store/diagnosisStore.ts` |
| WS Hook | `src/hooks/useWebSocket.ts` |
| WS 客户端 | `src/api/ws.ts` |

#### 4.3.3 HTTP 接口

```http
GET /api/diagnosis/session/current
```

#### 4.3.4 HTTP 响应契约

```ts
type DiagnosisSession = {
  session_id: string;
  alert: Alert;
  status: string;
  diagnosis_result?: DiagnosisResult | null;
  trace?: { steps: Array<ThinkingStep | Observation> } | null;
  duration_seconds: number;
  outcome?: string | null;
};
```

`DiagnosisResult`

```ts
type DiagnosisResult = {
  root_cause: string;
  root_cause_layer: string;
  root_cause_entities: string[];
  confidence: number;
  hypotheses: Hypothesis[];
  impact_summary: string;
  affected_services: string[];
  triage_priority: "P0" | "P1" | "P2" | "P3";
  diagnosis_certainty: "confirmed" | "probable" | "ambiguous";
};
```

#### 4.3.5 WebSocket 接口

```text
ws(s)://<origin>/ws/thinking-trace/:sessionId
```

#### 4.3.6 WebSocket 事件契约

```ts
type WSEvent = {
  schema_version: string;
  type:
    | "thinking_step"
    | "tool_call"
    | "tool_result"
    | "diagnosis_result"
    | "approval_required"
    | "loop_start"
    | "loop_progress"
    | "remediation_progress"
    | "alert"
    | "error"
    | "done";
  session_id: string;
  timestamp: string;
  data: Record<string, unknown>;
};
```

当前前端状态层只会在 `event.type === "thinking_step"` 时，将 `event.data` 追加到 `trace.steps` 中；其他事件类型目前会被忽略。

#### 4.3.7 Mock 数据来源

- `diagnosisSession`

#### 4.3.8 Mock 逻辑

| 项目 | 逻辑 |
| --- | --- |
| 延迟 | `140 ms` |
| HTTP 返回 | 直接返回静态 `diagnosisSession` |
| WebSocket | 当前没有 mock 实现 |

### 4.4 Remediation 模块

#### 4.4.1 功能范围

Remediation 页面使用该模块完成以下展示：

- 修复计划总览
- 审批弹窗
- Canary 进度
- 修复步骤列表

#### 4.4.2 前端调用链

| 层级 | 文件 |
| --- | --- |
| 页面 | `src/pages/Remediation.tsx` |
| 状态 | `src/store/remediationStore.ts` |

#### 4.4.3 查询接口

```http
GET /api/remediation/overview
```

#### 4.4.4 审批接口

```http
POST /api/remediation/:sessionId/approve
Content-Type: application/json

{
  "approved": boolean
}
```

#### 4.4.5 响应契约

查询接口：

```ts
type RemediationOverview = {
  session_id: string;
  plan: RemediationPlan;
  progress: {
    status: string;
    completed_steps: number;
    total_steps: number;
    batch_status: Array<{ batch: string; progress: number; status: string }>;
  };
  approval_required: boolean;
};
```

审批接口：

```ts
{
  success: boolean;
  status: string;
}
```

#### 4.4.6 Mock 数据来源

- `remediationOverview`

#### 4.4.7 Mock 逻辑

| 接口 | 延迟 | 逻辑 |
| --- | --- | --- |
| `GET /api/remediation/overview` | `120 ms` | 返回静态 `remediationOverview` |
| `POST /api/remediation/:sessionId/approve` | `90 ms` | 读取请求体中的 `approved`，返回 `approved` 或 `rejected` |

#### 4.4.8 前端审批后行为

前端在审批后不会重新请求 overview，而是直接在本地状态里做变更：

- 设置 `approvalDialogOpen = false`
- 设置 `approval_required = false`
- 设置 `progress.status = "approved"` 或 `"rejected"`

### 4.5 历史说明：对话能力已并入 Diagnosis 模块

#### 4.5.1 功能范围

该能力已并入 Diagnosis 页面，当前用于承载诊断内协同对话：

- 初始助手消息
- 用户输入
- 助手回复

#### 4.5.2 前端调用链

| 层级 | 文件 |
| --- | --- |
| 页面 | `src/pages/Diagnosis.tsx` |
| 状态 | `src/store/diagnosisStore.ts` |

#### 4.5.3 发送消息接口

```http
POST /api/chat
Content-Type: application/json

{
  "content": string
}
```

#### 4.5.4 响应契约

```ts
{
  reply: ChatMessage
}
```

`ChatMessage`

```ts
type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  created_at: string;
  tool_name?: string;
  metadata?: Record<string, unknown>;
};
```

#### 4.5.5 仅在 Mock 中存在的历史接口

```http
GET /api/chat/history
```

这个接口存在于 `src/mocks/handlers.ts` 中，但当前 Diagnosis 页面没有实际调用。

#### 4.5.6 Mock 数据来源

- `initialChatMessages`
- handler 内动态生成的 reply

#### 4.5.7 Mock 逻辑

| 接口 | 延迟 | 逻辑 |
| --- | --- | --- |
| `POST /api/chat` | `90 ms` | 读取 `content`，动态生成 assistant reply，包含动态 `id` 和 `created_at` |
| `GET /api/chat/history` | `50 ms` | 返回 `initialChatMessages` |

#### 4.5.8 重要实现说明

当前 Diagnosis 页面会直接从 `src/mocks/data.ts` 中读取 `initialChatMessages`，而不是通过 `/api/chat/history` 拉取历史消息。这意味着：

- 即使不请求历史接口，页面也依赖本地 mock 种子消息
- 如果进入真实后端联调模式，后续可能需要补一套真正的 history 拉取流程

### 4.6 Knowledge 模块

#### 4.6.1 功能范围

Knowledge 页面使用该模块完成以下展示：

- 文档列表初始化加载
- 搜索结果展示

#### 4.6.2 前端调用链

| 层级 | 文件 |
| --- | --- |
| 页面 | `src/pages/Knowledge.tsx` |

#### 4.6.3 文档列表接口

```http
GET /api/knowledge/documents
```

#### 4.6.4 搜索接口

```http
GET /api/knowledge/search?query=<string>&category=<string?>
```

#### 4.6.5 响应契约

列表接口：

```ts
{
  documents: KnowledgeDocument[];
}
```

搜索接口：

```ts
{
  results: KnowledgeDocument[];
}
```

`KnowledgeDocument`

```ts
type KnowledgeDocument = {
  id: string;
  title: string;
  source: string;
  category: string;
  excerpt: string;
  tags: string[];
  score?: number;
};
```

#### 4.6.6 Mock 数据来源

- `knowledgeDocuments`

#### 4.6.7 Mock 逻辑

| 接口 | 延迟 | 逻辑 |
| --- | --- | --- |
| `GET /api/knowledge/documents` | `80 ms` | 返回全部文档 |
| `GET /api/knowledge/search` | `120 ms` | 按 `category` 和 `title + excerpt + tags` 做文本匹配过滤 |

附加搜索行为：

- query 为空时，返回按 category 过滤后的结果
- query 匹配不区分大小写
- 额外兼容逻辑：如果 query 中包含 `"rocev2"`，handler 会尽量返回相关结果

### 4.7 Memory 模块

#### 4.7.1 功能范围

Memory 页面使用该模块完成以下展示：

- 事件历史
- 学习到的模式
- 基线配置

#### 4.7.2 前端调用链

| 层级 | 文件 |
| --- | --- |
| 页面 | `src/pages/Memory.tsx` |

#### 4.7.3 接口

```http
GET /api/memory/incidents?last=<number?>
GET /api/memory/patterns
GET /api/memory/baseline
```

#### 4.7.4 响应契约

Incidents：

```ts
type IncidentRecord = {
  incident_id: string;
  aidc_id: string;
  timestamp: string;
  alert: Alert;
  symptoms: string[];
  root_cause: string;
  root_cause_layer: string;
  root_cause_entities: string[];
  outcome: "resolved" | "partially_resolved" | "failed" | "escalated";
  resolution_time_seconds: number;
};
```

Patterns：

```ts
type LearnedPattern = {
  pattern_id: string;
  aidc_id: string;
  symptom_signature: string[];
  root_cause: string;
  effective_fix: string;
  occurrence_count: number;
  confidence: number;
  first_seen: string;
  last_seen: string;
  example_incidents: string[];
};
```

Baseline：

```ts
type ConfigBaseline = {
  aidc_id: string;
  version: number;
  metric_baselines: Record<string, string | number>;
  safety_thresholds: Record<string, string | number>;
  custom_rules: Record<string, unknown>;
  updated_at: string;
};
```

#### 4.7.5 Mock 数据来源

- `incidents`
- `learnedPatterns`
- `baseline`

#### 4.7.6 Mock 逻辑

| 接口 | 延迟 | 逻辑 |
| --- | --- | --- |
| `GET /api/memory/incidents` | `60 ms` | 返回完整 `incidents` 数组，当前 mock 不真正处理 `last` 参数 |
| `GET /api/memory/patterns` | `60 ms` | 返回完整 `learnedPatterns` |
| `GET /api/memory/baseline` | `50 ms` | 返回 `baseline` 对象 |

### 4.8 Skills 模块

#### 4.8.1 功能范围

Skills 页面使用该模块展示技能注册表卡片列表。

#### 4.8.2 前端调用链

| 层级 | 文件 |
| --- | --- |
| 页面 | `src/pages/Skills.tsx` |

#### 4.8.3 接口

```http
GET /api/skills
```

#### 4.8.4 响应契约

```ts
type SkillDescriptor = {
  id: string;
  name: string;
  scope: "builtin" | "custom";
  summary: string;
  source: string;
  permissions: string[];
  match_score: number;
};
```

#### 4.8.5 Mock 数据来源

- `skills`

#### 4.8.6 Mock 逻辑

| 项目 | 逻辑 |
| --- | --- |
| 延迟 | `70 ms` |
| 返回值 | 直接返回静态数组 |

## 5. Mock 数据汇总

| 模块 | Mock 种子变量 |
| --- | --- |
| Topology | `topologyNodes`, `topologyEdges` |
| Alerts | `alerts`, `alertClusters` |
| Diagnosis | `diagnosisSession` |
| Remediation | `remediationOverview` |
| Diagnosis 内对话 | `initialChatMessages` |
| Knowledge | `knowledgeDocuments` |
| Memory | `incidents`, `learnedPatterns`, `baseline` |
| Skills | `skills` |

主要 mock 文件：

- `src/mocks/data.ts`
- `src/mocks/handlers.ts`
- `src/mocks/browser.ts`

## 6. 当前已知缺口与联调注意事项

### 6.1 前端契约已存在，不代表后端路由已全部落地

前端代码已经明确引用了这些接口，但本文档描述的是“前端当前使用的契约”和“mock 实现”。它并不保证仓库中对应的后端 HTTP 路由已经全部实现完成。

### 6.2 WebSocket 契约是预留能力，当前并未完全落地

Diagnosis 页面已经有通用 WebSocket 客户端和事件模型，但：

- 是否启用由 `VITE_WS_ENABLED` 控制
- 当前没有浏览器侧 WebSocket mock
- diagnosis store 目前只处理 `thinking_step` 类型事件

### 6.3 某些 Mock 行为比未来真实后端更宽松

例如：

- `GET /api/memory/incidents` 当前会忽略 `last`
- chat history 接口虽然存在，但 UI 没有真正使用
- Diagnosis 页面内对话会直接注入本地初始消息，而不是完全通过 HTTP 获取

### 6.4 推荐的后端对齐优先级

1. 优先实现第 3 节列出的 HTTP 路由。
2. 返回字段名严格与 `src/api/types.ts` 中的 TypeScript 契约保持一致。
3. 明确 Diagnosis 内对话后续是否仍需引入 chat history API。
4. 明确 diagnosis streaming 是否会作为近期真实能力落地。

## 7. 文档维护时的参考源

更新本文档时，建议优先核对以下文件：

- `src/api/client.ts`
- `src/api/types.ts`
- `src/mocks/handlers.ts`
- `src/mocks/data.ts`
- `src/pages/*.tsx`
- `src/store/*.ts`
