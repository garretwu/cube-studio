# LangGraph 流式输出方案文档

## Context

当前 sre_agent 使用 `graph.ainvoke()` 运行诊断图，等待所有节点完成后才返回结果。虽然已有 `trace_callback` 机制通过 WebSocket 推送节点级别事件（thinking_step、tool_call、tool_result 等），但 LLM 的推理文本是**按节点批量推送**而非逐 token 流式输出。

**目标**：实现 Claude 风格的流式体验——LLM 思考文本逐字出现、工具调用实时展示、诊断结论流式生成。

---

## 1. LangGraph `astream_events` API 分析

LangGraph 提供三种流式模式：

| API | 粒度 | 适用场景 |
|-----|------|----------|
| `graph.ainvoke()` | 整图执行完返回 | 当前方式，无流式 |
| `graph.astream()` | 每个节点完成后 yield state | 节点级流式 |
| `graph.astream_events(input, version="v2")` | **token 级**事件流 | **目标方式** |

`astream_events` 关键事件：

```python
# LLM token 流式输出
{"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="诊断")}, "name": "ChatOpenAI", "tags": [...]}
{"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="该节点")}, ...}

# 节点生命周期
{"event": "on_chain_start", "name": "reason", "data": {"input": {...}}}
{"event": "on_chain_end", "name": "reason", "data": {"output": {...}}}

# 工具执行
{"event": "on_tool_start", "name": "k8s.list_pods", "data": {"input": {...}}}
{"event": "on_tool_end", "name": "k8s.list_pods", "data": {"output": {...}}}
```

---

## 2. 整体架构

```
Frontend (React)            Backend (FastAPI)                    LangGraph
─────────────────           ──────────────────                   ──────────
DiagnosisPage               /api/diagnose/stream                 graph.astream_events()
    │                           │                                      │
    ├── POST /diagnose/stream ──► 新 SSE 端点 ──────────────────────►│
    │                           │                                      │
    │   SSE: token_delta ◄─────┼◄── on_chat_model_stream ───────────┤
    │   SSE: node_started ◄────┼◄── on_chain_start ─────────────────┤
    │   SSE: node_completed ◄──┼◄── on_chain_end ───────────────────┤
    │   SSE: tool_started ◄────┼◄── on_tool_start ──────────────────┤
    │   SSE: tool_completed ◄──┼◄── on_tool_end ────────────────────┤
    │   SSE: state_snapshot ◄──┼◄── 节点完成后的 state diff ─────────┤
    │   SSE: done ◄────────────┼◄── 流结束 ──────────────────────────┤
    │                           │                                      │
    │   (同时 WebSocket 也推送)  │                                      │
    │   WS: thinking_step ◄────┼◄── trace_callback (保持兼容) ───────┤
    │   WS: diagnosis_result◄──┼◄── trace_callback ──────────────────┤
```

**核心决策**：新增 SSE (Server-Sent Events) 端点 `/api/diagnose/stream` 用于流式诊断，保留现有 WebSocket `/ws/thinking-trace/{session_id}` 用于向后兼容。

**理由**：
- SSE 天然适合单向流式推送，前端 `EventSource` / `fetch` 原生支持
- WebSocket 已有复杂数据流（alerts、topology、chat），混用增加复杂度
- SSE 天然支持 `Last-Event-ID` 断线重连
- Claude API 本身也使用 SSE

---

## 3. 新增事件类型

### 3.1 后端 EventType 扩展

```python
# sre_agent/models/events.py 新增
class EventType(str, Enum):
    # ... 现有事件保持不变 ...

    # 新增流式事件
    TOKEN_DELTA = "token_delta"           # LLM token 增量
    NODE_STARTED = "node_started"         # 图节点开始执行
    NODE_COMPLETED = "node_completed"     # 图节点执行完成
    TOOL_STARTED = "tool_started"         # 工具开始执行
    TOOL_COMPLETED = "tool_completed"     # 工具执行完成
    STATE_SNAPSHOT = "state_snapshot"     # 状态快照（节点完成后）
```

### 3.2 SSE 事件格式

