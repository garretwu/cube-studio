# SRE Agent 启动过程分析

## 一、前后端启动方式

### 1.1 Docker 容器模式

**入口链路**: `container_entrypoint.sh` → `run_web_stack.py`

```
container_entrypoint.sh
  ├── 设置环境变量 (BACKEND_PORT, FRONTEND_PORT, CONFIG_PATH, KUBECONFIG 等)
  ├── 创建数据目录 (checkpoints, llm_logs, memory, knowledge_db, wal)
  └── exec python run_web_stack.py
        ├── ensure_frontend_dependencies()  — 检查 node_modules 是否存在
        ├── build_runtime_env()             — 生成 JWT secret / access / refresh token
        ├── _spawn_backend()                — python -m sre_agent serve ...
        ├── wait_backend_ready()            — 轮询 /openapi.json (1s间隔, 最长180s)
        └── _spawn_frontend()               — npm run dev --host --port
```

启动特征：
- 前端**等待后端就绪**后才启动（串行）
- 后端就绪判断依据：`/openapi.json` 返回 200
- 后端/前端进程由 Python 主进程统一管理生命周期

### 1.2 本地开发模式

**入口**: `sre_agent/scripts/start_frontend_backend.py`

与 Docker 模式类似，额外支持：
- 端口自动分配（检测占用后自动 +1）
- 本地 LLM 探测（`--llm-mode=openai_compatible_local` 时会先探测 llama.cpp）
- 运行时信息输出到 `sre_agent/temp/dev_runtime_info.json`

---

## 二、启动步骤及耗时分析

### 2.1 完整启动时序

```
 ┌──────────────────────────────────────────────────────┐
 │  container_entrypoint.sh                             │
 │  ┌────────────────────────────────────────────────┐  │
 │  │ 1. 环境变量 + 目录创建                     <1s │  │
 │  ├────────────────────────────────────────────────┤  │
 │  │ 2. ensure_frontend_dependencies             <1s │  │
 │  ├────────────────────────────────────────────────┤  │
 │  │ 3. build_runtime_env (JWT/token 生成)       <1s │  │
 │  ├────────────────────────────────────────────────┤  │
 │  │ 4. _spawn_backend → python -m sre_agent serve│  │
 │  │    ┌──────────────────────────────────────┐   │  │
 │  │    │ 4a. Config 加载 (YAML→Pydantic)  <1s │   │  │
 │  │    │ 4b. LLM 运行时状态检查          <1s  │   │  │
 │  │    │ 4c. create_app()               2-5s  │   │  │
 │  │    │ 4d. uvicorn 启动               1-2s  │   │  │
 │  │    │ 4e. lifespan 启动事件        10-100s+ │   │  │
 │  │    └──────────────────────────────────────┘   │  │
 │  ├────────────────────────────────────────────────┤  │
 │  │ 5. wait_backend_ready (轮询 openapi.json)      │  │
 │  │    等待 4e 完成后返回                          │  │
 │  ├────────────────────────────────────────────────┤  │
 │  │ 6. _spawn_frontend → npm run dev         2-5s  │  │
 │  └────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────┘
```

### 2.2 后端启动详细步骤 (`create_app`)

源文件: `sre_agent/server.py:2604` — `create_app()`

| # | 步骤 | 代码位置 | 预估耗时 | 说明 |
|---|------|----------|----------|------|
| 1 | Config 加载 | `SREAgentConfig()` | <1s | YAML → Pydantic 模型 |
| 2 | JWT 设置解析 | `resolve_jwt_settings()` | <1s | 从 config 中读取 JWT 配置 |
| 3 | LLM 运行时状态 | `_collect_llm_runtime_status()` | <1s | 检查 API Key/Model/Base URL |
| 4 | Ontology Graph | `OntologyGraph(db_path)` | <1s | SQLite 数据库实例化 |
| 5 | Memory Store | `create_memory_store()` | <1s | SQLite/PG 工厂创建 |
| 6 | Knowledge Store | `_build_knowledge_store()` | <1s | 本地向量库或 Dify 适配器 |
| 7 | Tool Registry | `build_default_registry()` | <1s | 注册所有诊断工具 |
| 8 | **Tool Channel Bootstrap** | `bootstrap_tool_channels()` | **3-15s** | **10 个 channel 顺序初始化** |
| 9 | Diagnosis Runner | `DefaultDiagnosisRunner()` | <1s | 组装诊断运行器 |
| 10 | Re-Diagnose Runner | `DefaultReDiagnoseRunner()` | <1s | 组装重新诊断运行器 |
| 11 | Remediation Engine | `RemediationEngine()` | <1s | 修复引擎（含审批/WAL/验证器） |
| 12 | Session/Loop/Trace Store | 多个 Store 实例化 | <1s | 会话/循环/追踪存储 |
| 13 | IncidentHandler | `IncidentHandler()` | <1s | 告警去重/关联/分发 |
| 14 | ConversationalAgent | `ConversationalAgent()` | 1-3s | Chat Handler，可能涉及 LLM 连接验证 |
| 15 | TopologyDiscoveryService | `TopologyDiscoveryService()` | <1s | 拓扑发现服务实例化 |

