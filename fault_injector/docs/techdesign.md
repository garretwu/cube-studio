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
| Version | 1.3 |
| Status | Implemented |
| Last Updated | 2026-02-28 |
| Author | AIDC Auto-SRE Team |

---

## 1. Overview

### 1.1 System Purpose

Fault Injector �?Cube Studio 平台的多维度故障注入系统，采�?**确定性编�?+ LLM 诊断** 的混合架构：

- **确定性编�?*：故障注�?恢复由确定�?Python 引擎执行，安全可控可重放
- **LLM 诊断**：故障诊断分析由 MiniMax-2.1 提供智能推理（只读分析，不执行操作）

系统能够�?
- 在硬件层通过 BMC Redfish 和交换机 API 注入 CPU/GPU/内存/网络故障
- �?OS 层注入内核、文件系统、进程级故障
- 在平台层注入 K8s 组件、数据库、缓存、消息队列故�?
- 在服务层注入推理服务、训�?Pipeline、Notebook 故障
- �?Load Simulator 联动，通过过载制造故�?

### 1.2 Design Goals

1. **安全第一**：所有故障注入操作可恢复，WAL 写前日志保证
2. **确定性执�?*：核心执行路径无 LLM 调用，精确可控可审计
3. **异步优先**：所�?I/O 操作使用 asyncio，非阻塞执行
4. **Channel 抽象**：统一封装 SSH/Redfish/K8s/交换机等后端
5. **场景驱动**：故障逻辑封装为自包含�?Scenario �?

### 1.3 Scope

**In Scope:**
- 硬件/OS/平台/服务四层故障注入
- 必选场景：vLLM 延迟不稳定（6 �?root cause）、RDMA 异常�? 个子场景�?
- WAL 回滚恢复机制
- CLI 工具�?YAML 配置

**Out of Scope:**
- 生产环境实时故障注入
- BMC 网络配置变更（安全限制）
- LLM 直接执行故障注入（仅用于诊断�?

---

## 2. Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────�?
�?                       CLI (Click)                          �?
�? run / validate-config / list-scenarios / recover          �?
└─────────────────────────┬───────────────────────────────────�?
                          �?
┌─────────────────────────▼───────────────────────────────────�?
�?                   Orchestrator                             �?
�? (asyncio 确定性调�?                                        �?
�? · 阶段管理: baseline �?inject �?observe �?recover �?verify �?
�? · 并发控制: max_concurrent_faults                          �?
�? · 回滚看门�? auto_recover_timeout                         �?
└─────────────────────────┬───────────────────────────────────�?
                          �?
        ┌────────┬────────┼────────┬────────┬────────�?
        �?       �?       �?       �?       �?       �?
  ┌─────▼────�?┌─▼───�?┌──▼────�?┌──▼────�?┌──▼────�?┌─▼─────�?
  �?Hardware �?�?OS  �?│Platform�?│Service�?│Monitor�?│Diag-  �?
  �? Fault   �?│Fault�?�?Fault  �?�?Fault �?�?Agent �?│nosis  �?
  �? Agent   �?│Agent�?�?Agent  �?�?Agent �?�?      �?│Agent  �?
  �?(确定)   �?�?确定)�?�?(确定) �?�?(确定) �?�?(确定) �?�?LLM)  �?
  └────┬─────�?└──┬──�?└──┬────�?└──┬────�?└───┬────�?└──┬───�?
       �?         �?      �?         �?          �?        �?
       └──────────┴───────┴──────────┴───────────�?        �?
                          �?                                �?
                 ┌────────▼────────�?           ┌──────────▼──────────�?
                 �? Channel �?    �?           �? Load Simulator     �?
                 �? (类型化执行后�? �?           �? (联动压测)         �?
                 └────────┬────────�?           └─────────────────────�?
                          �?
┌─────────────────────────▼───────────────────────────────────�?
�?                   Safety �?                               �?
�? · SafetyGuard (禁止操作拦截)                                �?
�? · RollbackJournal (WAL 回滚日志)                           �?
└─────────────────────────────────────────────────────────────�?
```

### 2.2 Component Overview

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| CLI | Command-line entrypoints | Click |
| Orchestrator (FaultOrchestrator) | Scenario execution flow, session state, rollback | Python asyncio |
| Scenarios | Fault scenario definitions and lifecycle | Python (BaseScenario subclasses) |
| Channels (shared) | Typed execution backends | lib/channels (asyncssh, httpx, kubernetes client) |
| Safety | Guardrails + WAL rollback | Python |
| Reporting | Report generation | Planned |
| Diagnosis Agent | LLM analysis | Planned |

### 2.3 Data Flow

```
YAML 配置 �?Pydantic 校验 �?Orchestrator 调度
    �?
Session 初始�?�?Channel 连接�?�?WAL 初始�?
    �?
基线采集 (Prometheus) �?故障注入 (Channel.execute)
    �?
观测�?(指标采集) �?恢复 (WAL 回滚)
    �?
验证 (指标对比) �?报告生成
```

---

## 3. Detailed Design

### 3.1 Module: orchestrator/engine.py

**Purpose:** 主执行引擎，管理故障注入的完整生命周�?

**Key Classes:**

```python
class FaultOrchestrator:
    async def run(self) -> Session:
        """Execute the full fault injection lifecycle."""
        ...
```

**Dependencies:**
- Session (state persistence)
- Channels (execution backends in lib/channels)
- RollbackJournal (WAL)
- PrometheusChannel (baseline and observation metrics)

**Error Handling:**
- 任何阶段异常 �?触发 `rollback.recover_all()`
- 超时 �?Watchdog 自动恢复
- Channel 连接失败 �?重试或降�?

---

### 3.2 Module: orchestrator/session.py

**Purpose:** Session 状态管理和持久�?

**Key Classes:**

```python
@dataclass
class Session:
    session_id: str
    config_hash: str
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "completed", "failed", "recovered", "paused"]
    phase: Literal["init", "baseline", "inject", "observe", "recover", "verify", "report"]
    active_faults: list[ActiveFault]
    rollback_journal_path: str
    events: list[dict[str, Any]]
    scenario_results: dict[str, ScenarioResult]
    baseline_metrics: dict[str, Any]
