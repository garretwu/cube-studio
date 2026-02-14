# Cube Studio Load Simulator 设计规格

## 1. 概述

### 1.1 目标

为 Cube Studio 平台设计一个全功能负载模拟器，覆盖推理服务、训练流水线、模型微调、Notebook 在线开发四大核心功能。系统采用**确定性负载引擎 + LLM 瓶颈分析**的混合架构：负载生成/采集由确定性 Python+asyncio 引擎执行（精确控制并发、速率、阶段），瓶颈分析由 MiniMax-2.1 提供跨层关联推理。模拟器能够：

- 对各功能模块进行独立或组合压力测试
- 通过可配置参数调整负载强度、并发度、token 分布等
- 压出系统极限，暴露链路上各组件的瓶颈
- 自动采集多层指标并生成分析报告

### 1.2 目标系统配置

| 集群 | 节点数 | CPU | 内存 | 存储 | 网络 |
|------|--------|-----|------|------|------|
| 控制集群 | 3 | AMD 9354 ×2 | 32G DDR5 ×6 (192G) | 960G NVMe SSD RAID1 | 25G |
| 存储集群 | 3 | AMD 9354 ×2 | 32G DDR5 ×8 (256G) | 960G NVMe SSD RAID1 + 7.68T TLC ×12 | 25G + 200G |

### 1.3 已知系统容量参数

| 参数 | 值 | 来源 |
|------|-----|------|
| Gunicorn Workers | 20 | `entrypoint.sh` |
| DB 连接池大小 | 300 | `SQLALCHEMY_POOL_SIZE` |
| DB 最大溢出 | 800 | `SQLALCHEMY_MAX_OVERFLOW` |
| DB 连接回收 | 300s | `SQLALCHEMY_POOL_RECYCLE` |
| API 最大分页 | 2000 | `FAB_API_MAX_PAGE_SIZE` |
| 最大任务 CPU | 50 核 | `MAX_TASK_CPU` |
| 最大任务内存 | 100 Gi | `MAX_TASK_MEM` |
| Celery Prefetch | 10 | `worker_prefetch_multiplier` |
| Celery 最大任务/Worker | 12000 | `worker_max_tasks_per_child` |
| 缓存 TTL | 86400s (24h) | `CACHE_DEFAULT_TIMEOUT` |
| `upgrade_service` 速率限制 | 1/s | Celery rate_limit |
| `delete_workflow` 速率限制 | 1/h | Celery rate_limit |

---

## 2. 整体架构

### 2.1 设计原则：确定性负载引擎 + LLM 瓶颈分析

负载生成是**确定性、高性能的工作**：精确控制并发数、速率阶梯、请求分布、资源生命周期。LLM 引入的延迟和非确定性会降低负载生成精度，且 LLM 不擅长管理高并发 HTTP 连接池。因此采用**混合架构**：

| 层级 | 技术 | 理由 |
|------|------|------|
| 编排 + 负载 Agent | Python + asyncio（确定性） | 精确并发控制、阶梯递增、超时管理 |
| 瓶颈分析 Agent | MiniMax-2.1（LLM） | 跨层关联分析、根因推断、趋势预测需要推理能力 |
| 通道层 | 类型化 Channel 类 | 统一抽象 Cube Studio API / Prometheus / K8s / 推理端点 |

**与 Fault Injector 对齐**：两个系统共享相同的混合架构理念 — 确定性执行 + LLM 只读分析。共享 Channel 实现（CubeStudio Channel、Prometheus Channel、K8s Channel）。

### 2.2 系统架构图

```
                          ┌─────────────────────┐
                          │   YAML 配置文件      │
                          └─────────┬───────────┘
                                    │ Pydantic 校验
                          ┌─────────▼───────────┐
                          │    Orchestrator      │
                          │  (asyncio 确定性调度) │
                          │  · 阶段管理           │
                          │  · 并发控制           │
                          │  · 资源清理           │
                          └─────────┬───────────┘
                                    │
        ┌──────────┬────────┬───────┴──────┬──────────┬──────────┐
        │          │        │              │          │          │
  ┌─────▼────┐ ┌───▼───┐ ┌─▼─────┐ ┌──────▼──┐ ┌────▼───┐ ┌───▼────────┐
  │Inference │ │Pipeli-│ │Fine-  │ │Notebook │ │Monitor │ │Bottleneck  │
  │  Agent   │ │ne     │ │Tune   │ │ Agent   │ │ Agent  │ │Analyzer    │
  │          │ │Agent  │ │Agent  │ │         │ │        │ │(LLM 驱动)  │
  │ 确定性    │ │确定性  │ │确定性  │ │ 确定性   │ │ 确定性  │ │MiniMax-2.1 │
  └────┬─────┘ └──┬────┘ └──┬────┘ └───┬─────┘ └──┬────┘ └──┬─────────┘
       │          │         │          │           │         │
       │     Channel 层 (类型化执行后端)            │         │
  ┌────▼────────────────────▼──────────▼───┐  ┌───▼────┐    │
  │          CubeStudio Channel            │  │Prometh-│    │
  │  · /inferenceservice_modelview/api/    │  │eus     │    │
  │  · /pipeline_modelview/api/            │  │Channel │    │
  │  · /notebook_modelview/api/            │  │        │    │
  │  · JWT / username 认证                 │  │        │    │
  └─────────────┬──────────────────────────┘  └───┬────┘    │
                │                                  │         │
  ┌─────────────▼──────────────────────────┐      │         │
  │         Inference Channel              │      │         │
  │  · /v1/chat/completions (推理端点直连)  │      │         │
  │  · stream / non-stream                 │      │         │
  │  · 高并发连接池                         │      │         │
  └────────────────────────────────────────┘      │         │
                                                   │         │
                                         ┌─────────▼─────────┘
                                         │  指标聚合 → LLM 分析
                                         └───────────────────┘
```

### 2.3 Agent 角色定义

| Agent | 类型 | 职责 | 通道 | 输出 |
|-------|------|------|------|------|
| **Orchestrator** | 确定性 | 解析配置、阶段调度、并发控制、资源清理 | 内部 asyncio | Session 状态、阶段转换 |
| **Inference Agent** | 确定性 | 推理服务创建/部署/压测/清理全生命周期 | CubeStudio Channel, Inference Channel | 延迟/吞吐/错误率指标 |
| **Pipeline Agent** | 确定性 | 训练 Pipeline 创建/提交/监控/清理 | CubeStudio Channel | 调度延迟、执行时间 |
| **FineTune Agent** | 确定性 | LLaMA-Factory 微调任务提交/监控 | CubeStudio Channel | 训练吞吐、GPU 利用率 |
| **Notebook Agent** | 确定性 | Notebook 创建/Kernel 模拟/停止/清理 | CubeStudio Channel, Notebook Channel | 启动延迟、Kernel 响应 |
| **Monitor Agent** | 确定性 | 三层指标持续采集 | Prometheus Channel, CubeStudio Channel | 时序指标数据 |
| **Bottleneck Analyzer** | **LLM** | 六层瓶颈关联分析、根因推断、趋势预测 | MiniMax-2.1 API | 瓶颈报告、优化建议 |

> **为什么只有 Bottleneck Analyzer 使用 LLM？**
> - 负载生成是确定性工作：精确的并发数、精确的速率、精确的 token 分布。LLM 无法比 asyncio.Semaphore 更好地管理并发
> - 高并发场景下 LLM 调用延迟（100ms+）会成为瓶颈，降低压测精度
> - 跨层关联分析才是真正需要推理能力的任务 — 从 6 层指标中识别瓶颈因果链
> - Bottleneck Analyzer 是只读分析，即使 LLM 出错也不影响压测数据

### 2.4 技术栈

| 组件 | 技术选型 | 用途 |
|------|----------|------|
| 编排引擎 | Python 3.9 + asyncio | 确定性负载调度、阶段管理、并发控制 |
| 配置校验 | Pydantic v2 + PyYAML | 类型安全的 YAML 配置解析与验证 |
| 瓶颈分析 | MiniMax-2.1 (OpenAI-compatible API) | 跨层关联分析、根因推断、趋势预测 |
| HTTP 客户端 | httpx (async) + 连接池 | Cube Studio API 调用 + 推理端点高并发请求 |
| 指标采集 | httpx → Prometheus HTTP API | PromQL 查询 |
| 数据分析 | pandas + numpy | 指标聚合、百分位计算、趋势分析 |
| 报告生成 | Jinja2 + plotly/matplotlib | HTML 报告 + 交互图表 |
| CLI | Click | 命令行接口 |
| 并发控制 | asyncio.Semaphore + RateLimiter | 精确并发度 + 速率限制 |

### 2.5 项目结构

