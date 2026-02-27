<!--
=============================================================================
FILE: techdesign.md (Technical Design Document)
PURPOSE: Document technical architecture, design decisions, and implementation details
GUIDANCE FOR AI AGENTS:
- This file defines HOW the product should be implemented
- Include architecture diagrams, API designs, data models, and algorithms
- Document design decisions and their rationale
- Update this file when making significant architectural changes
- Reference this file for implementation patterns and conventions
=============================================================================
-->

# Technical Design Document: Fault Injector

## Document Metadata
| Field | Value |
|-------|-------|
| Project Name | Fault Injector |
| Version | 1.0 |
| Status | Implemented |
| Last Updated | 2026-02-27 |
| Author | AIDC Auto-SRE Team |

---

## 1. Overview

### 1.1 System Purpose

Fault Injector 是 Cube Studio 平台的多维度故障注入系统，采用 **确定性编排 + LLM 诊断** 的混合架构：

- **确定性编排**：故障注入/恢复由确定性 Python 引擎执行，安全可控可重放
- **LLM 诊断**：故障诊断分析由 MiniMax-2.1 提供智能推理（只读分析，不执行操作）

系统能够：
- 在硬件层通过 BMC Redfish 和交换机 API 注入 CPU/GPU/内存/网络故障
- 在 OS 层注入内核、文件系统、进程级故障
- 在平台层注入 K8s 组件、数据库、缓存、消息队列故障
- 在服务层注入推理服务、训练 Pipeline、Notebook 故障
- 与 Load Simulator 联动，通过过载制造故障

### 1.2 Design Goals

1. **安全第一**：所有故障注入操作可恢复，WAL 写前日志保证
2. **确定性执行**：核心执行路径无 LLM 调用，精确可控可审计
3. **异步优先**：所有 I/O 操作使用 asyncio，非阻塞执行
4. **Channel 抽象**：统一封装 SSH/Redfish/K8s/交换机等后端
5. **场景驱动**：故障逻辑封装为自包含的 Scenario 类

### 1.3 Scope

**In Scope:**
- 硬件/OS/平台/服务四层故障注入
- 必选场景：vLLM 延迟不稳定（6 个 root cause）、RDMA 异常（6 个子场景）
- WAL 回滚恢复机制
- CLI 工具和 YAML 配置

**Out of Scope:**
- 生产环境实时故障注入
- BMC 网络配置变更（安全限制）
- LLM 直接执行故障注入（仅用于诊断）

---

## 2. Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI (Click)                          │
│  run / validate-config / list-scenarios / recover          │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                    Orchestrator                             │
│  (asyncio 确定性调度)                                        │
│  · 阶段管理: baseline → inject → observe → recover → verify │
│  · 并发控制: max_concurrent_faults                          │
│  · 回滚看门狗: auto_recover_timeout                         │
└─────────────────────────┬───────────────────────────────────┘
                          │
        ┌────────┬────────┼────────┬────────┬────────┐
        │        │        │        │        │        │
  ┌─────▼────┐ ┌─▼───┐ ┌──▼────┐ ┌──▼────┐ ┌──▼────┐ ┌─▼─────┐
  │ Hardware │ │ OS  │ │Platform│ │Service│ │Monitor│ │Diag-  │
  │  Fault   │ │Fault│ │ Fault  │ │ Fault │ │ Agent │ │nosis  │
  │  Agent   │ │Agent│ │ Agent  │ │ Agent │ │       │ │Agent  │
  │ (确定)   │ │(确定)│ │ (确定) │ │ (确定) │ │ (确定) │ │(LLM)  │
  └────┬─────┘ └──┬──┘ └──┬────┘ └──┬────┘ └───┬────┘ └──┬───┘
       │          │       │          │           │         │
       └──────────┴───────┴──────────┴───────────┘         │
                          │                                 │
                 ┌────────▼────────┐            ┌──────────▼──────────┐
                 │  Channel 层     │            │  Load Simulator     │
                 │  (类型化执行后端) │            │  (联动压测)         │
                 └────────┬────────┘            └─────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                    Safety 层                                │
│  · SafetyGuard (禁止操作拦截)                                │
│  · RollbackJournal (WAL 回滚日志)                           │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Component Overview

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| CLI | 命令行入口 | Click |
| Orchestrator | 故障调度、阶段管理、并发控制 | Python asyncio |
| Agents | 各层故障注入执行 | Python (确定性) |
| Scenarios | 故障场景定义 | Python (BaseScenario 子类) |
| Channels | 类型化执行后端 | asyncssh / httpx / kubernetes-client |
| Safety | 安全守卫、WAL 回滚 | Python |
| Diagnosis Agent | LLM 诊断分析 | MiniMax-2.1 (Planned) |