```
event: token_delta
data: {"session_id": "xxx", "node": "reason", "content": "诊断", "run_id": "yyy"}

event: node_started
data: {"session_id": "xxx", "node": "reason", "run_id": "yyy"}

event: node_completed
data: {"session_id": "xxx", "node": "reason", "state_keys": ["trace_items", "pending_tool_calls"]}

event: tool_started
data: {"session_id": "xxx", "tool": "k8s.list_pods", "params": {...}, "run_id": "zzz"}

event: tool_completed
data: {"session_id": "xxx", "tool": "k8s.list_pods", "success": true, "duration_ms": 1200}

event: state_snapshot
data: {"session_id": "xxx", "node": "reason", "step_count": 3, "status": "running",
       "new_trace_items": [...], "diagnosis_result": null}

event: done
data: {"session_id": "xxx", "status": "diagnosed", "summary": "..."}
```

---

## 4. 后端改动

### 4.1 `sre_agent/agent/graph.py` — 新增 `run_diagnosis_stream()`

在现有 `run_diagnosis()` 旁边新增异步生成器：

```python
NODE_NAMES = {"load_and_select_skill", "reason", "act", "observe", "decide",
              "execute_selected_skill", "finalize"}


async def run_diagnosis_stream(
    *,
    query: str,
    context: ToolExecutionContext | None,
    variables: dict[str, Any] | None = None,
    alert_snapshot: dict[str, Any] | None = None,
    topology_context: dict[str, Any] | None = None,
    extra_alerts: list[dict[str, Any]] | None = None,
    # ... 所有 run_diagnosis 的参数 ...
    session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """流式运行诊断，yield 每个事件。"""
    active_session_id = session_id or uuid4().hex
    graph = create_sre_graph(
        llm=llm, ...,
        trace_callback=None,  # 流式模式不使用 trace_callback
    )
    initial_state = initialize_state(...)

    # 先 yield 开始事件
    yield {
        "type": "diagnosis_started",
        "session_id": active_session_id,
        "data": {"alert": alert_snapshot, "topology": topology_context},
    }

    async for event in graph.astream_events(
        initial_state,
        config={"configurable": {"thread_id": active_session_id}},
        version="v2",
    ):
        kind = event["event"]
        name = event.get("name", "")
        data = event.get("data", {})

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                yield {
                    "type": "token_delta",
                    "session_id": active_session_id,
                    "data": {"content": chunk.content, "node": name},
                }

        elif kind == "on_chain_start" and name in NODE_NAMES:
            yield {
                "type": "node_started",
                "session_id": active_session_id,
                "data": {"node": name},
            }

        elif kind == "on_chain_end" and name in NODE_NAMES:
            output = data.get("output", {})
            yield {
                "type": "node_completed",
                "session_id": active_session_id,
                "data": {
                    "node": name,
                    "status": output.get("status"),
                    "step_count": output.get("step_count"),
                    "new_trace_items": (output.get("trace_items") or [])[-1:],
                    "diagnosis_result": output.get("diagnosis_result"),
                },
            }

        elif kind == "on_tool_start":
            yield {
                "type": "tool_started",
                "session_id": active_session_id,
                "data": {"tool": name, "params": data.get("input", {})},
            }

        elif kind == "on_tool_end":
            yield {
                "type": "tool_completed",
                "session_id": active_session_id,
                "data": {"tool": name, "result": data.get("output")},
            }

    yield {"type": "done", "session_id": active_session_id, "data": {"status": "completed"}}
```

**关键点**：
- `create_sre_graph()` **无需修改**，`astream_events` 由编译后的 graph 自动支持
- 流式模式下 `trace_callback=None`，事件通过 `astream_events` 直接获取
- 保持 `run_diagnosis()` 不变，向后兼容

### 4.2 `sre_agent/server.py` — 新增 `StreamingDiagnosisRunner`

```python
class StreamingDiagnosisRunner:
    def __init__(self, execution_context, tool_registry, config, ontology, trace_publisher):
        self._execution_context = execution_context
        self._tool_registry = tool_registry
        self._config = config
        self._ontology = ontology
        self._trace_publisher = trace_publisher

    async def astream_diagnose(
        self, alert: Alert, extra_alerts: list[Alert] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式诊断，yield SSE 事件。同时推送 WebSocket 保持向后兼容。"""
        topology_context = _build_alert_blast_radius_context(self._ontology, alert)
        query = f"{_build_default_query(alert)}\n{topology_context['summary']}"
        variables = _build_runtime_diagnosis_variables(...)

        async for event in run_diagnosis_stream(
            query=query,
            context=self._execution_context,
            variables=variables,
            tool_registry=self._tool_registry,
            ...
        ):
            # 同时推送 WebSocket 事件（向后兼容）
            if event["type"] in {"node_completed", "diagnosis_started", "done"}:
                await self._trace_publisher.publish(event)
            yield event
```