```
load_simulator/
├── __main__.py               # python -m load_simulator 入口
├── cli.py                    # Click CLI (--config, --mode, --only, --dry-run 等)
│
├── config/
│   ├── schema.py             # Pydantic 模型: GlobalConfig, InferenceConfig, PipelineConfig 等
│   ├── loader.py             # YAML 解析 + 配置合并
│   └── defaults.py           # 默认值常量
│
├── orchestrator/
│   ├── engine.py             # 主执行引擎: init → load → observe → report
│   ├── session.py            # Session 状态持久化 (JSON), --resume 支持
│   └── scheduler.py          # 阶梯式负载调度、阶段转换逻辑
│
├── agents/
│   ├── base.py               # BaseLoadAgent ABC: setup(), run_stage(), teardown(), metrics()
│   ├── inference.py          # InferenceAgent — 推理服务全生命周期
│   ├── pipeline.py           # PipelineAgent — 训练 Pipeline 全生命周期
│   ├── finetune.py           # FineTuneAgent — LLaMA-Factory 微调
│   ├── notebook.py           # NotebookAgent — Notebook 生命周期
│   ├── monitor.py            # MonitorAgent — 三层指标采集
│   └── bottleneck.py         # BottleneckAnalyzer — MiniMax-2.1 LLM (唯一 LLM 组件)
│
├── channels/
│   ├── base.py               # BaseChannel ABC: request(), dry_run 支持
│   ├── cube_studio.py        # CubeStudio Channel: httpx async, JWT 认证, CRUD 封装
│   ├── inference.py          # Inference Channel: /v1/chat/completions, stream 支持, 连接池
│   ├── prometheus.py         # Prometheus Channel: PromQL 查询 (与 fault-injector 共享)
│   ├── notebook.py           # Notebook Channel: Jupyter API (kernel 操作)
│   └── kubernetes.py         # K8s Channel: Pod 状态查询 (与 fault-injector 共享)
│
├── load/
│   ├── profile.py            # LoadProfile: 阶梯/线性/尖峰/自定义负载曲线
│   ├── rate_limiter.py       # Token bucket / leaky bucket 速率限制器
│   ├── token_distribution.py # Token 分布生成器 (short/medium/long/mixed)
│   └── prompt_pool.py        # 推理 prompt 库: 预生成不同长度的 prompt
│
├── metrics/
│   ├── collector.py          # RequestMetrics 采集器 (per-request 指标)
│   ├── aggregator.py         # 百分位聚合 (P50/P75/P90/P95/P99)
│   ├── time_series.py        # 时序数据存储 + 窗口计算
│   └── thresholds.py         # 六层瓶颈阈值定义 + 触发检测
│
├── reporting/
│   ├── html_report.py        # Jinja2 HTML 报告生成
│   ├── charts.py             # plotly/matplotlib 图表 (延迟/吞吐/资源时间线)
│   └── comparison.py         # 历史对比报告生成
│
└── tests/
    ├── test_config.py         # 配置解析测试
    ├── test_channels.py       # Channel mock 测试
    ├── test_load_profile.py   # 负载曲线测试
    ├── test_metrics.py        # 指标聚合正确性测试
    └── test_agents.py         # Agent 逻辑测试 (dry-run 模式)
```

### 2.6 Channel 架构

Channel 是与外部系统交互的抽象层。所有 Agent 通过 Channel 发送请求，Channel 负责认证、连接池、重试、dry-run 和指标采集。

#### 2.6.1 CubeStudio Channel

```python
class CubeStudioChannel:
    """
    Cube Studio REST API 客户端。
    - JWT 或 username 认证 (Authorization header)
    - 自动重试 (指数退避)
    - dry-run 模式: 仅校验请求格式
    - 请求级指标自动采集
    """
    def __init__(self, base_url: str, auth_config: AuthConfig,
                 dry_run: bool = False, timeout: int = 30):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers=self._build_auth_headers(auth_config),
            timeout=timeout,
        )
        self.dry_run = dry_run
        self.metrics_collector = RequestMetricsCollector()

    # ---- 推理服务 ----
    async def create_inference_service(self, params: dict) -> dict:
        """POST /inferenceservice_modelview/api/"""
        ...

    async def deploy_inference_service(self, service_id: int,
                                        env: str = "test") -> dict:
        """POST /inferenceservice_modelview/api/deploy/{env}/{service_id}"""
        ...

    async def update_inference_replicas(self, service_id: int,
                                         min_replicas: int) -> dict:
        """POST /inferenceservice_modelview/api/deploy/update/"""
        ...

    async def clear_inference_service(self, service_id: int) -> dict:
        """POST /inferenceservice_modelview/api/clear/{service_id}"""
        ...

    async def delete_inference_service(self, service_id: int) -> dict:
        """DELETE /inferenceservice_modelview/api/{service_id}"""
        ...

    # ---- Pipeline ----
    async def create_pipeline(self, params: dict) -> dict:
        """POST /pipeline_modelview/api/"""
        ...

    async def create_task(self, params: dict) -> dict:
        """POST /task_modelview/api/"""
        ...

    async def run_pipeline(self, pipeline_id: int) -> dict:
        """GET /pipeline_modelview/api/run_pipeline/{pipeline_id}"""
        ...

    async def get_pipeline_status(self, pipeline_id: int) -> dict:
        """GET /pipeline_modelview/api/web/workflow/{pipeline_id}"""
        ...

    # ---- Notebook ----
    async def create_notebook(self, params: dict) -> dict:
        """POST /notebook_modelview/api/entry/jupyter"""
        ...

    async def list_notebooks(self, filters: list[dict] | None = None) -> list:
        """GET /notebook_modelview/api/list/"""
        ...

    async def reset_notebook(self, notebook_id: int) -> dict:
        """POST /notebook_modelview/api/reset/{notebook_id}"""
        ...

    async def stop_notebook(self, notebook_id: int) -> dict:
        """POST /notebook_modelview/api/stop/{notebook_id}"""
        ...
```

#### 2.6.2 Inference Channel

```python
class InferenceChannel:
    """
    推理端点直连客户端。用于绕过 Cube Studio API 直接向推理服务发送请求。
    - 高并发连接池 (可配置 max_connections)
    - stream / non-stream 模式
    - per-request 指标自动采集 (延迟, TTFT, TPS, tokens)
    - Token 分布生成器集成
    """
    def __init__(self, base_url: str, max_connections: int = 200,
                 timeout: int = 60):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            limits=httpx.Limits(max_connections=max_connections,
                                max_keepalive_connections=max_connections),
            timeout=timeout,
        )
        self.metrics = RequestMetricsCollector()

    async def chat_completion(self, model: str, prompt: str,
                               max_tokens: int, stream: bool = True
                               ) -> InferenceResult:
        """
        POST /v1/chat/completions
        返回 InferenceResult 包含: latency_ms, ttft_ms, tokens_per_second,
                                   output_tokens, status_code
        """
        start = time.monotonic()
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if stream:
            return await self._stream_request(body, start)
        else:
            return await self._non_stream_request(body, start)

    async def _stream_request(self, body: dict, start: float) -> InferenceResult:
        """Stream 模式: 逐 token 接收, 记录 TTFT 和 TPS"""
        ...

    async def _non_stream_request(self, body: dict, start: float) -> InferenceResult:
        """Non-stream 模式: 整体接收"""
        ...
```

#### 2.6.3 Prometheus Channel (与 Fault Injector 共享)

```python
class PrometheusChannel:
    """
    Prometheus HTTP API 查询。只读操作。
    与 fault-injector 共享同一实现。
    """
    async def query_instant(self, promql: str) -> float:
        """GET /api/v1/query?query=..."""
        ...

    async def query_range(self, promql: str, start: datetime,
                          end: datetime, step: str = "15s") -> list[tuple[float, float]]:
        """GET /api/v1/query_range"""
        ...
```

#### 2.6.4 Notebook Channel

```python
class NotebookChannel:
    """
    Jupyter Notebook API 客户端。直接操作 Jupyter Kernel (绕过 Cube Studio API)。
    - Kernel 创建/执行/销毁
    - WebSocket 输出接收
    """
    async def create_kernel(self, notebook_url: str) -> str:
        """POST /api/kernels → kernel_id"""
        ...

    async def execute_code(self, notebook_url: str, kernel_id: str,
                           code: str) -> ExecResult:
        """POST /api/kernels/{id}/execute"""
        ...

    async def shutdown_kernel(self, notebook_url: str, kernel_id: str):
        """DELETE /api/kernels/{id}"""
        ...
```

### 2.7 负载曲线引擎

负载曲线定义了并发度随时间变化的模式，是确定性编排的核心。

```python
class LoadProfile:
    """负载曲线: 控制并发度随时间的变化"""

    @staticmethod
    def step(stages: list[StageConfig]) -> LoadProfile:
        """阶梯式: 每个阶段固定并发, 阶段间瞬时跳变"""
        # [1, 10, 50, 100, 200] — 每阶段持续固定时间

    @staticmethod
    def linear(start: int, end: int, duration: int) -> LoadProfile:
        """线性递增: 从 start 匀速增长到 end"""

    @staticmethod
    def spike(base: int, spike: int, spike_duration: int,
              total_duration: int) -> LoadProfile:
        """尖峰: 稳定基线 + 周期性尖峰"""

    def concurrency_at(self, elapsed_seconds: float) -> int:
        """返回指定时刻的目标并发数"""

class RateLimiter:
    """Token bucket 速率限制器"""
    def __init__(self, rate: float, burst: int = 1):
        self.rate = rate       # 每秒允许的请求数
        self.burst = burst     # 突发容量

    async def acquire(self):
        """等待直到获得令牌"""
        ...
```

### 2.8 Session 状态与持久化

```python
@dataclass
class Session:
    session_id: str                           # UUID
    config_hash: str                          # YAML 配置文件 SHA256
    started_at: datetime
    status: Literal["running", "paused", "completed", "failed"]
    mode: Literal["single", "mixed", "stress", "soak"]
    active_agents: list[str]                  # 当前运行的 Agent 列表
    current_stages: dict[str, int]            # agent_name → current_stage_index
    created_resources: list[CreatedResource]   # 已创建的资源 (用于清理)
    metrics_snapshots: list[MetricsSnapshot]   # 周期性指标快照
    events: list[Event]                       # 完整事件时间线

@dataclass
class CreatedResource:
    """跟踪已创建的 Cube Studio 资源，确保测试结束后清理"""
    resource_type: Literal["inference_service", "pipeline", "notebook"]
    resource_id: int
    name: str
    created_at: datetime
    cleaned: bool = False

# Session 目录结构:
# reports/sessions/{session_id}/
# ├── session.json          # Session 元数据
# ├── events.jsonl          # 事件时间线 (append-only)
# ├── metrics/              # 指标快照
# │   ├── {timestamp}.json
# │   └── ...
# ├── request_logs/         # 请求级日志 (可选)
# │   ├── inference.jsonl
# │   ├── pipeline.jsonl
# │   └── notebook.jsonl
# └── report/               # 最终报告
#
# 恢复流程 (--resume <session_id>):
# 1. 加载 session.json 确定中断位置
# 2. 检查 created_resources 中 cleaned=false 的资源
# 3. 选择: 继续测试 或 仅清理资源
```