```

**Session 目录结构:**

```
fault-reports/sessions/{session_id}/
  session.json          # Session state and results
  rollback.jsonl        # WAL rollback journal
  # report/             # Planned
  # metrics/            # Planned
```

---

### 3.3 Module: scenarios/base.py

**Purpose:** 场景基类，定义标准生命周期接�?

**Key Classes:**

```python
class BaseScenario(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    @abstractmethod
    def layer(self) -> str:
        ...

    @abstractmethod
    async def inject(self, ctx: FaultContext) -> InjectResult:
        ...

    @abstractmethod
    def monitor_queries(self) -> dict[str, str]:
        ...

    @abstractmethod
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        ...

    @abstractmethod
    async def verify(self, ctx: FaultContext) -> bool:
        ...
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

**Purpose:** vLLM 延迟不稳定的 6 �?root cause 场景

**Implemented Scenarios:**

| Class | Scenario ID | Description |
|-------|-------------|-------------|
| `GPUContentionScenario` | RC-1 | GPU 资源争抢 |
| `NetworkJitterScenario` | RC-2 | 网络链路抖动 |
| `StorageIOInterferenceScenario` | RC-3 | 存储 I/O 干扰 |
| `PlatformCascadeScenario` | RC-4 | 平台组件级联延迟 |
| `OSResourcePressureScenario` | RC-5 | OS 资源压力 |
| `ThermalThrottlingScenario` | RC-6 | 热降�?|

**Example Implementation:**

```python
class GPUContentionScenario(BaseScenario):
    """RC-1: GPU 资源争抢导致 vLLM 延迟不稳�?""
    
    @property
    def name(self) -> str:
        return "gpu_contention"
    
    @property
    def description(self) -> str:
        return "GPU 资源争抢导致 vLLM 推理延迟不稳�?
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        
        # 在推理服务所在节点启�?GPU 满载
        await ctx.ssh.run_command(
            ctx.target_node,
            f"gpu-burn -d {duration}",
            recovery_command="pkill -f gpu-burn"
        )
        
        # 可�? 显存压力
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
        # WAL 已自动处�?pkill
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

**Purpose:** RDMA 网络异常�?6 个子场景

**Implemented Scenarios:**

| Class | Scenario ID | Description |
|-------|-------------|-------------|
| `PFCDeadlockScenario` | F-1 | PFC 死锁 |
| `ECNMisconfigurationScenario` | F-2 | ECN 阈值错�?|
| `RDMALoadImbalanceScenario` | F-3 | RDMA 负载不均�?|
| `RDMALinkFlapScenario` | F-4 | RDMA 链路间歇中断 |
| `RoCEMTUMismatchScenario` | F-5 | RoCE MTU 不匹�?|
| `RDMAQoSDowngradeScenario` | F-6 | RDMA QoS 降级 |

---

## 4. Data Models

### 4.1 Entity Relationship

```
┌─────────────�?      ┌─────────────�?
�?  Session   │──1:N──�?  Scenario  �?
�?            �?      �?  Result    �?
└──────┬──────�?      └─────────────�?
       �?
       �?1:1
       �?
┌─────────────�?      ┌─────────────�?
�? Rollback   │──1:N──�?  Rollback  �?
�? Journal    �?      �?   Entry    �?
└─────────────�?      └─────────────�?
```

### 4.2 Schema Definitions

```python
class SafetyConfig(BaseModel):
    require_confirmation: bool = True
    auto_recover_timeout: int = 600
    dry_run: bool = False
    max_concurrent_faults: int = 3
    excluded_nodes: list[str] = []

class GlobalConfig(BaseModel):
    session_dir: str = "./fault-reports/sessions/"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    safety: SafetyConfig

class ScenarioConfig(BaseModel):
    name: str
    enabled: bool = True
    target_nodes: list[str] = []
    params: dict[str, Any] = {}

class FaultInjectorConfig(BaseModel):
    global_: GlobalConfig
    inventory: dict[str, list[TargetNodeConfig]] = {}
    scenarios: dict[str, ScenarioConfig] = {}
    config_hash: str = ""

class InjectResult(BaseModel):
    success: bool
    fault_id: str = ""
    error: str | None = None

class RecoverResult(BaseModel):
    success: bool
    fault_id: str = ""
    error: str | None = None

class ScenarioResult(BaseModel):
    scenario_name: str
    success: bool
    verification_passed: bool | None = None
    error: str | None = None
    metrics: dict[str, Any] = {}

class RollbackEntry(BaseModel):
    fault_id: str
    channel: str
    target: str
    inject_action: str
    inject_params: dict = {}
    recover_action: str
    recover_params: dict = {}
    status: Literal["active", "recovered", "failed"]
```

---

## 5. API Design

### 5.1 CLI Interface

```bash
# standard run
fault-injector run --config config.yaml

# run a specific scenario
fault-injector run --config config.yaml --scenario gpu_contention

# dry-run (no injection)
fault-injector run --config config.yaml --dry-run

# list available scenarios
fault-injector list-scenarios

# validate a YAML config
fault-injector validate-config config.yaml

# recover active faults from a session
fault-injector recover --session <session_id> --session-dir ./fault-reports/sessions/
```

### 5.2 Channel Interface

```python
class BaseChannel(ABC):
    """Channel 基类 �?统一 dry_run �?WAL 集成"""
    
    def __init__(self, dry_run: bool = False,
                 wal: RollbackJournal | None = None):
        self.dry_run = dry_run
        self.wal = wal
    
    async def execute(self, action: str, params: dict,
                      recovery_action: str | None = None,
                      recovery_params: dict | None = None) -> ChannelResult:
        # 1. 禁止操作检�?
        if self._is_forbidden(action, params):
            raise SafetyViolationError(f"{action} is forbidden")
        
        # 2. 写操�?�?WAL 记录
        if recovery_action and self.wal:
            self.wal.record(action, recovery_action, recovery_params)
        
        # 3. dry_run �?仅日�?
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
}
```

---

## 6. Design Decisions

### Decision 1: 确定性编�?+ LLM 诊断

**Context:** 故障注入是破坏性操作，需要精确可控�?

**Decision:** 采用混合架构�?
- 编排 + 故障 Agent：Python + asyncio（确定性）
- 诊断 Agent：MiniMax-2.1（LLM�?

**Rationale:**
- LLM 幻觉可能生成错误�?shell 命令，对生产基础设施造成不可逆损�?
- 根因推断和传播链分析才是真正需要推理能力的任务
- Diagnosis Agent 是只读分析，即使 LLM 出错也不会造成系统损害

**Alternatives Considered:**
1. �?LLM 驱动 �?拒绝，不可控风险
2. 全规则驱�?�?拒绝，缺乏智能分析能�?

**Consequences:**
- 优点：安全可控，兼具智能分析能力
- 缺点：需要维护两套逻辑（确定�?+ LLM�?

---

### Decision 2: WAL (Write-Ahead Log) 回滚机制

**Context:** 故障注入后进程崩溃，需要能恢复到正常状态�?

**Decision:** 采用 WAL 模式，恢复操作在注入**之前**持久化到磁盘�?

**Rationale:**
- 只要 rollback.jsonl 未损坏，所有已注入故障均可恢复
- JSONL 格式确保即使在追加写入中途崩溃，已写入的条目仍然有效

**Implementation:**

```python
class RollbackJournal:
    def record(self, fault_id: str, recover_action: str,
               recover_params: dict) -> None:
        """写前记录：在故障注入之前调用，立�?fsync 到磁�?""
        entry = RollbackEntry(...)
        self.entries.append(entry)
        self._append_to_disk(entry)   # JSONL append + fsync
    
    async def recover_all(self) -> list[RecoveryResult]:
        """按注入逆序恢复所有活跃故�?""
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