### 2.3 Tool Channel Bootstrap 详情

源文件: `sre_agent/runtime/channels.py:315` — `bootstrap_tool_channels()`

Channel 初始化**全部串行**，顺序如下：

| # | Channel | 初始化函数 | 潜在耗时 | 说明 |
|---|---------|-----------|----------|------|
| 1 | ontology | 内联创建 | <1s | 包装 OntologyGraph |
| 2 | memory | `_ensure_memory_channel()` | <1s | 包装 MemoryStore |
| 3 | knowledge | `_ensure_knowledge_channel()` | <1s | 包装 KnowledgeStore |
| 4 | **k8s** | `_ensure_k8s_channel()` | 1-3s | 加载 kubeconfig，创建 K8s 客户端 |
| 5 | **prometheus** | `_ensure_prometheus_channel()` | 1-3s | 创建 Prometheus HTTP 客户端 |
| 6 | log | `_ensure_log_channel()` | <1s | Loki/日志客户端 |
| 7 | **ssh** | `_ensure_ssh_channel()` | <1s | SSH 配置加载（不立即连接） |
| 8 | switch | `_ensure_switch_channel()` | <1s | 交换机通道 |
| 9 | **redfish** | `_ensure_redfish_channel()` | <1s | BMC 通道配置加载（不立即认证） |
| 10 | cube_studio | `_ensure_cube_studio_channel()` | <1s | Cube Studio API 客户端 |
| 11 | alert | `_ensure_alert_channel()` | <1s | Alertmanager 客户端 |

> 注：Channel bootstrap 阶段只做客户端实例化，大多数不会发起网络请求。实际网络 I/O 集中在 lifespan 阶段。

### 2.4 Lifespan 启动事件详情

源文件: `sre_agent/server.py:2823` — `lifespan()` 异步上下文管理器

| # | 步骤 | 代码行 | 预估耗时 | 瓶颈分析 |
|---|------|--------|----------|----------|
| 1 | `ontology_graph.connect()` | :2825 | 1-3s | SQLite 连接初始化 |
| 2 | `memory_store.connect()` | :2827 | 1-3s | SQLite/PG 连接初始化 |
| 3 | **`_preload_redfish_sessions()`** | :2828 | **5-30s+** | **串行登录多台 BMC 设备，逐台认证，受网络延迟影响** |
| 4 | **`_precheck_ttft_external_ssh_access()`** | :2833 | **3-15s** | **SSH 到远端节点执行 whoami/hostname/ps，受网络延迟影响** |
| 5 | `alert_polling_service.start()` | :2839 | 1-2s | 启动告警轮询后台任务 |
| 6 | **`topology_discovery_service.start()`** | :2841 | **5-60s+** | **首次触发全量拓扑扫描（hybrid 模式下扫描 K8s+Prom+Switch+BMC）** |

**关键瓶颈**:

1. **Redfish 预认证** (`_preload_redfish_sessions`, :2415)
   - 仅在 `live` 或 `hybrid` 发现模式下执行
   - 从 live inventory 或环境变量加载 BMC 凭据
   - **逐台串行认证**，每台涉及 HTTP POST 登录
   - N 台 BMC × 单次登录延迟 ≈ N × (1-3s)

2. **SSH 预检查** (`_precheck_ttft_external_ssh_access`, :2495)
   - 仅在配置了 `ttft_external_process_default_node` 时执行
   - 通过 SSH 执行 `whoami`、`hostname`、`ps` 命令
   - 受目标节点网络连通性和响应速度影响

3. **拓扑发现首次运行** (`topology_discovery_service.start()`)
   - hybrid 模式下启动时立即触发
   - 涉及 K8s 资源扫描、Prometheus 查询、交换机发现、BMC 扫描
   - 各 scanner 串行执行
   - 完成前 `/openapi.json` 已经可访问（uvicorn 先启动），但数据未就绪

### 2.5 前端启动耗时

| # | 步骤 | 预估耗时 | 说明 |
|---|------|----------|------|
| 1 | `npm run dev` Vite 编译启动 | 2-5s | 开发模式按需编译 |
| 2 | 浏览器加载页面 + JS bundle | 1-3s | 首次加载 |
| 3 | 初始 API 调用（诊断历史等） | 1-3s | Axios → `/api/diagnosis/sessions` |
| 4 | WebSocket 连接建立 | <1s | `/ws/alerts`, `/ws/topology` |

前端本身启动很快（~5-10s），**主要瓶颈是等待后端就绪**。

---

## 三、前后端建链过程

### 3.1 两种连接模式

**Proxy 模式**（默认，`api_mode=proxy`）:

```
Browser ──→ Vite dev server (:8080)
              ├─ /api/*  ──proxy──→ FastAPI backend (:8000)
              └─ /ws/*   ──ws proxy──→ FastAPI backend (:8000)
```

- 前端无需配置 `VITE_API_BASE_URL`
- Vite 的 `vite.config.ts` 将 `/api` 和 `/ws` 代理到 `VITE_API_PROXY_TARGET`（默认 `http://127.0.0.1:8000`）