### 2.9 认证机制

基于 Cube Studio 的 JWT 认证体系（`myapp/security.py`）：

```python
# 方式 A：JWT Token（推荐用于自动化测试）
headers = {
    "Authorization": jwt.encode(
        {"iss": "cube-studio", "sub": "<username>"},
        os.environ["JWT_SECRET"],  # 从环境变量读取
        algorithm="HS256"
    )
}

# 方式 B：简单用户名（⚠ 仅限开发环境，Demo/生产环境必须使用 JWT）
# Cube Studio 允许 Authorization header 直接传用户名（< 40 字符）
# Demo 环境应禁用此方式，配置 auth.method: "jwt"
headers = {
    "Authorization": "admin"  # 不推荐：无签名验证
}
```

认证流程：`before_request` → `check_login()` → 解析 `Authorization` header → 设置 `g.user`

跳过认证的路径：`/static/`, `/logout`, `/login`, `/register`, `/health`, `/wechat`, `/proxy`, `/llm/api/`

---

## 3. 功能 Agent 详细设计

### 3.1 Inference Agent（推理压测）

#### 3.1.1 目标

模拟真实推理服务从创建到高并发请求的完整生命周期，测试不同 token 数、不同并发度下的性能表现。

#### 3.1.2 API 调用链

```
阶段 1: 服务创建
  POST /inferenceservice_modelview/api/
  Body: {
    "name": "load-test-{uuid}",
    "model_name": "{model_name}",
    "service_type": "vllm",          # vllm / tfserving / triton-server / ollama
    "images": "{image}",
    "model_path": "{model_path}",
    "resource_memory": "16Gi",
    "resource_cpu": "4",
    "resource_gpu": "1",
    "min_replicas": 1,
    "max_replicas": 4,
    "ports": "8080",
    "project_id": {project_id}
  }

阶段 2: 服务部署
  POST /inferenceservice_modelview/api/deploy/test/{service_id}
  # 等待部署就绪（轮询 K8s Pod 状态）

阶段 3: 推理请求压测
  # 通过实际推理端点发送请求
  # 域名访问: http://{model_name}.{SERVICE_DOMAIN}/v1/chat/completions
  # IP 访问:  http://{SERVICE_EXTERNAL_IP}:{port}/v1/chat/completions
  POST /v1/chat/completions
  Body: {
    "model": "{model_name}",
    "messages": [{"role": "user", "content": "{prompt}"}],
    "max_tokens": {token_count},
    "stream": true/false
  }

阶段 4: 弹性伸缩验证
  POST /inferenceservice_modelview/api/deploy/update/
  Body: {"service_id": {id}, "min_replicas": {n}}

阶段 5: 清理
  POST /inferenceservice_modelview/api/clear/{service_id}
  DELETE /inferenceservice_modelview/api/{service_id}
```

#### 3.1.3 Token 分布策略

| 模式 | 输入 Token 范围 | 输出 max_tokens | 用途 |
|------|----------------|-----------------|------|
| 短文本 | 10-50 | 50-100 | 模拟快速问答 |
| 中等文本 | 100-500 | 200-500 | 模拟常规对话 |
| 长文本 | 500-2000 | 500-2000 | 模拟文档摘要/翻译 |
| 混合 | 随机分布 | 随机分布 | 模拟真实流量模式 |

确定性自适应行为（规则驱动，非 LLM）：
- 根据实时延迟自动调整 token 分布比例
- 检测到 OOM 或超时后降级到较短 token
- 对比 stream 与 non-stream 模式的延迟差异

#### 3.1.4 并发控制

```python
# 阶梯式并发提升
ramp_stages = [
    {"concurrency": 1,   "duration": "30s",  "purpose": "baseline"},
    {"concurrency": 5,   "duration": "60s",  "purpose": "warm-up"},
    {"concurrency": 10,  "duration": "120s", "purpose": "normal load"},
    {"concurrency": 50,  "duration": "120s", "purpose": "high load"},
    {"concurrency": 100, "duration": "120s", "purpose": "stress"},
    {"concurrency": 200, "duration": "60s",  "purpose": "breaking point"},
]
```

#### 3.1.5 采集指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `inference_latency_p50/p95/p99` | histogram | 端到端推理延迟分位数 |
| `time_to_first_token` (TTFT) | histogram | 首 token 延迟（stream 模式） |
| `tokens_per_second` (TPS) | gauge | 输出 token 吞吐 |
| `request_success_rate` | gauge | 成功率 |
| `http_status_codes` | counter | 各状态码分布 |
| `hpa_scaling_events` | counter | 自动扩缩容事件数 |
| `hpa_scaling_latency` | histogram | 扩容到就绪的延迟 |

---

### 3.2 Pipeline Agent（训练压测）

#### 3.2.1 目标

模拟多用户并发提交训练流水线，测试 Argo Workflow 调度能力、K8s 资源分配速度、DAG 执行效率。

#### 3.2.2 API 调用链

```
阶段 1: 创建 Pipeline
  POST /pipeline_modelview/api/
  Body: {
    "name": "load-train-{uuid}",
    "describe": "Load test training pipeline",
    "project_id": {project_id},
    "dag_json": "{dag_definition}",
    "namespace": "pipeline",
    "parallelism": 3
  }

阶段 2: 创建 Task（DAG 中的每个节点）
  POST /task_modelview/api/
  Body: {
    "name": "train-task-{uuid}",
    "pipeline_id": {pipeline_id},
    "job_template_id": {template_id},   # PyTorch / TF / 自定义
    "args": "{task_args_json}",
    "resource_memory": "8Gi",
    "resource_cpu": "4",
    "resource_gpu": "1",
    "retry": 0,
    "timeout": 3600
  }

阶段 3: 运行 Pipeline
  GET /pipeline_modelview/api/run_pipeline/{pipeline_id}
  # 触发 dag_to_pipeline() → 生成 Argo Workflow YAML → 提交 K8s CRD

阶段 4: 监控执行
  GET /pipeline_modelview/api/web/workflow/{pipeline_id}
  GET /pipeline_modelview/api/web/log/{pipeline_id}
  # 轮询直到 Workflow 完成/失败

阶段 5: 清理
  DELETE /pipeline_modelview/api/{pipeline_id}
```

#### 3.2.3 DAG 拓扑模式

| 模式 | 拓扑 | 任务数 | 测试目标 |
|------|------|--------|----------|
| 线性链 | A → B → C | 3-5 | 顺序调度延迟 |
| 扇出 | A → [B,C,D] | 4-8 | 并行调度能力 |
| 扇入 | [A,B,C] → D | 4-8 | 依赖等待与汇聚 |
| 菱形 | A → [B,C] → D | 4 | 混合依赖 |
| 复杂 DAG | 多层混合 | 10-20 | 大规模 DAG 调度极限 |

#### 3.2.4 并发维度

- 同时运行的 Pipeline 数量：1 → 5 → 10 → 20 → 50
- 每个 Pipeline 内的并行 Task 数：1 → 3 → 5 → 10
- 总 Pod 数 = Pipeline 数 × 平均并行 Task 数

#### 3.2.5 训练模拟负载

为测试存储 I/O（存储不单独压测，集成在训练中），Task 执行以下模拟操作：

```bash
# 模拟训练数据读取（存储 I/O 压测）
dd if=/mnt/data/train_data.bin of=/dev/null bs=1M count=1024

# 模拟 GPU 计算（如有 GPU）
python -c "
import torch
x = torch.randn(4096, 4096, device='cuda')
for i in range(1000):
    x = torch.mm(x, x)
"

# 模拟 checkpoint 写入（存储写入压测）
dd if=/dev/urandom of=/mnt/output/checkpoint.bin bs=1M count=512
```

#### 3.2.6 采集指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `pipeline_submit_latency` | histogram | 从 API 调用到 Workflow 创建的延迟 |
| `pod_scheduling_latency` | histogram | Pod 从 Pending 到 Running 的延迟 |
| `task_execution_time` | histogram | 单个 Task 的执行时间 |
| `pipeline_total_time` | histogram | 整条 Pipeline 的端到端时间 |
| `dag_overhead` | gauge | DAG 调度开销（总时间 - 各 Task 实际执行时间之和） |
| `concurrent_pods` | gauge | 同时运行的 Pod 数 |
| `storage_read_throughput` | gauge | 存储读取速率 (MB/s) |
| `storage_write_throughput` | gauge | 存储写入速率 (MB/s) |

---

### 3.3 FineTune Agent（微调压测）

#### 3.3.1 目标

模拟 LLaMA-Factory 微调任务的提交与执行，测试 LoRA 微调场景下的 GPU 利用率、存储读写、模型加载性能。

#### 3.3.2 API 调用链

微调通过 Pipeline 机制实现，使用 `LLaMA-Factory` Job Template：

```
阶段 1: 创建微调 Pipeline
  POST /pipeline_modelview/api/
  Body: {
    "name": "load-finetune-{uuid}",
    "project_id": {project_id},
    "dag_json": "{single_task_dag}",
    "namespace": "pipeline"
  }

阶段 2: 创建微调 Task
  POST /task_modelview/api/
  Body: {
    "name": "finetune-{uuid}",
    "pipeline_id": {pipeline_id},
    "job_template_id": {llama_factory_template_id},
    "args": {
      "--template": "deepseek3",
      "--finetuning_type": "lora",
      "--lora_target": "all",
      "--model_path": "/mnt/{user}/models/DeepSeek-R1-Distill-Qwen-7B/",
      "--dataset": {
        "path": "/mnt/{user}/datasets/identity.json"
      },
      "--output_dir": "/mnt/{user}/finetune/output-{uuid}/",
      "--per_device_train_batch_size": "4",
      "--gradient_accumulation_steps": "4",
      "--lr_scheduler_type": "cosine",
      "--num_train_epochs": "3",
      "--learning_rate": "5e-5"
    },
    "resource_memory": "32Gi",
    "resource_cpu": "8",
    "resource_gpu": "1"
  }

阶段 3: 运行
  GET /pipeline_modelview/api/run_pipeline/{pipeline_id}

阶段 4: 监控 GPU 指标
  # 通过 Prometheus 查询 DCGM 指标
  DCGM_FI_DEV_GPU_UTIL{pod=~".*finetune.*"}
  DCGM_FI_DEV_FB_USED{pod=~".*finetune.*"}

阶段 5: 清理
  DELETE /pipeline_modelview/api/{pipeline_id}
```