### 2.3 Data Flow

```
YAML 配置 → Pydantic 校验 → Orchestrator 调度
    ↓
Session 初始化 → Channel 连接池 → WAL 初始化
    ↓
基线采集 (Prometheus) → 故障注入 (Channel.execute)
    ↓
观测期 (指标采集) → 恢复 (WAL 回滚)
    ↓
验证 (指标对比) → 报告生成
```

---

## 3. Detailed Design

### 3.1 Module: orchestrator/engine.py

**Purpose:** 主执行引擎，管理故障注入的完整生命周期

**Key Classes:**

```python
class OrchestrationEngine:
    """确定性编排引擎"""
    
    async def run(self, config: FaultInjectorConfig) -> SessionResult:
        """执行完整的故障注入流程
        
        Phase 1: 初始化 + 连通性检查
        Phase 2: 基线采集 (baseline_duration 秒)
        Phase 3: 按场景循环执行 inject → observe → recover → verify
        Phase 4: 诊断分析 (LLM, 可选)
        Phase 5: 报告生成
        """
        session = Session.create(config)
        watchdog = asyncio.create_task(
            self.rollback.auto_recover_watchdog(
                config.global_.safety.auto_recover_timeout
            )
        )
        
        try:
            # Phase 1-2: 初始化 + 基线
            channels = await self._init_channels(config)
            baseline = await self.monitor.collect_baseline(...)
            
            # Phase 3: 场景循环
            for scenario_config in self._resolve_scenarios(config):
                scenario = SCENARIO_REGISTRY[scenario_config.name]()
                ctx = FaultContext(channels=channels, baseline=baseline, ...)
                
                await scenario.inject(ctx)
                metrics = await self.monitor.observe(...)
                await scenario.recover(ctx)
                verified = await scenario.verify(ctx)
                
            # Phase 4-5: 诊断 + 报告
            ...
        finally:
            watchdog.cancel()
```

**Dependencies:**
- Session (状态持久化)
- Channels (执行后端)
- RollbackJournal (WAL)
- MonitorAgent (指标采集)

**Error Handling:**
- 任何阶段异常 → 触发 `rollback.recover_all()`
- 超时 → Watchdog 自动恢复
- Channel 连接失败 → 重试或降级

---

### 3.2 Module: orchestrator/session.py

**Purpose:** Session 状态管理和持久化

**Key Classes:**

```python
@dataclass
class Session:
    """故障注入会话"""
    session_id: str                             # UUID
    config_hash: str                            # YAML SHA256
    started_at: datetime
    status: Literal["running", "paused", "completed", "failed"]
    phase: Literal["init", "baseline", "inject", "observe", "recover", "verify", "report"]
    active_faults: list[ActiveFault]
    rollback_journal_path: str
    metrics_baseline: dict[str, list[float]]
    events: list[Event]
    scenario_results: dict[str, ScenarioResult]
    
    def save(self) -> None:
        """持久化到 session.json"""
        path = Path(self.rollback_journal_path).parent / "session.json"
        path.write_text(self.model_dump_json(indent=2))
    
    @classmethod
    def load(cls, session_id: str) -> "Session":
        """从磁盘加载 Session（用于 --resume）"""
        ...
```

**Session 目录结构:**

```
fault-reports/sessions/{session_id}/
├── session.json          # Session 元数据
├── rollback.jsonl        # WAL 回滚日志
├── baseline.json         # 基线指标数据
├── events.jsonl          # 事件时间线
├── metrics/              # 观测期指标
│   └── {timestamp}.json
└── report/               # 最终报告
    ├── report.html
    └── report.json
```

---

### 3.3 Module: scenarios/base.py

**Purpose:** 场景基类，定义标准生命周期接口

**Key Classes:**

```python
class BaseScenario(ABC):
    """故障场景基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """场景名称"""
    
    @property
    @abstractmethod
    def description(self) -> str:
        """场景描述"""
    
    @property
    @abstractmethod
    def layer(self) -> str:
        """故障层级: hardware | os | platform | service"""
    
    @abstractmethod
    async def inject(self, ctx: FaultContext) -> InjectResult:
        """注入故障"""
    
    @abstractmethod
    def monitor_queries(self) -> dict[str, str]:
        """返回监控 PromQL 映射"""
    
    @abstractmethod
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        """恢复故障"""
    
    @abstractmethod
    async def verify(self, ctx: FaultContext) -> bool:
        """验证恢复是否成功"""
```