**Direct 模式**（`api_mode=direct`）:

```
Browser ──→ Vite dev server (:8080) — 静态资源
  └─ API/WS 请求直接发到 VITE_API_BASE_URL (http://backend:8000)
```

- 需要设置 `VITE_API_BASE_URL`
- 适合前端独立部署场景

### 3.2 JWT 认证流程

源文件: `sre_agent/frontend/src/api/client.ts`

```
启动时:
  build_runtime_env()
    ├── secrets.token_urlsafe(48)        — 生成 JWT_SECRET
    ├── jwt.encode(access_token)          — 生成 VITE_API_TOKEN
    └── jwt.encode(refresh_token)         — 生成 VITE_API_REFRESH_TOKEN

前端请求拦截:
  Axios request interceptor
    └── headers.Authorization = "Bearer <VITE_API_TOKEN>"

Token 刷新:
  Axios response interceptor
    └── 401 响应 → 自动使用 refresh_token 换取新 access_token
        ├── 成功: 重试原请求
        └── 失败: 进入 terminal 状态，提示重新认证

Boot ID 追踪:
  每次请求检查 X-Server-Boot-Id header
  变化时判定后端重启，清空本地缓存状态
```

### 3.3 WebSocket 连接

源文件: `sre_agent/frontend/src/api/ws.ts` — `ManagedWebSocket`

| 端点 | 用途 | 触发时机 |
|------|------|----------|
| `/ws/alerts` | 实时告警推送 | App 启动时自动连接 (`useAlertsRealtimeSync`) |
| `/ws/topology` | 拓扑变更通知 | App 启动时自动连接 |
| `/ws/chat` | 对话式 AI 交互 | 用户发起对话时 |
| `/ws/thinking-trace/{session_id}` | 诊断过程实时流 | 发起诊断时 |

WebSocket 特性：
- Token 通过 `?token=` query param 传递
- 断线自动重连 + 指数退避策略
- 离线期间消息缓冲
- 60s 告警轮询作为 WebSocket 的降级方案

### 3.4 完整建链时序

```
  run_web_stack.py                        Frontend (Browser)
  ──────────────                          ─────────────────
      │
      ├── _spawn_backend()                │
      │     │                             │
      │     ├── Config 加载               │
      │     ├── create_app()              │
      │     │     ├── 12 Channel 初始化   │
      │     │     ├── Runner/Engine 组装  │
      │     │     └── Chat Handler        │
      │     ├── uvicorn.startup           │
      │     │     ├── ontology.connect()  │
      │     │     ├── memory.connect()    │
      │     │     ├── Redfish 预认证 ★    │
      │     │     ├── SSH 预检查 ★        │
      │     │     ├── Alert 轮询启动      │
      │     │     └── 拓扑发现启动 ★      │
      │     │                             │
      │     └── /openapi.json 就绪 ✅     │
      │                                   │
      ├── wait_backend_ready() ←──────────│ (轮询探测)
      │                                   │
      └── _spawn_frontend() ─────────────→│
                                          │
            npm run dev 启动              │
            浏览器加载页面 ──────────────→│
            ├── 初始化 TokenManager       │
            ├── 读取 VITE_API_TOKEN       │
            ├── 初始 API 调用 (JWT) ─────→│ GET /api/...
            ├── WebSocket /ws/alerts ────→│ 实时告警
            └── WebSocket /ws/topology ──→│ 拓扑变更
```

★ 标记为主要耗时瓶颈

---

## 四、优化建议

### 4.1 后端启动优化

| 方向 | 具体措施 | 预期收益 |
|------|----------|----------|
| **并行 Channel 初始化** | `asyncio.gather` 并行初始化 K8s/Prometheus/SSH 等 Channel | 减少 3-8s |
| **Redfish 延迟认证** | 改为 lazy，首次使用时才登录 BMC，不阻塞 lifespan | 减少 5-30s |
| **SSH 延迟预检查** | 改为 lazy，首次 TTFT 告警时才检查 | 减少 3-15s |
| **拓扑发现异步化** | lifespan 不等待首次发现完成，后台异步执行 | 减少 5-60s |
| **分离就绪检查** | `/openapi.json` 仅表示进程存活，增加 `/health` 表示完全就绪 | 前端可更早启动 |

### 4.2 前后端协调优化

| 方向 | 具体措施 | 预期收益 |
|------|----------|----------|
| **前后端并行启动** | 前端不等待后端就绪，直接启动 Vite | 减少 5-10s |
| **前端优雅降级** | 后端未就绪时显示 loading 状态，API 失败时自动重试 | 用户体验提升 |
| **渐进式就绪** | 后端分阶段暴露就绪状态（核心 API 先就绪，拓扑数据异步加载） | 用户可更早操作 |

### 4.3 Docker 层面

| 方向 | 具体措施 |
|------|----------|
| **预装依赖** | 在 Dockerfile 中执行 `npm install`，避免运行时检查 |
| **健康检查** | 使用 `healthcheck` 代替简单的 `depends_on` |
| **预热脚本** | 容器启动后执行预热请求，提前触发 JIT 编译和连接池初始化 |