#### 3.3.3 微调参数矩阵

| 维度 | 变量范围 | 测试目标 |
|------|----------|----------|
| 模型大小 | 1.5B / 7B / 14B | GPU 显存 & 调度压力 |
| LoRA Rank | 8 / 16 / 64 | 计算量与显存消耗 |
| Batch Size | 1 / 4 / 8 / 16 | GPU 利用率 & OOM 边界 |
| 数据集大小 | 100 / 1K / 10K 条 | 存储 I/O & 训练时长 |
| 并发微调任务 | 1 / 2 / 4 / 8 | GPU 资源争抢 |

确定性自适应行为（规则驱动，非 LLM）：
- 自动检测 GPU 显存不足并降低 batch size
- 根据模型大小推荐合理的 LoRA 配置
- 对比不同 `finetuning_type` (lora / qlora / full) 的资源消耗

#### 3.3.4 采集指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `finetune_gpu_util` | gauge | GPU 计算利用率 |
| `finetune_gpu_mem_used` | gauge | GPU 显存使用量 |
| `finetune_model_load_time` | histogram | 模型加载到 GPU 的时间 |
| `finetune_train_throughput` | gauge | 训练样本/秒 |
| `finetune_checkpoint_write_time` | histogram | Checkpoint 保存耗时 |
| `finetune_storage_io` | gauge | 数据集读取速率 (MB/s) |

---

### 3.4 Notebook Agent（Notebook 压测）

#### 3.4.1 目标

模拟多用户并发创建和使用 Notebook，测试 Pod 调度速度、Jupyter Kernel 响应能力、存储挂载性能。

#### 3.4.2 API 调用链

```
阶段 1: 创建 / 打开 Notebook
  POST /notebook_modelview/api/entry/jupyter
  Body: {
    "name": "load-nb-{uuid}",
    "images": "ccr.ccs.tencentyun.com/cube-studio/notebook:jupyter-ubuntu-cpu-base",
    "ide_type": "jupyter",
    "resource_memory": "4Gi",
    "resource_cpu": "2",
    "resource_gpu": "0",
    "volume_mount": "kubeflow-user-workspace(pvc):/mnt",
    "project_id": {project_id}
  }
  # 返回 Notebook 访问 URL

阶段 2: 等待 Pod 就绪
  GET /notebook_modelview/api/list/
  ?_filters=[{"col":"name","opr":"eq","value":"load-nb-{uuid}"}]
  # 轮询 expand.status 直到 Running

阶段 3: Kernel 操作模拟
  # 通过 Jupyter API 直接操作（绕过平台 API）
  # 基础 URL: http://{SERVICE_EXTERNAL_IP}:{NOTEBOOK_PORT}/notebook/jupyter/{name}/
  POST /api/kernels                    # 创建 Kernel
  POST /api/kernels/{id}/execute       # 执行代码
  GET  /api/kernels/{id}               # 获取状态

  # 模拟执行的代码片段
  Code Cell 1: import numpy as np; x = np.random.randn(10000, 10000)  # CPU 密集
  Code Cell 2: np.save('/mnt/test_data.npy', x)                       # 存储写入
  Code Cell 3: y = np.load('/mnt/test_data.npy')                      # 存储读取

阶段 4: 重置 / 停止
  POST /notebook_modelview/api/reset/{notebook_id}
  POST /notebook_modelview/api/stop/{notebook_id}

阶段 5: 清理
  DELETE /notebook_modelview/api/{notebook_id}
```

#### 3.4.3 IDE 类型覆盖

| IDE 类型 | 镜像 | 测试重点 |
|----------|------|----------|
| jupyter | notebook:jupyter-* | Kernel 执行、文件操作 |
| vscode | notebook:vscode-* | Terminal 操作、插件加载 |
| theia | notebook:theia-* | Web IDE 响应 |

#### 3.4.4 并发模式

- 并发创建数量：1 → 5 → 10 → 20 → 50
- 模拟持续使用（Kernel 保持活跃执行代码）
- 测试 idle timeout 与自动回收机制

#### 3.4.5 采集指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `notebook_create_latency` | histogram | 从 API 调用到 Pod Running 的延迟 |
| `notebook_kernel_start_time` | histogram | Kernel 启动延迟 |
| `notebook_code_exec_time` | histogram | 代码执行延迟 |
| `notebook_concurrent_count` | gauge | 同时运行的 Notebook 数 |
| `notebook_pvc_mount_time` | histogram | PVC 挂载延迟 |
| `notebook_memory_usage` | gauge | Notebook Pod 内存使用 |

---

## 4. Monitor Agent — 三层指标采集

### 4.1 架构

```
                      Monitor Agent
                          │
            ┌─────────────┼─────────────┐
            │             │             │
     ┌──────▼──────┐ ┌───▼────┐ ┌──────▼──────┐
     │ 应用层指标   │ │Prometheus│ │ 平台 API 层 │
     │ (Agent 内部) │ │ 系统指标  │ │   指标      │
     └─────────────┘ └────────┘ └─────────────┘
```

### 4.2 第一层：应用层指标（Agent 内部采集）

由各功能 Agent 在请求级别记录：

```python
class RequestMetrics:
    timestamp: float          # 请求发起时间
    method: str               # HTTP 方法
    url: str                  # 请求 URL
    status_code: int          # 响应状态码
    latency_ms: float         # 端到端延迟
    request_size_bytes: int   # 请求体大小
    response_size_bytes: int  # 响应体大小
    error_message: str        # 错误信息（如有）

    # 推理特有
    input_tokens: int
    output_tokens: int
    time_to_first_token_ms: float
    tokens_per_second: float
```

### 4.3 第二层：Prometheus 系统指标

通过 Prometheus API（`prometheus-k8s.monitoring:9090`）定期查询：

#### Pod 资源指标

```promql
# CPU 使用率
rate(container_cpu_usage_seconds_total{
  job="kubelet", container="", image="", pod=~".*{pod_pattern}.*"
}[2m])

# 内存使用
container_memory_working_set_bytes{
  job="kubelet", container="", image="", pod=~".*{pod_pattern}.*"
}
```

#### GPU 指标（DCGM Exporter）

```promql
# GPU 计算利用率
DCGM_FI_DEV_GPU_UTIL{pod=~".*{pod_pattern}.*"}

# GPU 显存利用率
DCGM_FI_DEV_FB_USED{pod!=""} / (
  DCGM_FI_DEV_FB_USED{pod!=""} + DCGM_FI_DEV_FB_FREE{pod!=""}
)

# 按节点聚合 GPU 利用率
sum by(instance, gpu) (DCGM_FI_DEV_GPU_UTIL)

# 按节点聚合 GPU 显存利用率
sum by(instance, gpu) (
  DCGM_FI_DEV_FB_USED / (DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE)
)
```

#### Istio 服务网格指标

```promql
# 推理服务 QPS
sum by (destination_workload) (
  istio_requests_total{
    destination_service_namespace="service",
    destination_workload=~"({service_name})"
  }
)

# 按状态码分布的请求速率
sum by (destination_workload, response_code) (
  irate(istio_requests_total{
    destination_service_namespace="service",
    destination_workload=~"({service_name})"
  }[5m])
)
```

#### 查询方式

```python
# 瞬时查询
GET http://prometheus-k8s.monitoring:9090/api/v1/query
  ?query={promql_expression}

# 范围查询
GET http://prometheus-k8s.monitoring:9090/api/v1/query_range
  ?query={promql_expression}
  &start={start_timestamp}
  &end={end_timestamp}
  &step=15s
```

### 4.4 第三层：平台 API 层指标

通过 Cube Studio REST API 采集平台状态：

```python
# 推理服务状态
GET /inferenceservice_modelview/api/
  ?_filters=[{"col":"model_status","opr":"eq","value":"online"}]
  &_select_columns=id,name,model_status,min_replicas,max_replicas,deploy_time

# 活跃 Pipeline 列表
GET /pipeline_modelview/api/
  ?_filters=[{"col":"run_id","opr":"ct","value":""}]
  &_order_column=created_on&_order_direction=desc

# Notebook 状态
GET /notebook_modelview/api/list/

# Grafana Dashboard 链接（用于报告嵌入）
# 服务: /grafana/d/istio-service/istio-service?var-namespace=service&var-service={name}
# Pod:  /grafana/d/pod-info/pod-info?var-pod={pod_name}
# GPU:  /grafana/d/dcgm/gpu
# 节点: /grafana/d/all-node/all-node
```

### 4.5 采集频率

| 层级 | 默认间隔 | 压测期间间隔 | 说明 |
|------|----------|-------------|------|
| 应用层 | 每请求 | 每请求 | 零采样损失 |
| Prometheus | 30s | 15s | 与 Prometheus scrape interval 对齐 |
| 平台 API | 60s | 30s | 避免对平台 API 产生额外负载 |

---

## 5. Bottleneck Analyzer Agent — 六层瓶颈分析

### 5.1 分析架构

```
请求链路:
  Client → Istio Gateway → Flask (Gunicorn) → SQLAlchemy → K8s API → Pod → 存储

六层分析:
  ┌─────────────────┐
  │ L1: API 网关层   │ Istio Ingress / VirtualService 路由
  ├─────────────────┤
  │ L2: Flask 后端层 │ Gunicorn Worker 饱和度
  ├─────────────────┤
  │ L3: 数据库层     │ 连接池耗尽、查询延迟
  ├─────────────────┤
  │ L4: K8s 调度层   │ Pod 调度延迟、资源不足
  ├─────────────────┤
  │ L5: 模型推理层   │ GPU 饱和、KV Cache 耗尽
  ├─────────────────┤
  │ L6: 存储 I/O 层  │ PVC 读写带宽瓶颈
  └─────────────────┘
```