### Decision 3: Channel 抽象�?

**Context:** 需要对接多种后端（SSH/Redfish/K8s/交换�?Prometheus）�?

**Decision:** 统一 Channel 抽象，封装认证、连接池、重试、dry-run、安全守卫�?

**Rationale:**
- �?Load Simulator �?SRE Agent 共享 Channel 实现
- 统一安全策略（禁止操作检查）
- 便于测试�?Mock

**Shared Channels:**

| Channel | 用�?| 共享系统 |
|---------|------|---------|
| SSHChannel | 节点命令执行 | fault-injector, SRE Agent |
| RedfishChannel | BMC 硬件控制 | fault-injector, SRE Agent |
| SwitchChannel | 交换机端�?配置操作 | fault-injector, SRE Agent |
| K8sChannel | Pod/Deployment 故障注入 | fault-injector, SRE Agent |
| PrometheusChannel | 基线采集与偏差检�?| fault-injector, Load Simulator, SRE Agent |
| CubeStudioChannel | 推理服务状态查询与更新 | fault-injector, Load Simulator, SRE Agent |

---

## 7. Security Considerations

### 7.1 Authentication & Authorization

- BMC Redfish: X-Auth-Token session 认证
- K8s: kubeconfig �?ServiceAccount
- SSH: 密钥认证（推荐）或密�?
- Cube Studio: JWT 认证