在 `create_app()` 中注入到 services：

```python
streaming_runner = StreamingDiagnosisRunner(
    execution_context=context,
    tool_registry=registry,
    config=cfg,
    ontology=ontology_graph,
    trace_publisher=publisher,
)
services = AgentCServices(
    ...,
    streaming_diagnosis_runner=streaming_runner,  # 新增字段
)
```

### 4.3 `sre_agent/api/routes.py` — 新增 SSE 端点

```python
from sse_starlette.sse import EventSourceResponse


@router.post("/diagnose/stream")
async def diagnose_stream(
    alert: Alert,
    request: Request,
    user: CurrentUser = Depends(require_role("operator", "admin")),
    extra_alert_fingerprints: list[str] = Query(default_factory=list),
) -> EventSourceResponse:
    services = _services(request)
    blocked_alert_names = _blocked_alert_names_from_request(request)
    if is_blocked_alert(alert, blocked_names=blocked_alert_names):
        raise HTTPException(status_code=400, detail="alert is blocked")
    if services.streaming_diagnosis_runner is None:
        raise HTTPException(status_code=500, detail="streaming diagnosis runner is not configured")

    prepared_alert = _enrich_alert_with_topology_summary(services, alert)
    extra_alerts = _lookup_extra_alerts(services.alert_store, extra_alert_fingerprints) if extra_alert_fingerprints else []
    runner = services.streaming_diagnosis_runner

    async def event_generator():
        try:
            async for event in runner.astream_diagnose(prepared_alert, extra_alerts=extra_alerts):
                yield {
                    "event": event["type"],
                    "data": json.dumps(event, ensure_ascii=False, default=str),
                }
        except Exception as exc:
            yield {
                "event": "error",
                "data": json.dumps({"message": str(exc)}),
            }

    return EventSourceResponse(event_generator(), ping=15)
```

**依赖**：需要添加 `sse-starlette` 包。

### 4.4 `sre_agent/models/events.py` — 扩展 EventType

```python
class EventType(str, Enum):
    # ... 现有 ...

    # 流式事件
    TOKEN_DELTA = "token_delta"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    STATE_SNAPSHOT = "state_snapshot"
```

---

## 5. 前端改动

### 5.1 API Client 新增 SSE 支持

```typescript
// src/api/client.ts 新增
export interface SSEEvent {
  type: string;
  session_id: string;
  data: Record<string, unknown>;
}

export async function streamDiagnosis(
  alert: unknown,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const baseURL = getBaseURL();
  const token = getToken();
  const response = await fetch(`${baseURL}/api/diagnose/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(alert),
    signal,
  });

  if (!response.ok) {
    throw new Error(`streaming diagnosis failed: ${response.status}`);
  }
  if (!response.body) {
    throw new Error("response body is null");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    let currentEvent = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ") && currentEvent) {
        try {
          const data = JSON.parse(line.slice(6));
          onEvent({ type: currentEvent, session_id: data.session_id, data: data.data || data });
        } catch {
          // 忽略解析失败
        }
        currentEvent = "";
      }
    }
  }
}
```

### 5.2 Diagnosis Store 新增流式处理

```typescript
// src/store/diagnosisStore.ts 扩展
interface DiagnosisState {
  // ... 现有字段 ...
  streamingText: string;
  currentNode: string | null;
  isStreaming: boolean;
  activeTools: Array<{ tool: string; params: Record<string, unknown> }>;
}

// 新增 action
startStreamingDiagnosis: async (alert: Alert) => {
  const controller = new AbortController();
  set({ streamingText: "", isStreaming: true, currentNode: null, activeTools: [] });

  try {
    await streamDiagnosis(
      alert,
      (event) => {
        switch (event.type) {
          case "token_delta":
            set((state) => ({
              streamingText: state.streamingText + (event.data.content || ""),
            }));
            break;
          case "node_started":
            set({ currentNode: event.data.node as string });
            break;
          case "node_completed":
            // 处理节点完成后的状态更新（trace items、diagnosis_result 等）
            if (event.data.new_trace_items) {
              // 追加 trace items
            }
            if (event.data.diagnosis_result) {
              // 更新诊断结果
            }
            set((state) => ({ streamingText: "", currentNode: null }));
            break;
          case "tool_started":
            set((state) => ({
              activeTools: [...state.activeTools, { tool: event.data.tool, params: event.data.params }],
            }));
            break;
          case "tool_completed":
            set((state) => ({
              activeTools: state.activeTools.filter((t) => t.tool !== event.data.tool),
            }));
            break;
          case "done":
            set({ isStreaming: false, currentNode: null });
            break;
        }
      },
      controller.signal,
    );
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      set({ isStreaming: false });
    }
  }
},
```

### 5.3 DiagnosisPage 使用流式组件

现有 `@ant-design/x` 的 `Think` 和 `Bubble` 组件已支持流式文本，只需将 `streamingText` 传入：

```tsx
// 现有 Think 组件直接支持 streaming content
<Think content={streamingText} />
```

页面组件中新增流式触发逻辑：

```tsx
const handleStreamDiagnose = (alert: Alert) => {
  diagnosisStore.startStreamingDiagnosis(alert);
};