### 5.2 各层分析规则

#### L1: API 网关 / Istio 层

| 指标 | 阈值 | 瓶颈判定 |
|------|------|----------|
| Istio 5xx 比例 | > 1% | 网关过载或后端不可达 |
| Istio 请求延迟 P99 | > 5s | 路由/负载均衡问题 |
| VirtualService 路由匹配失败 | > 0 | 配置错误 |
| 连接池耗尽（Envoy `cx_active` 达到上限） | — | 并发连接过多 |

#### L2: Flask 后端层

| 指标 | 阈值 | 瓶颈判定 |
|------|------|----------|
| Gunicorn Worker 活跃数 | = 20 (全部) | Worker 饱和，请求排队 |
| API 响应延迟 P95 | > 2s | 后端处理慢 |
| HTTP 503 频率 | > 0 | Worker 耗尽 |
| `before_request` 耗时 | > 100ms | 认证/权限检查慢 |

诊断方法：通过 Prometheus 查询 gunicorn 指标或发送并发 API 请求观察排队行为。

#### L3: 数据库层

| 指标 | 阈值 | 瓶颈判定 |
|------|------|----------|
| DB 活跃连接数 | > 300 (POOL_SIZE) | 连接池耗尽 |
| DB 连接等待时间 | > 1s | 连接争抢 |
| 慢查询（> 1s） | 频率增加 | 查询优化需要 |
| 连接溢出数 | 接近 800 (MAX_OVERFLOW) | 即将完全耗尽 |

诊断方法：
```sql
-- MySQL 连接状态
SHOW STATUS LIKE 'Threads_connected';
SHOW STATUS LIKE 'Threads_running';
SHOW PROCESSLIST;
```

#### L4: K8s 调度层

| 指标 | 阈值 | 瓶颈判定 |
|------|------|----------|
| Pod Pending 时间 | > 30s | 调度延迟 |
| Pod Pending 原因 | Insufficient cpu/memory/gpu | 资源不足 |
| 节点 CPU 利用率 | > 85% | 节点资源饱和 |
| 节点内存利用率 | > 90% | 节点内存压力 |
| PodDisruptionBudget 阻塞 | — | 缩容受限 |

诊断方法：
```promql
# 节点可分配资源
kube_node_status_allocatable{resource="cpu"}
kube_node_status_allocatable{resource="memory"}
kube_node_status_allocatable{resource="nvidia_com_gpu"}

# 已使用资源
sum by (node) (kube_pod_container_resource_requests{resource="cpu"})
sum by (node) (kube_pod_container_resource_requests{resource="memory"})
```

#### L5: 模型推理层

| 指标 | 阈值 | 瓶颈判定 |
|------|------|----------|
| GPU 利用率 | > 95% | GPU 计算饱和 |
| GPU 显存使用率 | > 95% | 显存即将 OOM |
| 推理延迟 P99 / P50 比值 | > 5 | 长尾延迟严重 |
| Token 吞吐下降 | 下降 > 30% | 性能退化 |
| 请求队列深度 | 持续增长 | 推理服务过载 |

确定性自适应行为（规则驱动，非 LLM）：
- 当检测到 GPU 饱和时，自动测试 HPA 扩容响应速度
- 分析 KV Cache 命中率与 token 长度的关系
- 对比不同 batch size 下的吞吐变化

#### L6: 存储 I/O 层

| 指标 | 阈值 | 瓶颈判定 |
|------|------|----------|
| PVC 读取带宽 | 接近存储网络上限 | 读取瓶颈 |
| PVC 写入带宽 | 接近存储网络上限 | 写入瓶颈 |
| IOPS | 接近设备上限 | 随机 I/O 瓶颈 |
| I/O 延迟 P99 | > 100ms | 存储响应慢 |

存储上下文参考：
- 存储集群 3 节点，每节点 7.68T ×12 TLC SSD
- 200G 数据网络专用于存储流量
- 25G 管理网络

诊断方法（在 Pod 内执行）：
```bash
# 顺序读测试
fio --name=seqread --rw=read --bs=1M --size=1G --numjobs=4 --time_based --runtime=30

# 顺序写测试
fio --name=seqwrite --rw=write --bs=1M --size=1G --numjobs=4 --time_based --runtime=30
```

### 5.3 确定性瓶颈检测 (阈值引擎)

在 LLM 分析之前，系统先通过**确定性阈值引擎**检测每层的瓶颈信号。阈值引擎是纯规则驱动的，不依赖 LLM：

```python
class ThresholdEngine:
    """确定性六层瓶颈检测器"""

    def evaluate(self, metrics: MetricsSnapshot,
                 config: BottleneckConfig) -> list[BottleneckSignal]:
        """对每层阈值做比较，返回触发的瓶颈信号列表"""
        signals = []
        # L1: API 网关
        if metrics.istio_5xx_rate > config.l1.error_rate_pct / 100:
            signals.append(BottleneckSignal(layer="L1", severity="critical", ...))
        # L2: Flask 后端
        if metrics.gunicorn_active_workers >= config.l2.worker_count * 0.9:
            signals.append(BottleneckSignal(layer="L2", severity="warning", ...))
        # L3 ~ L6: 类似...
        return signals

@dataclass
class BottleneckSignal:
    layer: str                  # L1 ~ L6
    severity: Literal["info", "warning", "critical"]
    metric_name: str
    current_value: float
    threshold_value: float
    description: str
```

阈值引擎的输出作为**结构化输入**传递给 Bottleneck Analyzer (LLM)。

### 5.4 LLM 瓶颈分析 (Bottleneck Analyzer)

> **Bottleneck Analyzer 是系统中唯一使用 LLM (MiniMax-2.1) 的组件。**
> 它是只读分析器：接收阈值引擎输出 + 原始指标数据，通过 LLM 推理生成跨层关联分析。

#### 5.4.1 架构：结构化上下文 → LLM 推理 → 结构化输出

```
┌──────────────────────────────────────────────┐
│            结构化上下文组装                     │
│                                              │
│  阈值引擎信号 ─────┐                          │
│  请求级指标 ───────┼──→ AnalysisPrompt ─────→│──→ MiniMax-2.1 API
│  Prometheus 指标 ──┤    (Jinja2 模板渲染)     │      │
│  系统容量参数 ─────┘                          │      │
│                                              │      ▼
│                                              │  结构化 JSON 响应
│                                              │      │
│                                              │      ▼
│                         BottleneckReport     │◄─ Pydantic 解析校验
│                         · bottlenecks[]      │
│                         · root_cause         │
│                         · capacity_prediction│
│                         · recommendations[]  │
└──────────────────────────────────────────────┘
```

**不使用 LLM Agent 循环（ReAct/tool-use）**，而是单次结构化 prompt → 结构化输出。原因同 Fault Injector 的 Diagnosis Agent。

#### 5.4.2 Prompt 工程

```python
class BottleneckAnalyzer:
    """唯一的 LLM 组件：瓶颈关联分析器"""

    SYSTEM_PROMPT = """
你是 Cube Studio 平台的性能分析专家。你将收到一次负载测试的完整数据，
包括六层瓶颈信号、请求级指标、系统级指标和已知系统容量参数。

你的任务：
1. 跨层关联分析：识别哪些瓶颈信号之间存在因果关系
2. 根因推断：从多个关联信号推断根本原因，区分表面症状与真实瓶颈
3. 容量预测：基于当前趋势预测系统极限（并发数、QPS、Pod 数等）
4. 优化建议：给出优先级排序的具体可操作建议
5. 历史对比（如有历史数据）：标注改善和退化项

已知系统容量参数:
- Gunicorn Workers: {workers}
- DB Pool: {pool_size} + {max_overflow} overflow
- GPU: {gpu_count} × {gpu_type}
- Celery rate limits: upgrade_service 1/s, delete_workflow 1/h

你必须以指定的 JSON 格式输出。不要输出 JSON 以外的内容。
"""

    async def analyze(self, context: AnalysisContext) -> BottleneckReport:
        # 1. 组装结构化上下文
        user_prompt = ANALYSIS_TEMPLATE.render(
            threshold_signals=context.signals,
            request_metrics=context.request_metrics_summary,
            prometheus_metrics=context.prometheus_summary,
            stages_results=context.per_stage_results,
            system_capacity=context.system_capacity,
            historical=context.previous_reports,  # 可选
        )

        # 2. 调用 LLM (单次)
        response = await self.llm_client.chat_completion(
            model=self.config.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=self.config.max_tokens,
            response_format={"type": "json_object"},
        )

        # 3. Pydantic 解析校验
        try:
            report = BottleneckReport.model_validate_json(response.content)
        except ValidationError:
            logger.warning("LLM output validation failed, using threshold-only report")
            report = self._fallback_threshold_report(context)

        return report
```

#### 5.4.3 瓶颈报告输出结构

```python
class BottleneckItem(BaseModel):
    layer: str                      # L1 ~ L6
    severity: str                   # info | warning | critical
    description: str                # 自然语言描述
    evidence: list[str]             # 支撑证据 (指标名 + 值)
    caused_by: str | None           # 上游因（如果是级联影响）

class CapacityPrediction(BaseModel):
    dimension: str                  # e.g. "concurrent_inference_requests"
    current_value: float
    estimated_limit: float
    limiting_factor: str            # e.g. "Gunicorn Worker count (20)"
    confidence: float               # 0.0 ~ 1.0

class Recommendation(BaseModel):
    priority: str                   # high | medium | low
    category: str                   # scaling | config | architecture | code
    description: str
    expected_improvement: str

class BottleneckReport(BaseModel):
    bottlenecks: list[BottleneckItem]
    root_cause: str                          # 主要瓶颈根因
    root_cause_layer: str                    # 根因层级
    capacity_predictions: list[CapacityPrediction]
    recommendations: list[Recommendation]
    overall_assessment: str                  # 整体评估 (1-2 句)
    historical_comparison: dict | None       # 与历史报告对比 (可选)
```