**FaultContext:**

```python
@dataclass
class FaultContext:
    """传递给 Scenario 的执行上下文"""
    channels: dict[str, BaseChannel]
    target_node: str
    params: dict
    baseline: dict[str, float]
    rollback: RollbackJournal
    
    @property
    def ssh(self) -> SSHChannel:
        return self.channels["ssh"]
    
    @property
    def redfish(self) -> RedfishChannel:
        return self.channels["redfish"]
    
    @property
    def k8s(self) -> K8sChannel:
        return self.channels["kubernetes"]
```

---

### 3.4 Module: scenarios/vllm_latency.py

**Purpose:** vLLM 延迟不稳定的 6 个 root cause 场景

**Implemented Scenarios:**

| Class | Scenario ID | Description |
|-------|-------------|-------------|
| `GPUContentionScenario` | RC-1 | GPU 资源争抢 |
| `NetworkJitterScenario` | RC-2 | 网络链路抖动 |
| `StorageIOInterferenceScenario` | RC-3 | 存储 I/O 干扰 |
| `PlatformCascadeScenario` | RC-4 | 平台组件级联延迟 |
| `OSResourcePressureScenario` | RC-5 | OS 资源压力 |
| `ThermalThrottlingScenario` | RC-6 | 热降频 |

**Example Implementation:**

```python
class GPUContentionScenario(BaseScenario):
    """RC-1: GPU 资源争抢导致 vLLM 延迟不稳定"""
    
    @property
    def name(self) -> str:
        return "gpu_contention"
    
    @property
    def description(self) -> str:
        return "GPU 资源争抢导致 vLLM 推理延迟不稳定"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        
        # 在推理服务所在节点启动 GPU 满载
        await ctx.ssh.run_command(
            ctx.target_node,
            f"gpu-burn -d {duration}",
            recovery_command="pkill -f gpu-burn"
        )
        
        # 可选: 显存压力
        if ctx.params.get("mem_pressure", False):
            await ctx.ssh.run_command(
                ctx.target_node,
                "python3 /opt/fault-injector/scripts/gpu_mem_pressure.py "
                f"--gpu 0 --alloc {ctx.params.get('alloc_pct', 60)}%",
                recovery_command="pkill -f gpu_mem_pressure"
            )
        
        return InjectResult(success=True)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "gpu_util": 'DCGM_FI_DEV_GPU_UTIL{node="$node"}',
            "gpu_mem_used": 'DCGM_FI_DEV_FB_USED{node="$node"}',
        }
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        # WAL 已自动处理 pkill
        await ctx.ssh.run_command(ctx.target_node, "nvidia-smi --gpu-reset 2>/dev/null || true")
        return RecoverResult(success=True)
    
    async def verify(self, ctx: FaultContext) -> bool:
        p50 = await ctx.prometheus.query_instant(
            self.monitor_queries()["inference_p50"]
        )
        baseline_p50 = ctx.baseline.get("inference_p50", 0)
        if baseline_p50 == 0:
            return True
        return abs(p50 - baseline_p50) / baseline_p50 < 0.15  # 15% 容差
```

---

### 3.5 Module: scenarios/rdma_anomaly.py

**Purpose:** RDMA 网络异常的 6 个子场景

**Implemented Scenarios:**

| Class | Scenario ID | Description |
|-------|-------------|-------------|
| `PFCDeadlockScenario` | F-1 | PFC 死锁 |
| `ECNMisconfigurationScenario` | F-2 | ECN 阈值错配 |
| `RDMALoadImbalanceScenario` | F-3 | RDMA 负载不均衡 |
| `RDMALinkFlapScenario` | F-4 | RDMA 链路间歇中断 |
| `RoCEMTUMismatchScenario` | F-5 | RoCE MTU 不匹配 |
| `RDMAQoSDowngradeScenario` | F-6 | RDMA QoS 降级 |

---

## 4. Data Models

### 4.1 Entity Relationship

```
┌─────────────┐       ┌─────────────┐
│   Session   │──1:N──│   Scenario  │
│             │       │   Result    │
└──────┬──────┘       └─────────────┘
       │
       │ 1:1
       ▼
┌─────────────┐       ┌─────────────┐
│  Rollback   │──1:N──│   Rollback  │
│  Journal    │       │    Entry    │
└─────────────┘       └─────────────┘
```