// 在渲染中
{isStreaming && streamingText && (
  <Think content={streamingText} />
)}
{currentNode && (
  <div className="node-indicator">正在执行: {currentNode}</div>
)}
```

---

## 6. 改动量评估

### 后端

| 文件 | 改动类型 | 改动量 | 说明 |
|------|----------|--------|------|
| [graph.py](../agent/graph.py) | **新增函数** | ~80 行 | 新增 `run_diagnosis_stream()` |
| [server.py](../server.py) | **新增类+注册** | ~60 行 | `StreamingDiagnosisRunner` + 注入到 services |
| [routes.py](../api/routes.py) | **新增端点** | ~30 行 | `/diagnose/stream` SSE 端点 |
| [events.py](../models/events.py) | **扩展枚举** | ~5 行 | 新增事件类型 |
| requirements / pyproject.toml | **新增依赖** | 1 行 | `sse-starlette` |
| **总计** | | **~175 行** | |

### 前端

| 文件 | 改动类型 | 改动量 | 说明 |
|------|----------|--------|------|
| [client.ts](../frontend/src/api/client.ts) | **新增函数** | ~50 行 | `streamDiagnosis()` SSE 客户端 |
| [diagnosisStore.ts](../frontend/src/store/diagnosisStore.ts) | **扩展 action** | ~40 行 | 流式状态管理 |
| [Diagnosis.tsx](../frontend/src/pages/Diagnosis.tsx) | **小改** | ~20 行 | 使用流式状态替代 WebSocket 拉取 |
| **总计** | | **~110 行** | |

### 不需要改动的部分

- **`create_sre_graph()`** — graph 定义不变，`astream_events` 对编译后的 graph 自动可用
- **[nodes.py](../agent/nodes.py)** — 所有节点逻辑完全不变
- **[state.py](../agent/state.py)** — state schema 不变
- **WebSocket 端点** ([websocket.py](../api/websocket.py)) — 保持原样向后兼容
- **前端 WebSocket hooks** — 保持原样
- **现有 `run_diagnosis()` 调用点** — 不受影响

---

## 7. 前后端契约

### 请求

```
POST /api/diagnose/stream
Content-Type: application/json
Authorization: Bearer <token>

{
  "alert_name": "GPUThermalThrottle",
  "severity": "critical",
  "labels": {"node": "gpu-node-01", "namespace": "service"},
  "annotations": {"summary": "GPU 温度过高触发降频"},
  "summary": "GPU thermal throttle detected"
}
```

### 响应

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

event: diagnosis_started
data: {"session_id":"abc123","data":{"alert":{"alert_name":"GPUThermalThrottle"},"topology":{"summary":"..."}}}

event: node_started
data: {"session_id":"abc123","data":{"node":"load_and_select_skill"}}

event: node_completed
data: {"session_id":"abc123","data":{"node":"load_and_select_skill","status":"running"}}

event: node_started
data: {"session_id":"abc123","data":{"node":"reason"}}

event: token_delta
data: {"session_id":"abc123","data":{"content":"正在","node":"ChatOpenAI"}}

event: token_delta
data: {"session_id":"abc123","data":{"content":"分析","node":"ChatOpenAI"}}

event: token_delta
data: {"session_id":"abc123","data":{"content":"GPU","node":"ChatOpenAI"}}

event: node_completed
data: {"session_id":"abc123","data":{"node":"reason","status":"running","step_count":1,"new_trace_items":[{"type":"thought","step":1,"content":"...","action":"tool_call","tool_name":"gpu.get_metrics"}]}}

event: node_started
data: {"session_id":"abc123","data":{"node":"act"}}

event: tool_started
data: {"session_id":"abc123","data":{"tool":"gpu.get_metrics","params":{"node":"gpu-node-01"}}}

event: tool_completed
data: {"session_id":"abc123","data":{"tool":"gpu.get_metrics","success":true}}

event: node_completed
data: {"session_id":"abc123","data":{"node":"act","status":"running"}}

... (reason → act → observe → decide → reason → ... 循环)

event: node_started
data: {"session_id":"abc123","data":{"node":"finalize"}}

event: node_completed
data: {"session_id":"abc123","data":{"node":"finalize","status":"diagnosed","diagnosis_result":{"root_cause":"...","confidence":0.85}}}

event: done
data: {"session_id":"abc123","data":{"status":"diagnosed","summary":"GPU thermal throttle caused by failed fan module"}}
```