#### 5.4.4 LLM 智能分析能力

Bottleneck Analyzer 基于 MiniMax-2.1 提供以下智能分析（全部通过单次 prompt 实现）：

1. **跨层关联分析**：识别因果链，例如 API 延迟升高(L2) ← DB 连接池紧张(L3) ← 大量 Pipeline 提交
2. **根因推断**：从多个关联信号推断根本原因，而非表面症状
3. **容量预测**：基于当前增长趋势预测何时达到系统极限
4. **优化建议**：根据瓶颈类型给出优先级排序的具体可执行建议
5. **历史对比**：保存历次测试结果，自动标注改善和退化项

#### 5.4.5 降级策略

当 LLM 不可用或输出不合规时，系统自动降级到**阈值报告**：

| 功能 | LLM 可用 | LLM 不可用 (降级) |
|------|----------|-------------------|
| 瓶颈识别 | 跨层关联 + 因果推断 | 仅阈值引擎信号列表 |
| 根因 | 智能推断 | "最高严重度层 = 根因" (简单规则) |
| 容量预测 | 趋势分析 + 上下文推理 | 基于当前值/阈值的线性外推 |
| 建议 | 具体可操作 | 通用建议模板 (per-layer) |
| 历史对比 | 智能对比分析 | 数值差值列表 |

---

## 6. 配置 Schema

### 6.1 完整 YAML 配置

```yaml
# load-simulator-config.yaml

# 全局配置
global:
  cube_studio_url: "http://cube-studio.example.com"
  auth:
    method: "jwt"                    # jwt | username
    username: "admin"
    jwt_password: "${JWT_SECRET}"     # JWT 签名密钥（从环境变量读取）
  prometheus_url: "http://prometheus-k8s.monitoring:9090"
  project_id: 1                      # Cube Studio 项目 ID
  report_output: "./reports/"
  log_level: "INFO"                  # DEBUG | INFO | WARNING | ERROR

# 编排引擎配置
orchestrator:
  max_parallel_agents: 4              # 同时运行的最大 Agent 数
  observe_interval: 30                # 每 30 秒采集指标快照
  session_dir: "./reports/sessions/"  # Session 状态持久化目录
  resource_cleanup:
    enabled: true                     # 测试结束后自动清理创建的资源
    cleanup_on_failure: true          # 测试失败时也清理

# Channel 连接配置
channels:
  cube_studio:
    timeout: 30                       # API 请求超时 (秒)
    retry_count: 3                    # 失败重试次数
    retry_backoff: 1.0                # 重试退避基数 (秒)
  inference:
    max_connections: 200              # 推理端点最大并发连接数
    max_keepalive: 200                # 长连接保持数
    timeout: 60                       # 推理请求超时 (秒)
  prometheus:
    timeout: 15                       # PromQL 查询超时 (秒)

# Bottleneck Analyzer (唯一 LLM 组件) 配置
bottleneck_analyzer:
  enabled: true
  llm:
    provider: "minimax"               # minimax | openai-compatible
    model: "minimax-2.1"
    api_base: "https://api.minimax.chat/v1"
    api_key_env: "MINIMAX_API_KEY"    # 从环境变量读取 API Key
    max_tokens: 8192                  # 分析报告最大 token 数
    temperature: 0.3                  # 低温度确保分析稳定性

# 监控配置
monitor:
  prometheus_scrape_interval: 15     # 秒
  platform_api_interval: 30          # 秒
  metrics_retention: 3600            # 指标保留时长（秒）

# 推理服务负载配置
inference:
  enabled: true
  services:
    - name: "vllm-load-test"
      service_type: "vllm"
      model_name: "deepseek-r1-7b"
      model_path: "/mnt/admin/models/DeepSeek-R1-Distill-Qwen-7B/"
      images: "ccr.ccs.tencentyun.com/cube-studio/vllm:latest"
      resource:
        cpu: "4"
        memory: "16Gi"
        gpu: "1"
      replicas:
        min: 1
        max: 4
  load:
    ramp_up:
      type: "step"                   # step | linear | spike
      stages:
        - concurrency: 1
          duration: 30
        - concurrency: 10
          duration: 120
        - concurrency: 50
          duration: 120
        - concurrency: 100
          duration: 120
        - concurrency: 200
          duration: 60
    token_distribution:
      mode: "mixed"                  # short | medium | long | mixed
      mixed_weights:
        short: 0.3                   # 10-50 tokens
        medium: 0.5                  # 100-500 tokens
        long: 0.2                    # 500-2000 tokens
    request:
      stream: true
      timeout: 60                    # 单请求超时（秒）
      think_time: 0                  # 请求间隔（秒），0 = 无间隔

# 训练 Pipeline 负载配置
pipeline:
  enabled: true
  templates:
    - name: "pytorch-training"
      job_template: "PyTorch"
      dag_type: "fan-out"            # linear | fan-out | fan-in | diamond | complex
      task_count: 4
      task_resource:
        cpu: "4"
        memory: "8Gi"
        gpu: "1"
      storage_io_test:
        enabled: true
        read_size_mb: 1024           # 模拟读取量
        write_size_mb: 512           # 模拟写入量
  load:
    concurrent_pipelines:
      type: "step"
      stages:
        - count: 1
          duration: 120
        - count: 5
          duration: 180
        - count: 10
          duration: 180
        - count: 20
          duration: 120

# 微调负载配置
finetune:
  enabled: true
  tasks:
    - name: "deepseek-lora"
      job_template: "LLaMA-Factory"
      model_path: "/mnt/admin/models/DeepSeek-R1-Distill-Qwen-7B/"
      dataset_path: "/mnt/admin/datasets/identity.json"
      finetuning_type: "lora"
      lora_target: "all"
      template: "deepseek3"
      hyperparams:
        per_device_train_batch_size: 4
        gradient_accumulation_steps: 4
        lr_scheduler_type: "cosine"
        num_train_epochs: 3
        learning_rate: 5e-5
      resource:
        cpu: "8"
        memory: "32Gi"
        gpu: "1"
  load:
    concurrent_tasks:
      type: "step"
      stages:
        - count: 1
          duration: 300
        - count: 2
          duration: 300
        - count: 4
          duration: 300

# Notebook 负载配置
notebook:
  enabled: true
  instances:
    - name_prefix: "load-nb"
      ide_type: "jupyter"
      images: "ccr.ccs.tencentyun.com/cube-studio/notebook:jupyter-ubuntu-cpu-base"
      resource:
        cpu: "2"
        memory: "4Gi"
        gpu: "0"
      simulate_usage: true           # 是否模拟 Kernel 执行代码
      code_cells:                    # 模拟执行的代码
        - "import numpy as np; x = np.random.randn(5000, 5000)"
        - "np.save('/mnt/test.npy', x)"
        - "y = np.load('/mnt/test.npy')"
  load:
    concurrent_notebooks:
      type: "step"
      stages:
        - count: 1
          duration: 120
        - count: 5
          duration: 180
        - count: 10
          duration: 180
        - count: 20
          duration: 120

# 瓶颈分析配置
bottleneck_analysis:
  enabled: true
  layers:
    - name: "api_gateway"
      enabled: true
    - name: "flask_backend"
      enabled: true
      thresholds:
        worker_saturation_pct: 90    # Worker 使用率告警阈值
        api_latency_p95_ms: 2000
    - name: "database"
      enabled: true
      thresholds:
        pool_usage_pct: 80           # 连接池使用率告警阈值
        slow_query_ms: 1000
    - name: "k8s_scheduling"
      enabled: true
      thresholds:
        pod_pending_sec: 30
        node_cpu_pct: 85
        node_memory_pct: 90
    - name: "model_inference"
      enabled: true
      thresholds:
        gpu_util_pct: 95
        gpu_mem_pct: 95
        latency_tail_ratio: 5       # P99/P50 比值
    - name: "storage_io"
      enabled: true
      thresholds:
        io_latency_p99_ms: 100
```

### 6.2 快速启动模板

```yaml
# quick-start-inference-only.yaml
# 仅推理压测 — 快速验证
global:
  cube_studio_url: "http://localhost"
  auth:
    method: "username"
    username: "admin"
  prometheus_url: "http://prometheus-k8s.monitoring:9090"
  project_id: 1

inference:
  enabled: true
  services:
    - name: "quick-test"
      service_type: "vllm"
      model_name: "test-model"
      model_path: "/mnt/admin/models/test/"
      images: "ccr.ccs.tencentyun.com/cube-studio/vllm:latest"
      resource:
        cpu: "4"
        memory: "16Gi"
        gpu: "1"
      replicas:
        min: 1
        max: 1
  load:
    ramp_up:
      type: "step"
      stages:
        - concurrency: 1
          duration: 30
        - concurrency: 5
          duration: 60
    token_distribution:
      mode: "short"
    request:
      stream: false
      timeout: 30

pipeline:
  enabled: false
finetune:
  enabled: false
notebook:
  enabled: false
bottleneck_analysis:
  enabled: true
```

---

## 7. 执行模式

### 7.1 单组件模式 (Single Component)

仅启用一个功能 Agent，用于定向压测某一模块。

```bash
# 仅推理
load-simulator --config config.yaml --only inference

# 仅训练
load-simulator --config config.yaml --only pipeline

# 仅微调
load-simulator --config config.yaml --only finetune

# 仅 Notebook
load-simulator --config config.yaml --only notebook
```

### 7.2 混合负载模式 (Mixed Load)

同时启用多个功能 Agent，模拟真实生产环境下多业务并行运行。

```bash
# 全部启用（按配置文件 enabled 字段）
load-simulator --config config.yaml

# 指定组合
load-simulator --config config.yaml --only inference,pipeline,notebook
```