### 4.2 Schema Definitions

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Literal

# ─── 配置 Schema ───

class GlobalConfig(BaseModel):
    cube_studio_url: str
    prometheus_url: str
    log_level: str = "INFO"
    report_output: str = "./fault-reports/"
    safety: SafetyConfig

class SafetyConfig(BaseModel):
    require_confirmation: bool = True
    max_concurrent_faults: int = 3
    auto_recover_timeout: int = 600
    excluded_nodes: list[str] = []
    dry_run: bool = False

class ScenarioConfig(BaseModel):
    name: str
    enabled: bool = True
    target_nodes: list[str]
    params: dict = {}

# ─── 结果 Schema ───

class InjectResult(BaseModel):
    success: bool
    error: str | None = None
    duration_seconds: float = 0

class RecoverResult(BaseModel):
    success: bool
    error: str | None = None

class ScenarioResult(BaseModel):
    scenario_name: str
    inject_result: InjectResult
    recover_result: RecoverResult
    verified: bool
    baseline_metrics: dict[str, float]
    during_metrics: dict[str, list[float]]
    post_metrics: dict[str, list[float]]

# ─── WAL Schema ───

class RollbackEntry(BaseModel):
    fault_id: str
    injected_at: datetime
    channel: str
    target: str
    inject_action: str
    recover_action: str
    recover_params: dict
    status: Literal["active", "recovered", "failed"]
```

---

## 5. API Design

### 5.1 CLI Interface

```bash
# 标准运行
fault-injector --config config.yaml

# 仅运行指定场景
fault-injector --config config.yaml --scenario gpu_contention

# 干运行（验证配置，不执行）
fault-injector --config config.yaml --dry-run

# 列出所有可用场景
fault-injector --list-scenarios

# 验证配置文件
fault-injector --validate --config config.yaml

# 恢复中断的会话
fault-injector --resume <session_id>

# 手动恢复所有故障
fault-injector --recover-all --session <session_id>
```

### 5.2 Channel Interface

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

### 5.3 Scenario Registry

```python
SCENARIO_REGISTRY: dict[str, Type[BaseScenario]] = {
    # vLLM latency (RC-1 ~ RC-6)
    "gpu_contention": GPUContentionScenario,
    "network_jitter": NetworkJitterScenario,
    "storage_io_interference": StorageIOInterferenceScenario,
    "platform_cascade": PlatformCascadeScenario,
    "os_resource_pressure": OSResourcePressureScenario,
    "thermal_throttling": ThermalThrottlingScenario,
    
    # RDMA anomaly (F-1 ~ F-6)
    "pfc_deadlock": PFCDeadlockScenario,
    "ecn_misconfiguration": ECNMisconfigurationScenario,
    "rdma_load_imbalance": RDMALoadImbalanceScenario,
    "rdma_link_flap": RDMALinkFlapScenario,
    "roce_mtu_mismatch": RoCEMTUMismatchScenario,
    "rdma_qos_downgrade": RDMAQoSDowngradeScenario,
    
    # Hardware extension
    "cpu_stress": CPUStressScenario,
    "gpu_removal": GPURemovalScenario,
    "thermal_throttle_hw": ThermalThrottleHWScenario,
    "network_delay_hw": NetworkDelayHWScenario,
    
    # OS extension
    "memory_pressure": MemoryPressureScenario,
    "disk_full": DiskFullScenario,
    "time_skew": TimeSkewScenario,
    
    # Platform extension
    "mysql_connection_drop": MySQLConnectionDropScenario,
    "redis_unavailable": RedisUnavailableScenario,
    "celery_worker_kill": CeleryWorkerKillScenario,
    "istio_gateway_kill": IstioGatewayKillScenario,
    
    # Service extension
    "inference_pod_kill": InferencePodKillScenario,
    "pipeline_workflow_cancel": PipelineWorkflowCancelScenario,
    "notebook_pod_kill": NotebookPodKillScenario,
}
```

---

## 6. Design Decisions

### Decision 1: 确定性编排 + LLM 诊断

**Context:** 故障注入是破坏性操作，需要精确可控。

**Decision:** 采用混合架构：
- 编排 + 故障 Agent：Python + asyncio（确定性）
- 诊断 Agent：MiniMax-2.1（LLM）

**Rationale:**
- LLM 幻觉可能生成错误的 shell 命令，对生产基础设施造成不可逆损害
- 根因推断和传播链分析才是真正需要推理能力的任务
- Diagnosis Agent 是只读分析，即使 LLM 出错也不会造成系统损害

**Alternatives Considered:**
1. 全 LLM 驱动 — 拒绝，不可控风险
2. 全规则驱动 — 拒绝，缺乏智能分析能力

**Consequences:**
- 优点：安全可控，兼具智能分析能力
- 缺点：需要维护两套逻辑（确定性 + LLM）

---

### Decision 2: WAL (Write-Ahead Log) 回滚机制

**Context:** 故障注入后进程崩溃，需要能恢复到正常状态。

**Decision:** 采用 WAL 模式，恢复操作在注入**之前**持久化到磁盘。

**Rationale:**
- 只要 rollback.jsonl 未损坏，所有已注入故障均可恢复
- JSONL 格式确保即使在追加写入中途崩溃，已写入的条目仍然有效

**Implementation:**

```python
class RollbackJournal:
    def record(self, fault_id: str, recover_action: str,
               recover_params: dict) -> None:
        """写前记录：在故障注入之前调用，立即 fsync 到磁盘"""
        entry = RollbackEntry(...)
        self.entries.append(entry)
        self._append_to_disk(entry)   # JSONL append + fsync
    
    async def recover_all(self) -> list[RecoveryResult]:
        """按注入逆序恢复所有活跃故障"""
        active = [e for e in reversed(self.entries) if e.status == "active"]
        results = []
        for entry in active:
            result = await self._execute_recovery(entry)
            entry.status = "recovered" if result.success else "failed"
            results.append(result)
        self._rewrite_journal()
        return results