---

## 8. 诊断流程对比

### 改造前

```
用户点击诊断 → POST /diagnose → 等待 30-120s → 返回完整结果
                                    ↓ (同时)
                              WS 推送节点级事件（批量）
```

用户体验：点击后长时间等待，期间只有零星的 WebSocket 事件。

### 改造后

```
用户点击诊断 → POST /diagnose/stream → 立即开始接收 SSE 流
                                         ↓
                                   token_delta (逐字显示思考过程)
                                   node_started → node_completed
                                   tool_started → tool_completed
                                   ... 循环 ...
                                   done (最终诊断结果)
```

用户体验：点击后立即看到 LLM 思考过程逐字显示，类似 Claude.ai 的交互体验。

---

## 9. 风险与注意事项

### 9.1 LLM Provider 兼容性

当前使用 MiniMax-M2.7，需确认其 OpenAI 兼容 API 是否支持 `stream=True`。

- **支持 streaming**：`on_chat_model_stream` 正常触发，实现完整 token 级流式
- **不支持 streaming**：`on_chat_model_stream` 事件可能不存在，退化为节点级流式（仍比 `ainvoke` 更好，因为每个节点完成后立即推送）
- 代码中需要**防御性处理**：如果未收到 `token_delta` 事件，前端显示节点级别的批量文本即可

### 9.2 超时控制

- SSE 连接需要设置合理的超时，建议 600s（与现有 `total_timeout_sec` 一致）
- `sse-starlette` 支持 `ping` 参数定期发送心跳，建议设 15s
- 前端 `AbortController` 允许用户取消正在进行的流式诊断

### 9.3 断线重连

- SSE 标准 `Last-Event-ID` 可用于断线后从断点恢复
- 当前方案中每个诊断是独立的流，重连意义有限（不同于长期订阅）
- 可通过 `InMemoryTracePublisher` 记录已推送事件供重放

### 9.4 并发限制

- 流式诊断占用 HTTP 连接更久（比普通 REST 请求长 10x-100x）
- 建议设置并发诊断数量限制（如同时最多 5 个流式诊断）
- 可在 `AgentCServices` 中增加 `max_concurrent_streaming` 配置

### 9.5 向后兼容

- 现有的 `POST /diagnose` 和 `POST /diagnose/start` 端点**完全不变**
- WebSocket `/ws/thinking-trace/{session_id}` **保持原样**
- 新端点 `/api/diagnose/stream` 是**纯增量**

---

## 10. 验证方案

### 10.1 单元测试

测试 `run_diagnosis_stream()` 产生的事件序列：

```python
async def test_run_diagnosis_stream_emits_events():
    events = []
    async for event in run_diagnosis_stream(query="test", context=..., ...):
        events.append(event)

    # 验证事件序列
    assert events[0]["type"] == "diagnosis_started"
    assert any(e["type"] == "token_delta" for e in events)
    assert events[-1]["type"] == "done"
```

### 10.2 集成测试

启动 FastAPI，用 curl 测试 SSE 端点：

```bash
curl -N -X POST http://localhost:8080/api/diagnose/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"alert_name":"TestAlert","severity":"warning","labels":{},"annotations":{},"summary":"test"}'
```

预期输出为 SSE 格式的事件流。

### 10.3 前端测试

在 DiagnosisPage 触发流式诊断，验证：
1. 思考文本逐字显示
2. 工具调用出现执行指示器
3. 诊断结果流式渲染
4. 完成后状态正确更新

### 10.4 兼容性测试

确认旧的 WebSocket `/ws/thinking-trace/{session_id}` 仍能收到事件。

### 10.5 降级测试

当 LLM provider 不支持 streaming 时，确认节点级流式仍正常工作。
