# AIDC Auto-SRE 架构文档

本文档详细说明 `fault_injector`（故障注入器）和 `load_simulator`（负载模拟器）两个核心模块的架构设计和函数调用关系。

---

## 目录

1. [Fault Injector 架构](#1-fault-injector-架构)
2. [Load Simulator 架构](#2-load-simulator-架构)
3. [共享组件](#3-共享组件)
4. [典型调用流程](#4-典型调用流程)

---

## 1. Fault Injector 架构

### 1.1 模块概览

Fault Injector 是一个多维度的故障注入系统，用于 Cube Studio 的弹性测试。支持硬件、OS、平台和服务层的故障注入。

```
fault_injector/
├── cli.py                 # CLI 入口
├── config/                # 配置加载和 Schema
│   ├── loader.py          # YAML 配置加载
│   ├── schema.py          # Pydantic 数据模型
│   └── defaults.py        # 默认配置
├── orchestrator/          # 编排引擎
│   ├── engine.py          # 主编排器
│   ├── session.py         # 会话管理
│   ├── scheduler.py       # 场景调度
│   └── watchdog.py        # 超时看门狗
├── agents/                # 层级代理
│   ├── base.py            # 代理基类
│   ├── hardware.py        # 硬件层代理
│   ├── os_fault.py        # OS 层代理
│   ├── platform.py        # 平台层代理
│   └── service.py         # 服务层代理
├── scenarios/             # 故障场景
│   ├── base.py            # 场景基类
│   ├── registry.py        # 场景注册表
│   └── rdma_anomaly.py    # RDMA 异常场景
├── safety/                # 安全机制
│   ├── guard.py           # 安全守卫
│   └── rollback.py        # 回滚日志
└── channels/              # 通信通道 (委托到 lib/channels/)
```

### 1.2 核心组件关系

```
┌─────────────────────────────────────────────────────────────────────┐
│                           CLI (cli.py)                              │
│                     python -m fault_injector run                    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Config Loader (config/loader.py)                 │
│                     load_config(path) → FaultInjectorConfig         │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  FaultOrchestrator (orchestrator/engine.py)         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  组件初始化:                                                  │   │
│  │  - RollbackJournal (WAL)                                     │   │
│  │  - SafetyGuard                                               │   │
│  │  - FaultWatchdog                                             │   │
│  │  - Channels: SSH, Prometheus, Redfish, Switch, K8s          │   │
│  │  - Layer Agents: Hardware, OS, Platform, Service            │   │
│  └─────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Layer Agents (agents/)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────┐ │
│  │HardwareAgent │  │ OSFaultAgent │  │PlatformAgent │  │ServiceA │ │
│  │  layer=hw    │  │  layer=os    │  │ layer=plat   │  │layer=sv │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────┬────┘ │
└─────────┼─────────────────┼─────────────────┼───────────────┼──────┘
          │                 │                 │               │
          └─────────────────┴─────────────────┴───────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Scenarios (scenarios/)                           │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  BaseScenario (abstract)                                     │  │
│  │  - inject(ctx) → InjectResult                               │  │
│  │  - recover(ctx) → RecoverResult                             │  │
│  │  - verify(ctx) → bool                                       │  │
│  │  - monitor_queries() → dict[str, str]                       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐   │
│  │NetworkJitter   │  │RoCEMTUMismatch │  │ PFCDeadlock       │   │
│  │  layer=os      │  │  layer=os      │  │  layer=platform   │   │
│  └────────────────┘  └────────────────┘  └────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Channels (lib/channels/)                       │
│  ┌──────────┐  ┌────────────┐  ┌─────────┐  ┌────────┐  ┌──────┐  │
│  │SSHChannel│  │RedfishChann│  │SwitchCh │  │K8sChan │  │PromCh│  │
│  │          │  │            │  │         │  │        │  │      │  │
│  │run_cmd() │  │power_cycle │  │netconf  │  │kubectl │  │query │  │
│  └──────────┘  └────────────┘  └─────────┘  └────────┘  └──────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.3 核心类说明

#### 1.3.1 FaultOrchestrator

**文件**: `fault_injector/orchestrator/engine.py`

主编排器，负责协调整个故障注入流程。

```python
class FaultOrchestrator:
    def __init__(self, config: FaultInjectorConfig, dry_run: bool = False, session_dir: str | None = None):
        self.config = config
        self.dry_run = dry_run
        self.session_dir = session_dir
        
        # 运行时组件
        self.session: Session | None = None
        self.rollback: RollbackJournal | None = None
        self.guard: SafetyGuard | None = None
        self.watchdog: FaultWatchdog | None = None
        
        # Channels
        self.ssh: SSHChannel | None = None
        self.prometheus: PrometheusChannel | None = None
        self.switch: SwitchChannel | None = None
        # ...
        
        # Layer Agents
        self.layer_agents: dict[str, Any] = {}
    
    async def run(self) -> Session:
        """主入口：执行完整的故障注入会话"""
        # 1. 创建 Session
        # 2. 初始化组件 (_init_components)
        # 3. 启动 Watchdog
        # 4. 预检 (_preflight_check)
        # 5. 收集基线 (_collect_baseline)
        # 6. 执行场景计划 (_run_sequential_plan 或 _run_combined_plan)
        # 7. 生成报告 (_generate_report)
        # 8. 清理 (_close_channels)
```

#### 1.3.2 BaseAgent

**文件**: `fault_injector/agents/base.py`

所有层级代理的抽象基类。

```python
class BaseAgent(ABC):
    def __init__(self, name: str, layer: str, channels: dict[str, Any] | None = None):
        self.name = name
        self.layer = layer
        self.channels = channels or {}
    
    @abstractmethod
    async def inject(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        """注入故障"""
    
    @abstractmethod
    async def recover(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        """恢复故障"""
    
    @abstractmethod
    async def verify(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        """验证恢复状态"""
    
    @abstractmethod
    def status(self) -> dict[str, Any]:
        """返回当前执行状态"""
```

#### 1.3.3 BaseScenario

**文件**: `fault_injector/scenarios/base.py`

所有故障场景的抽象基类。

```python
@dataclass
class FaultContext:
    """故障注入上下文 - 传递给场景的执行环境"""
    ssh: SSHChannel
    rollback: RollbackJournal
    guard: SafetyGuard
    target_node: str
    params: dict[str, Any]
    fault_id: str
    redfish: "RedfishChannel | None" = None
    switch: "SwitchChannel | None" = None
    k8s: "K8sChannel | None" = None
    prometheus: "PrometheusChannel | None" = None
    session_id: str = ""
    interface: str = "eth0"


class BaseScenario(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """场景名称"""
    
    @property
    def layer(self) -> str:
        """故障层级 (hardware / os / platform / service)"""
        return "os"
    
    @abstractmethod
    async def inject(self, ctx: FaultContext) -> InjectResult:
        """注入故障"""
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        """恢复故障（默认由 WAL 处理）"""
    
    async def verify(self, ctx: FaultContext) -> bool:
        """验证恢复是否成功"""
    
    def monitor_queries(self) -> dict[str, str]:
        """返回监控期间的 PromQL 查询"""
```

#### 1.3.4 BaseChannel

**文件**: `lib/channels/base.py`

所有通信通道的抽象基类。

```python
class BaseChannel(ABC):
    def __init__(self, dry_run: bool = False, wal: RollbackJournal | None = None, guard: SafetyGuard | None = None):
        self.dry_run = dry_run
        self.wal = wal
        self.guard = guard
    
    async def execute(
        self,
        action: str,
        params: dict[str, Any],
        recovery_action: str | None = None,
        recovery_params: dict[str, Any] | None = None,
        fault_id: str | None = None,
        target: str = "",
    ) -> ChannelResult:
        """执行操作（带安全检查和 WAL 记录）"""
        # 1. 安全检查
        # 2. WAL 记录（在执行之前）
        # 3. dry_run 模式检查
        # 4. 实际执行 (_execute_impl)
    
    @abstractmethod
    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        """实际执行操作（子类实现）"""
```

### 1.4 会话生命周期

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Session Phase Flow                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐     │
│  │   INIT   │───▶│ BASELINE │───▶│  INJECT  │───▶│ OBSERVE  │     │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘     │
│       │                                               │             │
│       │                                               ▼             │
│       │                                         ┌──────────┐       │
│       │                                         │ RECOVER  │       │
│       │                                         └──────────┘       │
│       │                                               │             │
│       │                                               ▼             │
│       │                                         ┌──────────┐       │
│       │                                         │  VERIFY  │       │
│       │                                         └──────────┘       │
│       │                                               │             │
│       ▼                                               ▼             │
│  ┌──────────┐                                   ┌──────────┐       │
│  │  FAILED  │                                   │ REPORT   │       │
│  └──────────┘                                   └──────────┘       │
│                                                       │             │
│                                                       ▼             │
│                                                 ┌──────────┐       │
│                                                 │COMPLETED │       │
│                                                 └──────────┘       │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.5 安全机制

#### 1.5.1 SafetyGuard

**文件**: `fault_injector/safety/guard.py`

安全守卫，检查命令和操作是否被允许。

```python
class SafetyGuard:
    def __init__(self, config: SafetyConfig):
        self.config = config
        self._forbidden_patterns = [
            # 危险命令模式
        ]
    
    def check_command(self, command: str, channel: str) -> None:
        """检查命令是否安全"""
        # 如果违反安全规则，抛出 SafetyViolationError
```

#### 1.5.2 RollbackJournal (WAL)

**文件**: `fault_injector/safety/rollback.py`

预写日志，记录所有故障注入操作和对应的恢复操作。

```python
class RollbackJournal:
    def record(
        self,
        fault_id: str,
        channel: str,
        target: str,
        inject_action: str,
        inject_params: dict,
        recover_action: str,
        recover_params: dict,
    ) -> None:
        """记录故障注入到 WAL"""
    
    async def recover_all(self) -> list[RecoverResult]:
        """恢复所有活跃故障"""
```

#### 1.5.3 FaultWatchdog

**文件**: `fault_injector/orchestrator/watchdog.py`

看门狗，监控故障注入会话，超时自动触发恢复。

```python
class FaultWatchdog:
    def __init__(self, timeout_seconds: int, rollback_journal: RollbackJournal, session_id: str):
        self.timeout_seconds = timeout_seconds
        self.rollback_journal = rollback_journal
        self.session_id = session_id
    
    async def start(self) -> None:
        """启动看门狗"""
    
    def cancel(self) -> None:
        """取消看门狗"""
```

---

## 2. Load Simulator 架构

### 2.1 模块概览

Load Simulator 是 AI 数据中心服务负载测试工具，支持推理、流水线、微调和 Notebook 等场景。

```
load_simulator/
├── cli.py                 # CLI 入口
├── config/                # 配置加载和 Schema
│   ├── loader.py          # YAML 配置加载
│   ├── schema.py          # Pydantic 数据模型
│   └── defaults.py        # 默认配置
├── orchestrator/          # 编排引擎
│   ├── engine.py          # 主编排器
│   ├── session.py         # 会话追踪
│   ├── session_store.py   # 会话持久化
│   ├── adaptive.py        # 自适应规则
│   ├── preflight.py       # 预检
│   └── platform_monitor.py # 平台监控
├── agents/                # 负载代理
│   ├── base.py            # 代理基类
│   ├── inference.py       # 推理代理
│   ├── pipeline.py        # 流水线代理
│   ├── finetune.py        # 微调代理
│   ├── notebook.py        # Notebook 代理
│   ├── monitor.py         # 指标监控
│   └── bottleneck.py      # 瓶颈分析
├── channels/              # 通信通道
│   ├── inference.py       # 推理 API 通道
│   └── notebook.py        # Jupyter API 通道
├── load/                  # 负载生成
│   ├── profile.py         # 负载配置
│   ├── rate_limiter.py    # 速率限制
│   ├── prompt_pool.py     # 提示词池
│   └── token_distribution.py # Token 分布
├── metrics/               # 指标收集
│   ├── collector.py       # 指标收集器
│   ├── aggregator.py      # 指标聚合
│   ├── thresholds.py      # 阈值定义
│   └── time_series.py     # 时间序列
└── reporting/             # 报告生成
    ├── bundle.py          # 报告打包
    ├── charts.py          # 图表生成
    ├── html_report.py     # HTML 报告
    └── session_output.py  # 会话输出
```

### 2.2 核心组件关系

```
┌─────────────────────────────────────────────────────────────────────┐
│                           CLI (cli.py)                              │
│                  python -m load_simulator run                       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Config Loader (config/loader.py)                 │
│                    load_config(path) → LoadSimulatorConfig          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   LoadOrchestrator (orchestrator/engine.py)         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  组件初始化:                                                  │   │
│  │  - SessionTracker                                            │   │
│  │  - SessionStore (可选)                                       │   │
│  │  - MetricCollector                                           │   │
│  │  - PrometheusChannel (可选)                                  │   │
│  │  - AdaptiveRules                                             │   │
│  └─────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Load Agents (agents/)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────┐ │
│  │InferenceAgent│  │PipelineAgent │  │FineTuneAgent │  │Notebook │ │
│  │              │  │              │  │              │  │ Agent   │ │
│  │ vLLM API    │  │ Argo Workflow│  │LLaMA-Factory │  │Jupyter  │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────┬────┘ │
└─────────┼─────────────────┼─────────────────┼───────────────┼──────┘
          │                 │                 │               │
          └─────────────────┴─────────────────┴───────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Channels (channels/)                          │
│  ┌──────────────────┐  ┌───────────────────┐  ┌──────────────────┐ │
│  │ InferenceChannel │  │ NotebookChannel   │  │ CubeStudioChannel│ │
│  │                  │  │                   │  │                  │ │
│  │ /v1/chat/complet │  │ /api/kernels      │  │ pipeline API    │ │
│  └──────────────────┘  └───────────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 核心类说明

#### 2.3.1 LoadOrchestrator

**文件**: `load_simulator/orchestrator/engine.py`

主编排器，协调所有代理并收集会话结果。

```python
class LoadOrchestrator:
    _AGENT_FACTORIES: dict[str, type[BaseAgent]] = {
        "inference": InferenceAgent,
        "pipeline": PipelineAgent,
        "finetune": FineTuneAgent,
        "notebook": NotebookAgent,
    }
    
    def __init__(
        self,
        config: LoadSimulatorConfig,
        preflight_checker: Callable | None = None,
        enable_monitor: bool = True,
        session_dir: str | None = None,
        resume_session_id: str | None = None,
        dry_run: bool = False,
    ):
        self._config = config
        self._adaptive = AdaptiveRules(...)
        self._session_tracker = SessionTracker()
        self._metric_collector = MetricCollector()
        # ...
    
    async def run(self, only: list[str] | None = None) -> SessionResult:
        """主入口：执行负载测试会话"""
        # 1. 确定执行模式 (single/mixed/stress/soak)
        # 2. 构建阶段计划 (_mode_plan)
        # 3. 执行预检 (_run_preflight)
        # 4. 按阶段执行 (_run_stage)
        # 5. 自适应评估 (_adaptive.evaluate)
        # 6. 瓶颈分析 (BottleneckAnalyzer)
        # 7. 返回 SessionResult
```

#### 2.3.2 BaseAgent

**文件**: `load_simulator/agents/base.py`

所有负载代理的抽象基类。

```python
@dataclass
class AgentResult:
    """代理执行结果"""
    name: str
    status: str  # "success" | "error"
    metrics: dict[str, Any] = field(default_factory=dict)
    start_time: float | None = None
    end_time: float | None = None
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    agent_name: str = "base"
    
    @abstractmethod
    async def run(self, duration_seconds: int) -> AgentResult:
        """执行负载测试"""
    
    async def _timed_run(self, duration_seconds: int) -> AgentResult:
        """带计时和错误处理的执行包装"""
    
    async def _execute(self, duration_seconds: int) -> AgentResult:
        """实际执行逻辑（子类实现）"""
```

#### 2.3.3 自适应规则

**文件**: `load_simulator/orchestrator/adaptive.py`

根据运行时指标动态调整负载。

```python
@dataclass
class AdaptiveThresholds:
    pause_error_rate: float = 0.05
    breaking_error_rate: float = 0.10
    p99_breaking_multiplier: float = 10.0
    gpu_mem_reduce_pct: float = 95.0
    cpu_pause_pct: float = 95.0


class AdaptiveRules:
    def __init__(self, thresholds: AdaptiveThresholds):
        self.thresholds = thresholds
    
    def evaluate(self, metrics: dict[str, Any], baseline_p99_ms: float | None = None) -> AdaptiveDecision:
        """评估当前指标并返回决策"""
        # 返回决策：
        # - CONTINUE: 继续当前负载
        # - PAUSE_RAMP_UP: 暂停增加负载
        # - REDUCE_INFERENCE_CONCURRENCY: 降低并发
        # - RECORD_BREAKING_POINT: 记录断点
```

### 2.4 执行模式

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Execution Modes                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  single (单代理)                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  {"name": "single", "duration_scale": 1.0, "concurrency": 1} │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  mixed (多代理)                                                     │
│  ┌────────────────────────┐  ┌────────────────────────────────┐   │
│  │ mixed-warmup           │─▶│ mixed-main                     │   │
│  │ duration=0.7, conc=0.8 │  │ duration=1.0, conc=1.0         │   │
│  └────────────────────────┘  └────────────────────────────────┘   │
│                                                                     │
│  stress (压力测试)                                                  │
│  ┌────────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐          │
│  │ baseline   │─▶│ ramp-1  │─▶│ ramp-2  │─▶│ stress-1 │─▶ ...    │
│  │ dur=0.5    │  │ dur=0.8 │  │ dur=1.0 │  │ dur=1.2  │          │
│  │ conc=0.5   │  │ conc=0.8│  │ conc=1.0│  │ conc=1.25│          │
│  └────────────┘  └─────────┘  └─────────┘  └──────────┘          │
│                                                                     │
│  soak (浸泡测试)                                                    │
│  ┌────────────────┐  ┌─────────────────┐  ┌─────────────────┐    │
│  │ soak-warmup    │─▶│ soak-steady-1   │─▶│ soak-steady-2   │    │
│  │ dur=1.0        │  │ dur=2.0         │  │ dur=2.0         │    │
│  │ conc=0.6       │  │ conc=0.6        │  │ conc=0.6        │    │
│  └────────────────┘  └─────────────────┘  └─────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.5 瓶颈分析

**文件**: `load_simulator/agents/bottleneck.py`

```python
class BottleneckAnalyzer:
    def analyze(self, metrics: dict[str, Any]) -> BottleneckReport:
        """分析指标并识别瓶颈"""
        # 检查：
        # - CPU 使用率
        # - 内存使用率
        # - GPU 使用率
        # - GPU 内存
        # - 错误率
        # - 延迟 P99
```

---

## 3. 共享组件

### 3.1 lib/channels/ 通道模块

两个系统共享 `lib/channels/` 下的通信通道：

```
lib/channels/
├── base.py          # BaseChannel 基类
├── ssh.py           # SSHChannel (asyncssh)
├── prometheus.py    # PrometheusChannel (PromQL)
├── redfish.py       # RedfishChannel (BMC)
├── kubernetes.py    # K8sChannel (kubectl)
├── switch.py        # SwitchChannel (NETCONF)
└── cube_studio.py   # CubeStudioChannel (REST API)
```

### 3.2 配置 Schema 对比

| 特性 | Fault Injector | Load Simulator |
|------|----------------|----------------|
| 配置文件 | YAML | YAML |
| Schema 库 | Pydantic v2 | Pydantic v2 |
| 主配置类 | `FaultInjectorConfig` | `LoadSimulatorConfig` |
| 节点清单 | `inventory: dict[str, list[TargetNodeConfig]]` | N/A |
| 场景配置 | `scenarios: dict[str, ScenarioConfig]` | `agents: list[str]` |

---

## 4. 典型调用流程

### 4.1 Fault Injector 完整调用流程

```
用户命令
    │
    ▼
python -m fault_injector run --config test.yaml --scenario roce_mtu_mismatch
    │
    ▼
cli.py:run_cmd()
    │
    ├── load_config("test.yaml")
    │       │
    │       ▼
    │   config/loader.py:load_config()
    │       │
    │       ├── yaml.safe_load()
    │       └── _parse_config() → FaultInjectorConfig
    │
    ├── FaultOrchestrator(config)
    │
    └── asyncio.run(orchestrator.run())
            │
            ▼
    engine.py:FaultOrchestrator.run()
            │
            ├── Session.create()
            │
            ├── _init_components()
            │       ├── RollbackJournal
            │       ├── SafetyGuard
            │       ├── FaultWatchdog
            │       ├── SSHChannel
            │       ├── PrometheusChannel
            │       ├── SwitchChannel
            │       ├── MonitorAgent
            │       └── Layer Agents (Hardware, OS, Platform, Service)
            │
            ├── watchdog.start()
            │
            ├── _preflight_check()
            │
            ├── _collect_baseline()
            │       └── monitor_agent.collect_baseline()
            │
            ├── _run_sequential_plan() 或 _run_combined_plan()
            │       │
            │       ▼
            │   对于每个 ScenarioConfig:
            │       │
            │       ├── _get_layer_agent(scenario_name)
            │       │       └── 查询 SCENARIO_REGISTRY 获取 layer
            │       │       └── 返回对应的 layer_agent
            │       │
            │       ├── _build_context() → FaultContext
            │       │
            │       ├── agent.inject(scenario_name, ctx)
            │       │       │
            │       │       ▼
            │       │   agents/os_fault.py:OSFaultAgent.inject()
            │       │       │
            │       │       ├── _resolve(scenario_name) → Scenario
            │       │       └── scenario.inject(ctx)
            │       │               │
            │       │               ▼
            │       │       scenarios/base.py:BaseScenario.inject()
            │       │               │
            │       │               └── ssh.run_command("ip link set ...")
            │       │                       │
            │       │                       ▼
            │       │               lib/channels/ssh.py:SSHChannel.run_command()
            │       │                       │
            │       │                       ├── _get_connection()
            │       │                       └── conn.run("sudo ip link ...")
            │       │
            │       ├── session.add_active_fault()
            │       │
            │       ├── [等待 observe_duration]
            │       │
            │       ├── agent.recover(scenario_name, ctx)
            │       │
            │       └── agent.verify(scenario_name, ctx)
            │
            ├── _generate_report()
            │
            ├── session.complete()
            │
            └── _close_channels()
```

### 4.2 Load Simulator 完整调用流程

```
用户命令
    │
    ▼
python -m load_simulator run --config config.yaml --mode stress
    │
    ▼
cli.py:run_cmd()
    │
    ├── load_config("config.yaml")
    │       │
    │       ▼
    │   config/loader.py:load_config()
    │       └── → LoadSimulatorConfig
    │
    ├── LoadOrchestrator(cfg)
    │
    └── asyncio.run(orchestrator.run(only=[]))
            │
            ▼
    engine.py:LoadOrchestrator.run()
            │
            ├── _effective_mode(selected) → "stress"
            │
            ├── _mode_plan("stress") → stages[]
            │       └── 返回阶段计划列表
            │
            ├── _run_preflight(selected)
            │       └── 检查各服务端点可达性
            │
            ├── session_tracker.start()
            │
            └── 对于每个 stage:
                    │
                    ├── _run_stage(selected, duration_scale, concurrency_scale)
                    │       │
                    │       ├── 对于每个 agent:
                    │       │       │
                    │       │       ├── _build_agent(name, ...) → (agent, duration)
                    │       │       │       ├── InferenceAgent(InferenceChannel)
                    │       │       │       ├── PipelineAgent(CubeStudioChannel)
                    │       │       │       ├── FineTuneAgent(CubeStudioChannel)
                    │       │       │       └── NotebookAgent(NotebookChannel)
                    │       │       │
                    │       │       └── asyncio.create_task(agent.run(duration))
                    │       │
                    │       ├── MetricsMonitor.start() (后台任务)
                    │       │
                    │       ├── asyncio.gather(*agent_tasks)
                    │       │
                    │       └── monitor.aggregate() → system_metrics
                    │
                    ├── _merge_numeric_metrics(results, system_metrics)
                    │
                    ├── _adaptive.evaluate(metrics, baseline_p99)
                    │       └── → AdaptiveDecision
                    │
                    ├── 记录 adaptive_event
                    │
                    └── 如果 decision.record_breaking_point:
                            └── _refine_breaking_point() (二分搜索)
            
            ├── BottleneckAnalyzer.analyze(merged_all)
            │
            ├── session_tracker.complete()
            │
            └── 返回 SessionResult
```

### 4.3 SSH Channel 认证流程

```
SSHChannel.run_command(node, command)
    │
    ├── _get_node_config(node) → TargetNodeConfig
    │
    └── _get_connection(node)
            │
            ├── 检查现有连接缓存
            │
            ├── 构建连接参数:
            │       ├── host, port, username
            │       ├── known_hosts=None (禁用主机密钥检查)
            │       └── 认证方式:
            │               ├── if key_file: client_keys=[key_file]
            │               └── elif password: password=password
            │
            └── asyncssh.connect(**connect_kwargs)
                    │
                    ├── TCP 连接
                    ├── SSH 握手
                    └── 认证 (key 或 password)
```

---

## 附录

### A. 文件索引

| 模块 | 文件 | 主要类/函数 |
|------|------|-------------|
| fault_injector | `cli.py` | `run_cmd()`, `recover_cmd()`, `resume_cmd()` |
| fault_injector | `config/loader.py` | `load_config()` |
| fault_injector | `config/schema.py` | `FaultInjectorConfig`, `SSHConfig`, `ScenarioConfig` |
| fault_injector | `orchestrator/engine.py` | `FaultOrchestrator` |
| fault_injector | `orchestrator/session.py` | `Session`, `ActiveFault`, `ScenarioResult` |
| fault_injector | `agents/base.py` | `BaseAgent`, `AgentResult` |
| fault_injector | `agents/os_fault.py` | `OSFaultAgent` |
| fault_injector | `scenarios/base.py` | `BaseScenario`, `FaultContext` |
| fault_injector | `scenarios/registry.py` | `SCENARIO_REGISTRY`, `register_scenario()` |
| fault_injector | `safety/rollback.py` | `RollbackJournal`, `RollbackEntry` |
| fault_injector | `safety/guard.py` | `SafetyGuard` |
| load_simulator | `cli.py` | `run_cmd()`, `validate_config_cmd()` |
| load_simulator | `config/loader.py` | `load_config()` |
| load_simulator | `orchestrator/engine.py` | `LoadOrchestrator`, `SessionResult` |
| load_simulator | `agents/base.py` | `BaseAgent`, `AgentResult` |
| load_simulator | `agents/inference.py` | `InferenceAgent` |
| load_simulator | `agents/bottleneck.py` | `BottleneckAnalyzer` |
| load_simulator | `orchestrator/adaptive.py` | `AdaptiveRules`, `AdaptiveThresholds` |
| lib/channels | `base.py` | `BaseChannel` |
| lib/channels | `ssh.py` | `SSHChannel` |
| lib/channels | `prometheus.py` | `PrometheusChannel` |
| lib/channels | `switch.py` | `SwitchChannel` |

### B. 命令速查

```bash
# Fault Injector
python -m fault_injector run --config fault_injector/test.yaml --scenario network_jitter
python -m fault_injector run --config fault_injector/test.yaml --dry-run
python -m fault_injector list-scenarios
python -m fault_injector validate-config fault_injector/test.yaml
python -m fault_injector recover --session <session_id>

# Load Simulator
python -m load_simulator run --config load_simulator/config/test.yaml
python -m load_simulator run --duration 60 --concurrency 8
python -m load_simulator run --mode stress --output-format json
python -m load_simulator list-scenarios
python -m load_simulator validate-config load_simulator/config/test.yaml
```

---

*文档版本: 1.0.0 | 最后更新: 2026-02-28*