### 7.2 Data Protection

- 所有密�?密钥通过环境变量引用：`${BMC_PASSWORD}`
- WAL 日志不记录明文密�?
- 敏感操作记录审计日志

### 7.3 Security Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| SSH 命令注入 | `shlex.quote()` 所有参�?|
| BMC 网络修改 | 硬编码禁�?`PATCH .../EthernetInterfaces/{id}` |
| 危险命令执行 | SafetyGuard 白名�?+ 黑名�?|
| 进程崩溃 | WAL 回滚 + `--resume` 恢复 |

### 7.4 禁止操作（FORBIDDEN_OPERATIONS�?

| Channel | 禁止操作 | 原因 |
|---------|----------|------|
| Redfish | BMC 恢复出厂设置 | 不可�?|
| Redfish | 修改 BMC 网口 IP | 可能导致 BMC 不可�?|
| K8s | 删除 namespace | 级联删除所有资�?|
| K8s | 删除 etcd 数据 | 集群不可恢复 |
| SSH | `rm -rf /` 类命�?| 全盘删除 |
| SSH | 修改 SSH 服务配置 | 可能导致节点不可�?|
| Switch | 删除管理 VLAN | 交换机不可达 |
| Switch | 恢复出厂设置 | 所有配置丢�?|

---

## 8. Performance Considerations

### 8.1 Scalability

- 异步 I/O：所�?Channel 操作使用 asyncio
- 并发控制：`max_concurrent_faults` 限制同时注入的故障数
- 连接池：SSH/HTTP 连接复用

### 8.2 Resource Limits

| 配置�?| 默认�?| 说明 |
|--------|--------|------|
| `max_concurrent_faults` | 3 | 同时最多注�?3 个故�?|
| `ssh.pool_size` | 5 | 每节�?SSH 连接池大�?|
| `auto_recover_timeout` | 600s | 超时后自动恢�?|

### 8.3 Performance Requirements

| Metric | Target |
|--------|--------|
| 单场景注入延�?| < 5s |
| 基线采集时长 | 120s（可配置�?|
| 观测期时�?| 300s（可配置�?|
| 恢复验证时长 | 120s（可配置�?|

---

## 9. Error Handling

### 9.1 Error Categories

| Category | Handling |
|----------|----------|
| 配置错误 | Pydantic 校验失败 �?立即退�?|
| 连接失败 | 重试 (retry_count=2) �?降级或退�?|
| 注入失败 | 记录错误 �?继续下一个场景或退�?|
| 验证失败 | 警告 �?记录到报�?|
| 进程崩溃 | WAL 回滚 �?`--resume` 恢复 |