```

---

### Decision 3: Channel 抽象层

**Context:** 需要对接多种后端（SSH/Redfish/K8s/交换机/Prometheus）。

**Decision:** 统一 Channel 抽象，封装认证、连接池、重试、dry-run、安全守卫。

**Rationale:**
- 与 Load Simulator 和 SRE Agent 共享 Channel 实现
- 统一安全策略（禁止操作检查）
- 便于测试和 Mock

**Shared Channels:**

| Channel | 用途 | 共享系统 |
|---------|------|---------|
| SSHChannel | 节点命令执行 | fault-injector, SRE Agent |
| RedfishChannel | BMC 硬件控制 | fault-injector, SRE Agent |
| SwitchChannel | 交换机端口/配置操作 | fault-injector, SRE Agent |
| K8sChannel | Pod/Deployment 故障注入 | fault-injector, SRE Agent |
| PrometheusChannel | 基线采集与偏差检测 | fault-injector, Load Simulator, SRE Agent |
| CubeStudioChannel | 推理服务状态查询与更新 | fault-injector, Load Simulator, SRE Agent |

---

## 7. Security Considerations

### 7.1 Authentication & Authorization

- BMC Redfish: X-Auth-Token session 认证
- K8s: kubeconfig 或 ServiceAccount
- SSH: 密钥认证（推荐）或密码
- Cube Studio: JWT 认证

### 7.2 Data Protection

- 所有密码/密钥通过环境变量引用：`${BMC_PASSWORD}`
- WAL 日志不记录明文密码
- 敏感操作记录审计日志

### 7.3 Security Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| SSH 命令注入 | `shlex.quote()` 所有参数 |
| BMC 网络修改 | 硬编码禁止 `PATCH .../EthernetInterfaces/{id}` |
| 危险命令执行 | SafetyGuard 白名单 + 黑名单 |
| 进程崩溃 | WAL 回滚 + `--resume` 恢复 |

### 7.4 禁止操作（FORBIDDEN_OPERATIONS）

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

---

## 8. Performance Considerations

### 8.1 Scalability

- 异步 I/O：所有 Channel 操作使用 asyncio
- 并发控制：`max_concurrent_faults` 限制同时注入的故障数
- 连接池：SSH/HTTP 连接复用

### 8.2 Resource Limits

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_concurrent_faults` | 3 | 同时最多注入 3 个故障 |
| `ssh.pool_size` | 5 | 每节点 SSH 连接池大小 |
| `auto_recover_timeout` | 600s | 超时后自动恢复 |

### 8.3 Performance Requirements

| Metric | Target |
|--------|--------|
| 单场景注入延迟 | < 5s |
| 基线采集时长 | 120s（可配置） |
| 观测期时长 | 300s（可配置） |
| 恢复验证时长 | 120s（可配置） |

---

## 9. Error Handling