Orchestrator (确定性调度) 负责：
- 按顺序或并行启动各功能 Agent
- 监控各 Agent 的资源消耗，避免测试本身成为瓶颈
- 协调负载叠加时序（例如先启动推理服务，稳定后再叠加训练负载）
- 通过 `AdaptiveRules` 做确定性自适应调整（见 9.1）

### 7.3 压力测试模式 (Stress Test)

目标：找到系统的崩溃点。

```bash
load-simulator --config config.yaml --mode stress
```

Stress 模式行为（全部由确定性 `AdaptiveRules` 驱动）：
1. 从最低并发开始，持续线性增加
2. 每次增加后等待指标稳定（30s）
3. 当 `AdaptiveRules` 检测到以下信号时记录 "breaking point"：
   - 错误率 > 5%
   - P99 延迟 > 基线的 10 倍
   - HTTP 503/502 频率突增
   - Pod OOMKilled
4. 在 breaking point 附近做精细化二分探测（确定性二分搜索）
5. 测试结束后由 Bottleneck Analyzer (LLM) 生成极限参数报告

### 7.4 持久测试模式 (Soak Test)

目标：发现内存泄漏、连接泄漏、资源碎片化等长时间运行才暴露的问题。

```bash
load-simulator --config config.yaml --mode soak --duration 24h
```

Soak 模式行为：
1. 维持中等负载（约 50% 压力水平）持续运行
2. 每 15 分钟记录一次完整指标快照
3. 分析指标趋势：
   - 内存是否持续增长（泄漏检测）
   - DB 连接数是否持续增加（连接泄漏）
   - 响应延迟是否逐步退化
   - GPU 显存碎片化程度

---

## 8. 报告生成

### 8.1 报告结构

```
reports/sessions/{session_id}/
├── session.json                          # Session 元数据 (阶段、状态、资源清单)
├── events.jsonl                          # 事件时间线 (append-only)
├── metrics/                              # 指标快照 (按 observe_interval)
│   ├── {timestamp}.json
│   └── ...
├── request_logs/                         # 请求级日志 (可选)
│   ├── inference.jsonl
│   ├── pipeline.jsonl
│   └── notebook.jsonl
└── report/
    ├── report.html                       # 主报告 (Jinja2 渲染)
    ├── report.json                       # 机器可读的 BottleneckReport
    ├── metrics-raw.csv                   # 原始指标数据
    └── charts/
        ├── latency-timeline.html         # 延迟时间线 (plotly 交互图)
        ├── throughput-timeline.html       # 吞吐时间线
        ├── error-rate-timeline.html       # 错误率时间线
        ├── resource-utilization.html      # 资源利用率 (CPU/MEM/GPU)
        ├── bottleneck-heatmap.html        # 六层瓶颈热力图
        └── stage-comparison.html          # 各阶段指标对比
```

### 8.2 报告内容

#### 执行概要

```
┌────────────────────────────────────────────────┐
│              Load Test Summary                  │
├────────────────────────────────────────────────┤
│ 测试时间:    2026-02-13 10:00 ~ 12:30          │
│ 测试模式:    Mixed Load                         │
│ 持续时长:    2h 30m                             │
│ 总请求数:    152,384                            │
│ 成功率:      99.2%                              │
│ 峰值并发:    100                                │
├────────────────────────────────────────────────┤
│ 组件         │ 状态   │ 峰值吞吐   │ P99 延迟   │
│──────────────│────────│────────────│───────────│
│ Inference    │ ✓ PASS │ 850 req/s  │ 2.3s      │
│ Pipeline     │ ✓ PASS │ 12 run/min │ 45s       │
│ FineTune     │ ⚠ WARN │ 3 task/h   │ N/A       │
│ Notebook     │ ✓ PASS │ 8 create/m │ 35s       │
└────────────────────────────────────────────────┘
```

#### 瓶颈分析摘要

```
┌────────────────────────────────────────────────┐
│           Bottleneck Analysis                   │
├────────────────────────────────────────────────┤
│ ⚠ L2 Flask 后端层:                              │
│   - Gunicorn Worker 在并发 80 时达到饱和 (20/20) │
│   - 建议: 增加 Worker 数量或切换到异步框架        │
│                                                 │
│ ⚠ L3 数据库层:                                   │
│   - 连接池在混合负载下使用率达到 78% (234/300)     │
│   - 慢查询集中在 pipeline list 接口               │
│   - 建议: 优化查询索引，考虑读写分离              │
│                                                 │
│ ✓ L1 API 网关层: 正常                            │
│ ✓ L4 K8s 调度层: 正常                            │
│ ⚠ L5 模型推理层:                                 │
│   - GPU 利用率峰值 97%，显存使用 89%              │
│   - 长 token (>1000) 请求导致 P99 尾延迟 > 8s    │
│ ✓ L6 存储 I/O 层: 正常                           │
└────────────────────────────────────────────────┘
```

#### 详细指标表

对每个组件输出：
- 延迟分布：P50 / P75 / P90 / P95 / P99 / Max
- 吞吐曲线：随时间变化的 RPS/TPS
- 错误分类：按 HTTP 状态码、错误类型分组统计
- 资源利用率：CPU、内存、GPU 使用率的时间序列

#### LLM 瓶颈分析洞察

Bottleneck Analyzer (MiniMax-2.1) 生成的结构化分析（测试结束后单次调用）：

```
[智能洞察]

1. 系统整体表现: Cube Studio 在混合负载模式下整体稳定，但 Flask 后端
   和 GPU 推理层存在可见瓶颈。

2. 关键发现:
   - Flask Worker 是当前最大瓶颈。20 个 Gevent Worker 在并发 80 时全部
     饱和，之后的请求延迟呈指数级增长。这是一个架构级限制。
   - DB 连接池使用率在混合负载时偏高（78%），但尚未成为阻塞点。
     如果同时运行更多训练 Pipeline，预计会首先触发连接池耗尽。
   - 推理服务在长 token 场景下表现出明显的尾延迟问题，P99/P50 比值
     达到 6.2，超过阈值 5。建议启用推理请求优先级队列。

3. 极限预测:
   - 按当前配置，系统可支撑: ~80 并发推理请求 + 10 个并行 Pipeline +
     15 个活跃 Notebook
   - 主要约束因素: Gunicorn Worker 数量 (20) 和 DB 连接池 (300)

4. 优化建议:
   a. [高优先级] 增加 Gunicorn Worker 至 40-60，或评估 ASGI 迁移
   b. [中优先级] 为高频读接口增加 Redis 缓存层
   c. [中优先级] 推理服务启用请求队列和优先级调度
   d. [低优先级] DB 连接池增加到 500，MAX_OVERFLOW 增加到 1200
```

---

## 9. 自适应与智能增强

### 9.1 确定性自适应行为 (Orchestrator 内置)

以下自适应行为由 Orchestrator 的确定性规则引擎实现，**不依赖 LLM**：

```python
class AdaptiveRules:
    """基于阈值的确定性自适应规则"""

    async def evaluate(self, metrics: MetricsSnapshot,
                       current_stage: StageConfig) -> AdaptiveAction:
        # 规则 1: 错误率过高 → 暂停升级并发
        if metrics.error_rate > 0.05:
            return AdaptiveAction.PAUSE_RAMP_UP

        # 规则 2: 错误率持续高 → 记录 breaking point 并回退
        if metrics.error_rate > 0.10:
            return AdaptiveAction.RECORD_BREAKING_POINT

        # 规则 3: P99 延迟超过基线 10x → 记录 breaking point
        if metrics.p99_latency > self.baseline_p99 * 10:
            return AdaptiveAction.RECORD_BREAKING_POINT

        # 规则 4: OOMKilled 事件 → 降低资源请求或并发
        if metrics.oom_killed_count > 0:
            return AdaptiveAction.REDUCE_LOAD

        # 规则 5: GPU 显存 > 95% → 降低推理并发
        if metrics.gpu_mem_util > 0.95:
            return AdaptiveAction.REDUCE_INFERENCE_CONCURRENCY

        return AdaptiveAction.CONTINUE
```

自适应行为日志示例：

```
观测: API P95 延迟突然升高 200%
规则: p95_latency > baseline * 3 → PAUSE_RAMP_UP
行动: 暂停增加并发 → 等待 30s → 如指标恢复则继续

观测: GPU 显存使用率达到 96%
规则: gpu_mem_util > 0.95 → REDUCE_INFERENCE_CONCURRENCY
行动: 降低推理并发 50% → 记录当前吞吐为安全上限

观测: Pipeline Pod 持续 Pending > 2 分钟
规则: pod_pending_time > 120s → RECORD_SCHEDULING_LIMIT
行动: 记录当前并行 Pipeline 数为调度极限 → 不再增加 Pipeline 并发
```

### 9.2 LLM 增强分析 (Bottleneck Analyzer 提供)

以下分析由 Bottleneck Analyzer (MiniMax-2.1) 在测试**结束后**执行，不影响测试过程：

1. **跨层因果推断**：确定性规则只能检测单层阈值，LLM 可以推断 "L2 Worker 饱和 ← L3 DB 慢查询阻塞了 Worker" 这种跨层因果
2. **容量趋势预测**：基于多阶段指标变化趋势预测系统极限
3. **历史对比分析**：保存历次测试结果的 `BottleneckReport`，LLM 自动对比标注改善/退化

```
[版本对比: v2026.01.01 vs v2026.02.01]

改善:
  - 推理 P99 延迟: 2.8s → 2.3s (↓18%)
  - Pipeline 调度延迟: 12s → 8s (↓33%)

退化:
  - DB 连接池峰值使用率: 65% → 78% (↑20%)
  - Notebook 启动延迟: 28s → 35s (↑25%)

新发现:
  - 新版本在并发 > 120 时出现间歇性 502 错误（旧版本未观察到）
```

4. **优化建议排序**：结合系统架构上下文，给出优先级排序的具体建议

### 9.3 异常场景探索 (Stress 模式内置)

以下探索行为在 Stress 模式下由确定性 Orchestrator 自动执行：