### 9.2 Recovery Strategies

1. **自动恢复**：Watchdog 超时后自�?`recover_all()`
2. **手动恢复**：`fault-injector --recover-all --session <id>`
3. **断点续传**：`fault-injector --resume <session_id>`

---

## 10. Testing Strategy

### 10.1 Unit Tests

- Scenario logic: tests/unit/features/scenarios/
- Channel behaviors: tests/unit/features/channels/
- Common helpers: tests/helpers/
- Fixtures and mocks: tests/fixtures/, tests/mocks/

### 10.2 Integration Tests

- NETCONF connectivity: tests/integration/test_netconf_connection.py
- Switch operations: tests/integration/test_switch_operations.py

### 10.3 End-to-End Tests

- Placeholders live under tests/e2e/ (journeys, page-objects)
- Dry-run flows can be exercised via the CLI

---

## 11. Deployment

### 11.1 Environments

| Environment | 用�?| 配置 |
|-------------|------|------|
| dev | 开发测�?| 本地 YAML 配置 |
| staging | 预生产验�?| 真实 BMC/交换机连�?|
| production | 生产演练 | �?dry-run 模式 |

### 11.2 Configuration

```yaml
# fault-injector-config.yaml
global:
  session_dir: "./fault-reports/sessions/"
  log_level: "INFO"
  safety:
    require_confirmation: true
    auto_recover_timeout: 600
    dry_run: false
    max_concurrent_faults: 3
    excluded_nodes: []

inventory:
  nodes:
    - name: "test-vm-1"
      ssh:
        host: "192.168.1.100"
        port: 22
        user: "root"
        key_file: "~/.ssh/id_rsa"
      redfish:
        bmc_host: "192.168.1.110"
        username: "admin"
        password: "<SECRET>"
        verify_tls: true
        timeout: 30
      interface: "eth0"
      roles: []

scenarios:
  network_jitter:
    enabled: true
    target_nodes: ["test-vm-1"]
    params:
      delay_ms: 50
      jitter_ms: 100
      distribution: "pareto"
      loss_pct: 0
      duration: 60
      interface: "eth0"
```

---

## 12. Project Structure

```
fault_injector/
|-- __init__.py
|-- __main__.py
|-- agents.md
|-- cli.py
|-- fault-injector-test.yaml
|-- testing.md
|-- agent_docs/
|   |-- code_patterns.md
|   |-- product_requirements.md
|   |-- project_brief.md
|   `-- tech_stack.md
|-- agents/
|   |-- __init__.py
|   |-- base.py
|   |-- hardware.py
|   |-- monitor.py
|   |-- os_fault.py
|   |-- platform.py
|   |-- scenario_dispatch.py
|   `-- service.py
|-- config/
|   |-- __init__.py
|   |-- defaults.py
|   |-- loader.py
|   |-- schema.py
|   `-- templates/
|       `-- fault-injector-test.yaml
|-- docs/
|   |-- PRD.md
|   `-- techdesign.md
|-- orchestrator/
|   |-- __init__.py
|   |-- engine.py
|   |-- scheduler.py
|   |-- session.py
|   `-- watchdog.py
|-- reporting/
|   `-- __init__.py
|-- safety/
|   |-- __init__.py
|   |-- guard.py
|   `-- rollback.py
|-- scenarios/
|   |-- __init__.py
|   |-- base.py
|   |-- rdma_anomaly.py
|   |-- registry.py
|   `-- vllm_latency.py
`-- tests/
    |-- __init__.py
    |-- conftest.py
    |-- channel/
    |-- common/
    |-- config/
    |-- e2e/
    |-- fixtures/
    |-- helpers/
    |-- integration/
    |-- mocks/
    |-- mocktest/
    |-- scenario/
    `-- unit/
        `-- features/
            |-- agents/
            |-- channels/
            |-- cli/
            |-- orchestrator/
            `-- scenarios/
```



---

## 13. Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-02-28 | 1.3 | AIDC Auto-SRE Team | Final cleanup: removed deprecated scenario extension modules and trimmed scenario registry to active RC/F families |
| 2026-02-28 | 1.2 | AIDC Auto-SRE Team | Agent-centric orchestrator migration: added layer agents, scheduler, resume recovery, and CLI orchestration path |
| 2026-02-28 | 1.1 | AIDC Auto-SRE Team | Sync docs with current code layout, CLI, schema, and tests |
| 2026-02-27 | 1.0 | AIDC Auto-SRE Team | Initial implementation snapshot |
