### 9.1 Error Categories

| Category | Handling |
|----------|----------|
| 配置错误 | Pydantic 校验失败 → 立即退出 |
| 连接失败 | 重试 (retry_count=2) → 降级或退出 |
| 注入失败 | 记录错误 → 继续下一个场景或退出 |
| 验证失败 | 警告 → 记录到报告 |
| 进程崩溃 | WAL 回滚 → `--resume` 恢复 |

### 9.2 Recovery Strategies

1. **自动恢复**：Watchdog 超时后自动 `recover_all()`
2. **手动恢复**：`fault-injector --recover-all --session <id>`
3. **断点续传**：`fault-injector --resume <session_id>`

---

## 10. Testing Strategy

### 10.1 Unit Tests

- 配置解析测试 (`tests/config/`)
- Channel mock 测试 (`tests/channel/`)
- 场景逻辑测试 (`tests/scenario/`)
- 回滚日志测试 (`tests/common/test_rollback.py`)
- 安全守卫测试 (`tests/common/test_safety.py`)

### 10.2 Integration Tests

- SSH Channel 集成测试
- Redfish Channel 集成测试
- Switch NETCONF 集成测试

### 10.3 End-to-End Tests

- 完整场景流程测试 (dry-run 模式)
- `--resume` 恢复测试
- Load Simulator 联动测试

---

## 11. Deployment

### 11.1 Environments

| Environment | 用途 | 配置 |
|-------------|------|------|
| dev | 开发测试 | 本地 YAML 配置 |
| staging | 预生产验证 | 真实 BMC/交换机连接 |
| production | 生产演练 | 仅 dry-run 模式 |

### 11.2 Configuration

```yaml
# fault-injector-config.yaml
global:
  cube_studio_url: "http://cube-studio.example.com"
  prometheus_url: "http://prometheus-k8s.monitoring:9090"
  safety:
    require_confirmation: true
    max_concurrent_faults: 3
    auto_recover_timeout: 600
    dry_run: false

inventory:
  spoke1:
    compute_nodes:
      - name: "gpu-1-1"
        ssh: { host: "10.1.1.1", user: "root" }
        bmc: { host: "10.1.100.1", user: "admin" }

scenarios:
  mandatory:
    vllm_latency_instability:
      enabled: true
      root_causes: ["gpu_contention", "network_jitter"]
```

---

## 12. Project Structure

```
fault_injector/
├── __main__.py               # python -m fault_injector 入口
├── cli.py                    # Click CLI
├── config/
│   ├── schema.py             # Pydantic 配置模型
│   ├── loader.py             # YAML 加载 + 校验
│   └── defaults.py           # 默认值常量
├── orchestrator/
│   ├── engine.py             # 主执行引擎
│   ├── session.py            # Session 状态管理
│   ├── scheduler.py          # 故障时序调度
│   └── watchdog.py           # 自动恢复看门狗
├── agents/
│   ├── base.py               # BaseAgent ABC
│   ├── hardware.py           # HardwareFaultAgent
│   ├── os_fault.py           # OSFaultAgent
│   ├── platform.py           # PlatformFaultAgent
│   ├── service.py            # ServiceFaultAgent
│   └── monitor.py            # MonitorAgent
├── channels/
│   ├── base.py               # BaseChannel ABC
│   ├── ssh.py                # SSHChannel
│   ├── redfish.py            # RedfishChannel
│   ├── switch.py             # SwitchChannel
│   ├── kubernetes.py         # K8sChannel
│   └── prometheus.py         # PrometheusChannel
├── scenarios/
│   ├── base.py               # BaseScenario ABC
│   ├── registry.py           # 场景注册表
│   ├── vllm_latency.py       # RC-1 ~ RC-6
│   ├── rdma_anomaly.py       # F-1 ~ F-6
│   ├── hardware.py           # 硬件扩展场景
│   ├── os_fault.py           # OS 扩展场景
│   ├── platform.py           # 平台扩展场景
│   └── service.py            # 服务扩展场景
├── safety/
│   ├── guard.py              # SafetyGuard
│   └── rollback.py           # RollbackJournal (WAL)
├── reporting/
│   └── __init__.py           # 报告生成 (Planned)
└── tests/
    ├── config/
    ├── channel/
    ├── scenario/
    ├── common/
    └── integration/
```

---

## 13. Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-02-27 | 1.0 | AIDC Auto-SRE Team | 基于 fault-injector.md 设计规格更新，反映实现状态 |