- **Breaking point 二分搜索**：在首次检测到 breaking point 后，在 `[上一稳定点, breaking point]` 区间做二分搜索精确定位
- **资源竞争测试**：同时创建多个 GPU 任务，观察调度公平性
- **混合负载叠加**：先启动推理服务稳定后，逐步叠加 Pipeline/Notebook 负载
- **存储 I/O 叠加**：训练任务写 checkpoint 时叠加推理服务的模型加载

---

## 10. 运行方式

### 10.1 命令行接口

```bash
# 标准运行
load-simulator --config config.yaml

# 指定模式
load-simulator --config config.yaml --mode stress|soak|mixed|single

# 仅运行指定组件
load-simulator --config config.yaml --only inference,pipeline

# 干运行（验证配置 + API 连通性检查，不实际创建资源）
load-simulator --config config.yaml --dry-run

# 指定输出目录
load-simulator --config config.yaml --report-dir ./my-reports/

# 恢复中断的测试
load-simulator --resume {session_id}
```

### 10.2 Orchestrator 引擎核心逻辑

```python
class OrchestrationEngine:
    """确定性编排引擎"""

    async def run(self, config: LoadSimulatorConfig) -> SessionResult:
        session = Session.create(config)
        channels = await self._init_channels(config)

        try:
            # Phase 1: 连通性预检
            await self._preflight_check(channels, config)

            # Phase 2: 资源创建 (推理服务部署、Pipeline 创建等)
            agents = self._create_agents(config, channels)
            for agent in agents:
                await agent.setup()
                session.created_resources.extend(agent.created_resources)
                session.save()

            # Phase 3: 负载执行 (阶梯式并发递增)
            monitor_task = asyncio.create_task(
                self.monitor.continuous_collect(config.monitor))
            adaptive = AdaptiveRules(baseline=session.baseline)

            for stage_idx, stage in enumerate(self._resolve_stages(config)):
                session.phase = f"stage_{stage_idx}"
                session.save()

                # 各 Agent 并行执行当前阶段
                stage_results = await asyncio.gather(*[
                    agent.run_stage(stage) for agent in agents
                ])

                # 确定性自适应检查
                metrics = await self.monitor.snapshot()
                action = await adaptive.evaluate(metrics, stage)
                session.events.append(Event(stage=stage_idx, metrics=metrics,
                                            action=action))

                if action == AdaptiveAction.RECORD_BREAKING_POINT:
                    session.breaking_point = stage
                    if config.mode == "stress":
                        break  # Stress 模式在 breaking point 停止
                elif action == AdaptiveAction.REDUCE_LOAD:
                    # 降低下一阶段并发
                    continue

            # 取消监控任务并等待退出 + flush 指标缓存
            monitor_task.cancel()
            try:
                await monitor_task  # 等待任务实际退出
            except asyncio.CancelledError:
                pass
            await session.flush_metrics()  # 确保最后一批指标写入磁盘

            # Phase 4: 瓶颈分析 (LLM)
            if config.bottleneck_analyzer.enabled:
                threshold_signals = self.threshold_engine.evaluate(
                    session.all_metrics, config.bottleneck_analysis)
                analysis = await self.bottleneck_analyzer.analyze(
                    AnalysisContext(
                        signals=threshold_signals,
                        request_metrics=session.request_metrics_summary,
                        prometheus_summary=session.prometheus_summary,
                        per_stage_results=session.stage_results,
                        system_capacity=config.system_capacity,
                    ))
                session.bottleneck_report = analysis

            # Phase 5: 报告生成
            report = self.reporter.generate(session)
            session.status = "completed"

        except Exception as e:
            session.status = "failed"
            raise
        finally:
            # 资源清理 (总是执行)
            if config.orchestrator.resource_cleanup.enabled:
                await self._cleanup_resources(session, agents)
            session.save()

        return session
```

### 10.3 输出示例

```
[10:00:00] Orchestrator: 解析配置完成，启用组件: inference, pipeline, notebook
[10:00:01] Orchestrator: 连通性预检 — Cube Studio API ✓, Prometheus ✓
[10:00:02] Inference Agent: 创建推理服务 vllm-load-test...
[10:00:15] Inference Agent: 服务部署中，等待 Pod 就绪...
[10:01:02] Inference Agent: 服务就绪，开始压测 (阶段 1: 并发 1)
[10:01:02] Pipeline Agent: 创建训练 Pipeline pytorch-training...
[10:01:05] Notebook Agent: 创建 Notebook load-nb-001...
[10:01:32] Inference Agent: 阶段 1 完成 — 基线 P50=120ms, P99=350ms
[10:01:32] Inference Agent: 进入阶段 2 (并发 10)
[10:02:00] Monitor Agent: 系统指标 — CPU: 32%, MEM: 45%, GPU: 68%
[10:03:32] Inference Agent: 阶段 2 完成 — P50=180ms, P99=890ms
[10:03:32] Inference Agent: 进入阶段 3 (并发 50)
[10:04:00] Monitor Agent: ⚠ Gunicorn Worker 使用率 75% (15/20)
[10:05:00] Threshold Engine: L2 预警 — Worker 饱和度 75% 接近阈值 90% (approaching)
[10:05:32] Inference Agent: 阶段 3 完成 — P50=450ms, P99=2.8s
[10:05:33] Adaptive Rules: 错误率 0.8% < 5%, 继续
[10:05:33] Inference Agent: 进入阶段 4 (并发 100)
[10:06:00] Threshold Engine: L2 告警 — Worker 饱和度 95% > 阈值 90%
[10:06:30] Adaptive Rules: 错误率 7.2% > 5% → RECORD_BREAKING_POINT (并发 ~100)
...
[12:30:00] Orchestrator: 测试完成，开始瓶颈分析...
[12:30:05] Bottleneck Analyzer: 调用 MiniMax-2.1 分析...
[12:30:12] Bottleneck Analyzer: 分析完成，根因: L2 Flask Worker 饱和
[12:30:13] Reporter: 生成 HTML 报告...
[12:30:15] Reporter: 已保存至 ./reports/sessions/{session_id}/report/report.html
[12:30:16] Orchestrator: 清理测试资源 — 删除推理服务、Pipeline、Notebook...
[12:30:25] Orchestrator: 清理完成
```

---

## 附录 A: Cube Studio API 速查

### 认证

```
Header: Authorization: <JWT_TOKEN>
JWT Payload: {"iss": "cube-studio", "sub": "<username>"}
JWT Algorithm: HS256
JWT Secret: ${JWT_SECRET}  # 从环境变量读取，禁止使用默认弱密钥
```

### 通用查询参数

```
?_filters=[{"col":"field","opr":"eq","value":"val"}]
&_page_index=0
&_page_size=20
&_order_column=created_on
&_order_direction=desc
&_select_columns=id,name,describe
```

### 通用响应格式

```json
{
  "status": 0,
  "message": "success",
  "result": { ... },
  "count": 10
}
```

### 端点汇总

| 功能 | 端点 | 关键操作 |
|------|------|----------|
| 推理服务 | `/inferenceservice_modelview/api/` | CRUD, deploy/{debug,test,prod}/{id}, clear/{id} |
| Pipeline | `/pipeline_modelview/api/` | CRUD, run_pipeline/{id}, copy_pipeline/{id} |
| Task | `/task_modelview/api/` | CRUD |
| Notebook | `/notebook_modelview/api/` | entry/jupyter, reset/{id}, stop/{id}, list/ |
| 用户 | `/users/api/` | 用户管理 |

### K8s 命名空间

| 命名空间 | 用途 |
|----------|------|
| `pipeline` | 训练流水线 |
| `service` | 推理服务 |
| `jupyter` | Notebook |
| `automl` | 超参数调优 |
| `aihub` | AI Hub |

### Celery 任务速率限制

| 任务 | 速率 | 软超时 |
|------|------|--------|
| `upgrade_service` | 1/s | 3600s |
| `delete_workflow` | 1/h | 600s |
| `upload_workflow` | 10/s | 600s |
| `check_docker_commit` | 1/s | 600s |
| `get_k8s_resource` | 1/s | 300s |

---

## 附录 B: Prometheus PromQL 速查

```promql
# Pod CPU 使用率
rate(container_cpu_usage_seconds_total{job="kubelet",container="",image="",pod=~".*{name}.*"}[2m])

# Pod 内存使用
container_memory_working_set_bytes{job="kubelet",container="",image="",pod=~".*{name}.*"}

# GPU 利用率
DCGM_FI_DEV_GPU_UTIL{pod=~".*{name}.*"}

# GPU 显存使用率
DCGM_FI_DEV_FB_USED{pod!=""} / (DCGM_FI_DEV_FB_USED{pod!=""} + DCGM_FI_DEV_FB_FREE{pod!=""})

# Istio QPS
sum by(destination_workload)(istio_requests_total{destination_service_namespace="service",destination_workload=~"({name})"})

# Istio 响应码分布
sum by(destination_workload,response_code)(irate(istio_requests_total{destination_service_namespace="service",destination_workload=~"({name})"}[5m]))

# 节点可分配资源
kube_node_status_allocatable{resource="cpu"}
kube_node_status_allocatable{resource="memory"}
kube_node_status_allocatable{resource="nvidia_com_gpu"}

# 节点已请求资源
sum by(node)(kube_pod_container_resource_requests{resource="cpu"})
sum by(node)(kube_pod_container_resource_requests{resource="memory"})
```

---

## 附录 C: Grafana Dashboard 链接

| Dashboard | URL Pattern |
|-----------|-------------|
| Pod 指标 | `/grafana/d/pod-info/pod-info?var-pod={pod_name}` |
| 服务指标 | `/grafana/d/istio-service/istio-service?var-namespace=service&var-service={service_name}` |
| 集群概览 | `/grafana/d/all-node/all-node?var-org={org}` |
| 节点详情 | `/grafana/d/node/node?var-node={node_name}` |
| GPU 监控 | `/grafana/d/dcgm/gpu` |
