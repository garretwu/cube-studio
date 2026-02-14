# Cube Studio Fault Injector 设计规格

## 1. 概述

### 1.1 目标

为 Cube Studio 平台设计一个多维度故障注入系统，覆盖硬件、OS、平台、服务四个层次。系统采用**确定性编排 + LLM 诊断**的混合架构：故障注入/恢复由确定性 Python 引擎执行（安全可控可重放），故障诊断分析由 MiniMax-2.1 提供智能推理。系统能够：

- 在硬件层通过 BMC Redfish 和交换机 API 注入 CPU / GPU / 内存 / 网络 / 交换机故障
- 在 OS 层注入内核、文件系统、进程级故障
- 在平台层注入 K8s 组件、数据库、缓存、消息队列故障
- 在服务层注入推理服务、训练 Pipeline、Notebook 故障
- 与 Load Simulator 联动，通过过载制造故障
- **必选场景**：制造 vLLM 推理 latency 不稳定（多种 root cause）和 RDMA 网络异常
- 所有故障可配置、可恢复、可组合

### 1.2 系统拓扑

```
                    ┌─────────────────────────────────────────────┐
                    │              HUB 集群                        │
                    │  ┌───────────────────┐ ┌──────────────────┐ │
                    │  │   控制集群 (3节点)  │ │  存储集群 (3节点) │ │
                    │  │  AMD9354×2         │ │  AMD9354×2       │ │
                    │  │  DDR5 192G         │ │  DDR5 256G       │ │
                    │  │  960G NVMe RAID1   │ │  960G NVMe RAID1 │ │
                    │  │  NIC: 25G          │ │  7.68T TLC ×12   │ │
                    │  │                    │ │  NIC: 25G + 200G │ │
                    │  └───────────────────┘ └──────────────────┘ │
                    └──────────────┬──────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │      H3C 交换机网络          │
                    │  200G: H3C S9855-24B8D      │
                    │  25G:  H3C S6850-56HF       │
                    └──────┬──────────────┬───────┘
                           │              │
              ┌────────────▼────┐  ┌──────▼─────────────┐
              │  Spoke1 集群     │  │  Spoke2 集群        │
              │  2 计算节点      │  │  2 计算节点          │
              │  AMD9354×2      │  │  AMD9354×2          │
              │  GPU 5090 ×4    │  │  GPU 5090 ×4        │
              │  DDR5 256G      │  │  DDR5 256G          │
              │  3.84TB TLC     │  │  3.84TB TLC         │
              │  NIC: 25G+200G  │  │  NIC: 25G+200G      │
              └─────────────────┘  └─────────────────────┘
```

### 1.3 BMC 管理接入

所有服务器（控制集群 + 存储集群 + 计算集群共 10 节点）通过 BMC Redfish API 进行带外管理。BMC 型号为 EG8621G4，基于 AST2600 芯片，固件符合 Redfish DSP0266 1.1.0 规范。

---

## 2. 整体架构

### 2.1 设计原则：确定性编排 + LLM 诊断

故障注入是**破坏性操作**，核心执行路径必须确定性可控。LLM 的非确定性（幻觉命令、重试逻辑不稳定）不适合直接驱动故障注入/恢复流程。因此采用**混合架构**：

| 层级 | 技术 | 理由 |
|------|------|------|
| 编排 + 故障 Agent | Python + asyncio（确定性） | 故障注入/恢复必须精确、可重放、可审计 |
| 诊断 Agent | MiniMax-2.1（LLM） | 根因推断、传播链分析、韧性评分需要推理能力 |
| 通道层 | 类型化 Channel 类 | 统一抽象 SSH/Redfish/K8s/交换机等后端 |

**核心不变量**：任何故障注入操作，其恢复命令在注入**之前**写入回滚日志。进程崩溃时可通过日志恢复所有活跃故障。

**回滚幂等性约束**：WAL 回滚仅恢复本 Session（`session_id`）注入的变更，不影响其他用户/进程的配置。Demo 阶段建议在单租户环境下执行，避免因并发注入导致回滚冲突。

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
                          │  · 回滚看门狗         │
                          └─────────┬───────────┘
                                    │
        ┌──────────┬────────┬───────┴───────┬──────────┬──────────┐
        │          │        │               │          │          │
  ┌─────▼────┐ ┌───▼──┐ ┌──▼─────┐ ┌───────▼───┐ ┌────▼───┐ ┌───▼────────┐
  │ Hardware │ │  OS  │ │Platform│ │  Service  │ │Monitor │ │Diagnosis   │
  │  Fault   │ │Fault │ │ Fault  │ │  Fault    │ │ Agent  │ │  Agent     │
  │  Agent   │ │Agent │ │ Agent  │ │  Agent    │ │        │ │(LLM 驱动)  │
  │ 确定性    │ │确定性 │ │ 确定性  │ │  确定性    │ │ 确定性  │ │MiniMax-2.1 │
  └────┬─────┘ └──┬───┘ └──┬────┘ └────┬──────┘ └───┬────┘ └──┬─────────┘
       │          │        │           │             │         │
       │     Channel 层 (类型化执行后端)              │         │
  ┌────▼────┐  ┌──▼──┐ ┌──▼────┐  ┌───▼────┐  ┌────▼───┐    │
  │Redfish  │  │SSH  │ │K8s    │  │Cube    │  │Prometh-│    │
  │Channel  │  │Chan-│ │Channel│  │Studio  │  │eus     │    │
  │         │  │nel  │ │       │  │Channel │  │Channel │    │
  │Switch   │  │     │ │       │  │        │  │        │    │
  │Channel  │  │     │ │       │  │        │  │        │    │
  └────┬────┘  └──┬──┘ └──┬───┘  └───┬────┘  └───┬────┘    │
       │          │       │          │            │          │
       └──────────┴───────┴──────────┴────────────┘          │
                          │                                   │
                 ┌────────▼────────┐              ┌──────────▼──────────┐
                 │  Rollback       │              │  Load Simulator     │
                 │  Journal        │              │  (联动压测制造故障)   │
                 │  (WAL 回滚日志)  │              └─────────────────────┘
                 └─────────────────┘
```

### 2.3 Agent 角色定义

| Agent | 类型 | 职责 | 操作通道 | 输出 |
|-------|------|------|----------|------|
| **Orchestrator** | 确定性 | 解析配置、阶段调度、并发控制、回滚看门狗 | 内部 asyncio | 执行计划、Session 状态 |
| **Hardware Fault Agent** | 确定性 | CPU/GPU/内存/网络/交换机硬件故障 | Redfish Channel, Switch Channel, SSH Channel | 硬件状态变更确认 |
| **OS Fault Agent** | 确定性 | 内核参数、文件系统、进程、网络栈故障 | SSH Channel | OS 层故障确认 |
| **Platform Fault Agent** | 确定性 | K8s 组件、MySQL、Redis、Celery、Istio 故障 | K8s Channel, SSH Channel | 平台组件状态 |
| **Service Fault Agent** | 确定性 | 推理服务、Pipeline、Notebook 应用层故障 | CubeStudio Channel, K8s Channel | 服务状态变更 |
| **Monitor Agent** | 确定性 | 持续采集故障前/中/后指标 | Prometheus Channel, CubeStudio Channel | 时序指标数据 |
| **Diagnosis Agent** | **LLM** | 分析故障传播链、根因定位、韧性评分、生成恢复建议 | MiniMax-2.1 API | 故障诊断报告 |

> **为什么只有 Diagnosis Agent 使用 LLM？**
> - 故障注入/恢复操作是 YAML 配置驱动的确定性流程，不需要"推理"来决定执行什么
> - LLM 幻觉可能生成错误的 shell 命令，对生产基础设施造成不可逆损害
> - 根因推断和传播链分析才是真正需要推理能力的任务 — 从多维时序指标中识别因果关系
> - Diagnosis Agent 是只读分析，即使 LLM 出错也不会造成系统损害

### 2.4 技术栈

| 组件 | 技术选型 | 用途 |
|------|----------|------|
| 编排引擎 | Python 3.9 + asyncio | 确定性故障调度、阶段管理、并发控制 |
| 配置校验 | Pydantic v2 + PyYAML | 类型安全的 YAML 配置解析与验证 |
| 诊断推理 | MiniMax-2.1 (OpenAI-compatible API) | 根因推断、传播链分析、韧性评分 |
| 硬件管理 | httpx (async) → Redfish REST API | BMC 带外操作 (X-Auth-Token 认证) |
| 交换机管理 | Paramiko (SSH CLI) + ncclient (NETCONF) | H3C 交换机配置变更 |
| 远程执行 | asyncssh / Paramiko | OS 级命令执行，连接池管理 |
| K8s 操作 | kubernetes Python client (async) | K8s 资源操作 |
| 平台 API | httpx (async) | Cube Studio REST 调用 (JWT 认证) |
| 指标采集 | httpx → Prometheus HTTP API | PromQL 查询 |
| 报告生成 | Jinja2 + plotly/matplotlib | HTML 报告 + 指标可视化图表 |
| CLI | Click | 命令行接口 |
| 混沌工具 | stress-ng, tc, iptables, fio, gpu-burn | OS/GPU 层故障注入（通过 SSH Channel 调用） |

### 2.5 项目结构

```
fault_injector/
├── __main__.py               # python -m fault_injector 入口
├── cli.py                    # Click CLI (--config, --scenario, --dry-run, --resume 等)
│
├── config/
│   ├── schema.py             # Pydantic 模型: GlobalConfig, InventoryConfig, ScenarioConfig 等
│   ├── loader.py             # YAML 解析 + inventory 节点连通性预检
│   └── defaults.py           # 默认值常量
│
├── orchestrator/
│   ├── engine.py             # 主执行引擎: baseline → inject → observe → recover → verify → report
│   ├── session.py            # Session 状态持久化 (JSON on disk), --resume 支持
│   ├── scheduler.py          # 故障时序调度, combined scenario 阶段编排
│   └── watchdog.py           # 自动恢复看门狗 (auto_recover_timeout)
│
├── agents/
│   ├── base.py               # BaseAgent ABC: inject(), recover(), verify(), status()
│   ├── hardware.py           # HardwareFaultAgent — 组合 Redfish/Switch/SSH Channel
│   ├── os_fault.py           # OSFaultAgent — SSH Channel
│   ├── platform.py           # PlatformFaultAgent — K8s/SSH Channel
│   ├── service.py            # ServiceFaultAgent — CubeStudio/K8s Channel
│   ├── monitor.py            # MonitorAgent — Prometheus/CubeStudio Channel
│   └── diagnosis.py          # DiagnosisAgent — MiniMax-2.1 LLM (唯一 LLM 组件)
│
├── channels/
│   ├── base.py               # BaseChannel ABC: execute(), dry_run 支持, 回滚注册
│   ├── ssh.py                # SSH Channel: Paramiko 连接池, 按节点状态追踪
│   ├── redfish.py            # Redfish Channel: BMC session 认证, HTTPS
│   ├── switch.py             # Switch Channel: H3C SSH CLI + NETCONF (ncclient)
│   ├── kubernetes.py         # K8s Channel: kubernetes Python client 封装
│   ├── cube_studio.py        # CubeStudio Channel: httpx async, JWT 认证
│   └── prometheus.py         # Prometheus Channel: PromQL 查询执行器
│
├── scenarios/
│   ├── base.py               # BaseScenario ABC: inject(), monitor_queries(), recover(), verify()
│   ├── vllm_latency.py       # 6 个 root cause 场景实现 (RC-1 ~ RC-6)
│   ├── rdma_anomaly.py       # 6 个 RDMA 子场景实现 (F-1 ~ F-6)
│   └── registry.py           # 场景名 → 类映射, --scenario CLI 查找
│
├── safety/
│   ├── guard.py              # 硬编码禁止操作、确认门控、并发故障数限制
│   └── rollback.py           # WAL 回滚日志: 写前记录恢复步骤, 崩溃恢复
│
├── reporting/
│   ├── timeline.py           # 事件时间线构建器
│   ├── html_report.py        # Jinja2 HTML 报告生成
│   ├── charts.py             # plotly/matplotlib 指标可视化
│   └── resilience.py         # 韧性评分算法
│
└── tests/
    ├── test_config.py         # 配置解析测试
    ├── test_channels.py       # Channel mock 测试
    ├── test_scenarios.py      # 场景逻辑测试 (dry-run 模式)
    ├── test_rollback.py       # 回滚日志正确性测试
    └── test_safety.py         # 安全守卫测试
```

### 2.6 Channel 架构

Channel 是实际执行操作的后端抽象。所有 Agent 通过 Channel 执行操作，Channel 负责认证、重试、dry-run 和回滚注册。

#### 2.6.1 Channel 基类

```python
class BaseChannel(ABC):
    def __init__(self, dry_run: bool, rollback_journal: RollbackJournal):
        self.dry_run = dry_run
        self.rollback = rollback_journal

    async def execute(self, action: str, params: dict,
                      recovery_action: str | None = None,
                      recovery_params: dict | None = None) -> ChannelResult:
        """
        执行操作。如果提供了 recovery_action，在执行前将恢复操作写入回滚日志。
        dry_run 模式下只记录日志不实际执行。
        """
        if recovery_action:
            self.rollback.record(action, recovery_action, recovery_params)

        if self.dry_run:
            logger.info(f"[DRY-RUN] {action}: {params}")
            return ChannelResult(success=True, dry_run=True)

        return await self._execute_impl(action, params)

    @abstractmethod
    async def _execute_impl(self, action: str, params: dict) -> ChannelResult:
        """子类实现实际执行逻辑"""
```

#### 2.6.2 SSH Channel

```python
class SSHChannel(BaseChannel):
    """
    Paramiko SSH 连接池管理。
    - 按节点维护持久连接，支持连接复用
    - 命令执行超时控制
    - 自动注册恢复命令到回滚日志
    """
    def __init__(self, inventory: dict, dry_run: bool, rollback: RollbackJournal,
                 pool_size: int = 5, command_timeout: int = 60):
        super().__init__(dry_run, rollback)
        self.inventory = inventory          # node_name → SSHConfig
        self.pool_size = pool_size
        self.command_timeout = command_timeout
        self._pools: dict[str, asyncio.Queue] = {}

    async def run_command(self, node: str, command: str,
                          recovery_command: str | None = None,
                          timeout: int | None = None) -> ExecResult:
        """在目标节点执行命令，可选注册恢复命令"""
        ...

    async def run_stress_ng(self, node: str, stress_type: str,
                            params: dict, duration: int) -> ExecResult:
        """封装 stress-ng 调用，自动注册 kill 为恢复操作"""
        cmd = f"stress-ng --{stress_type} {params} --timeout {duration}"
        return await self.run_command(node, cmd,
            recovery_command=f"pkill -f 'stress-ng --{stress_type}'")

    async def run_tc_netem(self, node: str, interface: str,
                           delay_ms: int, jitter_ms: int = 0,
                           loss_pct: float = 0) -> ExecResult:
        """注入网络延迟/丢包，自动注册 tc qdisc del 为恢复"""
        cmd = f"tc qdisc add dev {interface} root netem delay {delay_ms}ms {jitter_ms}ms"
        if loss_pct > 0:
            cmd += f" loss {loss_pct}%"
        return await self.run_command(node, cmd,
            recovery_command=f"tc qdisc del dev {interface} root")
```

#### 2.6.3 Redfish Channel

```python
class RedfishChannel(BaseChannel):
    """
    BMC Redfish REST API 客户端。
    - X-Auth-Token session 认证
    - 自动 token 刷新
    - 安全守卫: 禁止修改 BMC 网口 IP
    """
    FORBIDDEN_PATHS = [
        "/Managers/Self/Actions/Manager.RestoreFactory",
    ]

    async def authenticate(self, bmc_ip: str) -> str:
        """POST /redfish/v1/SessionService/Sessions → X-Auth-Token"""
        ...

    async def get_thermal(self, bmc_ip: str) -> ThermalData:
        """GET /redfish/v1/Chassis/Self/Thermal"""
        ...

    async def set_fan_control(self, bmc_ip: str, fan_index: int,
                              mode: str, pwm: int) -> ChannelResult:
        """
        PATCH /redfish/v1/Chassis/Self/Thermal/ThermalManagement
        自动注册 FanControlMode=Auto 为恢复操作
        """
        ...

    async def reset_system(self, bmc_ip: str, reset_type: str) -> ChannelResult:
        """POST /redfish/v1/Systems/Self/Actions/ComputerSystem.Reset"""
        ...
```

#### 2.6.4 Switch Channel

```python
class SwitchChannel(BaseChannel):
    """
    H3C 交换机操作：SSH CLI + NETCONF。
    - SSH CLI: 适用于 system-view 交互式命令
    - NETCONF (ncclient): 适用于结构化配置变更
    - 每条变更命令自动记录 undo 命令
    """
    async def ssh_cli_execute(self, switch: str,
                              commands: list[str],
                              undo_commands: list[str] | None = None) -> ExecResult:
        """执行 CLI 命令序列，注册 undo 命令为恢复操作"""
        ...

    async def shutdown_port(self, switch: str, interface: str) -> ExecResult:
        """关闭交换机端口，自动注册 undo shutdown"""
        return await self.ssh_cli_execute(switch,
            commands=["system-view", f"interface {interface}", "shutdown", "quit"],
            undo_commands=["system-view", f"interface {interface}", "undo shutdown", "quit"])

    async def netconf_edit(self, switch: str, xml_config: str,
                           undo_xml: str | None = None) -> ExecResult:
        """通过 NETCONF 编辑交换机配置"""
        ...
```

#### 2.6.5 K8s Channel

```python
class K8sChannel(BaseChannel):
    """
    Kubernetes API 操作。
    - 支持多集群 (通过 kubeconfig 切换)
    - 目标命名空间: infra, pipeline, service, jupyter, automl
    - 安全守卫: 禁止删除 namespace
    """
    FORBIDDEN_OPERATIONS = [
        ("delete", "Namespace"),  # 禁止删除命名空间
    ]

    # Cube Studio 使用的标签选择器
    LABEL_SELECTORS = {
        "backend": "app=kubeflow-dashboard",
        "worker": "app=kubeflow-dashboard-worker",
        "beat": "app=kubeflow-dashboard-schedule",
        "inference": "app={service_name}",
        "training_operator": "control-plane=kubeflow-training-operator",
        "istio_ingress": "app=istio-ingress",
    }

    async def delete_pod(self, label_selector: str, namespace: str,
                         cluster: str = "default") -> ExecResult:
        """删除匹配的 Pod（依赖 K8s 重启策略自动恢复）"""
        ...

    async def scale_deployment(self, name: str, namespace: str,
                               replicas: int, cluster: str = "default") -> ExecResult:
        """修改 Deployment 副本数，记录原始值用于恢复"""
        ...

    async def delete_crd(self, crd_name: str) -> ExecResult:
        """删除 CRD（危险操作，需 safety confirmation）"""
        ...
```

#### 2.6.6 CubeStudio Channel

```python
class CubeStudioChannel(BaseChannel):
    """
    Cube Studio REST API 客户端。
    - JWT 或 username 认证 (Authorization header)
    - 目标端点:
        /inferenceservice_modelview/api/ — 推理服务 CRUD
        /pipeline_modelview/api/        — Pipeline CRUD
        /notebook_modelview/api/        — Notebook CRUD
        /k8s/                          — K8s 资源查询
    """
    async def update_inference_service(self, service_name: str,
                                        params: dict) -> ExecResult:
        """POST /inferenceservice_modelview/api/deploy/update/"""
        ...

    async def get_service_status(self, service_name: str) -> dict:
        """GET /inferenceservice_modelview/api/?_filters=[name=...]"""
        ...
```

#### 2.6.7 Prometheus Channel

```python
class PrometheusChannel(BaseChannel):
    """
    Prometheus HTTP API 查询。只读操作，无需回滚。
    """
    async def query_instant(self, promql: str) -> float:
        """GET /api/v1/query?query=..."""
        ...

    async def query_range(self, promql: str, start: datetime,
                          end: datetime, step: str = "15s") -> list[tuple[float, float]]:
        """GET /api/v1/query_range?query=...&start=...&end=...&step=..."""
        ...

    async def collect_baseline(self, queries: dict[str, str],
                               duration: int = 120) -> dict[str, list]:
        """采集基线指标：运行 duration 秒，按 scrape_interval 采样"""
        ...

    async def compare_to_baseline(self, current: dict,
                                  baseline: dict,
                                  threshold: float = 0.1) -> DeviationReport:
        """对比当前指标与基线，返回偏差报告"""
        ...
```

### 2.7 回滚日志 (Rollback Journal)

回滚日志是系统安全的核心机制。采用 **Write-Ahead Log (WAL)** 模式：恢复操作在注入**之前**持久化到磁盘。

#### 2.7.1 数据结构

```python
@dataclass
class RollbackEntry:
    fault_id: str                    # 故障唯一标识
    injected_at: datetime            # 注入时间
    channel: str                     # 执行通道 (ssh / redfish / k8s / switch)
    target: str                      # 目标 (node name / switch name / namespace)
    inject_action: str               # 注入操作描述
    recover_action: str              # 恢复操作
    recover_params: dict             # 恢复参数
    status: Literal["active", "recovered", "failed"]

class RollbackJournal:
    def __init__(self, session_dir: Path):
        self.journal_path = session_dir / "rollback.jsonl"
        self.entries: list[RollbackEntry] = []

    def record(self, fault_id: str, recover_action: str,
               recover_params: dict) -> None:
        """写前记录：在故障注入之前调用，立即 fsync 到磁盘"""
        entry = RollbackEntry(...)
        self.entries.append(entry)
        self._append_to_disk(entry)   # JSONL append + fsync

    async def recover_all(self) -> list[RecoveryResult]:
        """按注入逆序恢复所有活跃故障

        幂等性约束：只回滚本 Session 注入的变更（通过 session_id
        过滤 WAL 条目）。在共享环境中 Demo 时应确保单租户执行，
        避免误回滚其他用户/进程的配置变更。
        """
        active = [e for e in reversed(self.entries) if e.status == "active"]
        results = []
        for entry in active:
            result = await self._execute_recovery(entry)
            entry.status = "recovered" if result.success else "failed"
            results.append(result)
        self._rewrite_journal()       # 更新状态
        return results

    async def auto_recover_watchdog(self, timeout: int) -> None:
        """后台协程：超时后自动恢复所有故障"""
        await asyncio.sleep(timeout)
        logger.warning(f"Auto-recover timeout ({timeout}s) reached, recovering all faults")
        await self.recover_all()
```

#### 2.7.2 崩溃恢复语义

```
进程崩溃时的恢复流程:

1. 用户执行: fault-injector --resume <session_id>
2. 加载 session_dir/rollback.jsonl
3. 扫描所有 status="active" 的条目
4. 按注入逆序执行恢复操作
5. 更新条目状态
6. 报告恢复结果

保证: 只要 rollback.jsonl 未损坏，所有已注入故障均可恢复。
JSONL 格式确保即使在追加写入中途崩溃，已写入的条目仍然有效。
```

### 2.8 Scenario 模式

每个故障场景是自包含的类，实现标准生命周期接口：

```python
class BaseScenario(ABC):
    """故障场景基类"""

    @abstractmethod
    async def inject(self, ctx: FaultContext) -> InjectResult:
        """注入故障。Channel 调用在此方法中组合。"""

    @abstractmethod
    def monitor_queries(self) -> dict[str, str]:
        """返回监控期间需要查询的 PromQL 映射"""

    @abstractmethod
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        """恢复故障。通常由回滚日志自动处理，此方法用于额外清理。"""

    @abstractmethod
    async def verify(self, ctx: FaultContext) -> bool:
        """验证恢复是否成功：指标是否回到基线水平"""


class FaultContext:
    """传递给 Scenario 的执行上下文"""
    ssh: SSHChannel
    redfish: RedfishChannel
    switch: SwitchChannel
    k8s: K8sChannel
    cube_studio: CubeStudioChannel
    prometheus: PrometheusChannel
    target_node: str
    params: dict
    baseline: dict[str, float]      # 基线指标
    rollback: RollbackJournal
```

#### 场景实现示例

```python
class VLLMGpuContention(BaseScenario):
    """RC-1: GPU 资源争抢导致 vLLM 延迟不稳定"""

    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        # 在推理服务所在节点启动 GPU 满载
        await ctx.ssh.run_command(ctx.target_node,
            f"gpu-burn -d {duration}",
            recovery_command="pkill -f gpu-burn")
        # 可选: 显存压力
        if ctx.params.get("mem_pressure", False):
            await ctx.ssh.run_command(ctx.target_node,
                "python3 /opt/fault-injector/scripts/gpu_mem_pressure.py "
                f"--gpu 0 --alloc {ctx.params.get('alloc_pct', 60)}%",
                recovery_command="pkill -f gpu_mem_pressure")
        return InjectResult(success=True)

    def monitor_queries(self) -> dict[str, str]:
        return {
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "gpu_util": 'DCGM_FI_DEV_GPU_UTIL{node="$node"}',
            "gpu_mem_used": 'DCGM_FI_DEV_FB_USED{node="$node"}',
        }

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        # 回滚日志已自动处理 pkill，此处做额外清理
        await ctx.ssh.run_command(ctx.target_node, "nvidia-smi --gpu-reset 2>/dev/null || true")
        return RecoverResult(success=True)

    async def verify(self, ctx: FaultContext) -> bool:
        p50 = await ctx.prometheus.query_instant(self.monitor_queries()["inference_p50"])
        baseline_p50 = ctx.baseline.get("inference_p50", 0)
        if baseline_p50 == 0:
            return True
        return abs(p50 - baseline_p50) / baseline_p50 < 0.15  # 15% 容差
```

#### 场景注册表

```python
# scenarios/registry.py
SCENARIO_REGISTRY: dict[str, type[BaseScenario]] = {
    # vLLM Latency 必选场景
    "gpu_contention":          VLLMGpuContention,
    "network_jitter":          VLLMNetworkJitter,
    "storage_io_interference": VLLMStorageIO,
    "platform_cascade":        VLLMPlatformCascade,
    "os_resource_pressure":    VLLMOSPressure,
    "thermal_throttling":      VLLMThermalThrottle,
    # RDMA 必选场景
    "pfc_deadlock":            RDMAPfcDeadlock,
    "ecn_misconfiguration":    RDMAEcnMisconfig,
    "rdma_load_imbalance":     RDMALoadImbalance,
    "rdma_link_flap":          RDMALinkFlap,
    "roce_mtu_mismatch":       RoCEMtuMismatch,
    "rdma_qos_downgrade":      RDMAQosDowngrade,
    # 硬件层
    "cpu_stress":              CPUStress,
    "gpu_removal":             GPURemoval,
    "thermal_throttle":        ThermalThrottle,
    "network_delay":           NetworkDelay,
    # OS 层
    "memory_pressure":         MemoryPressure,
    "disk_full":               DiskFull,
    "time_skew":               TimeSkew,
    # 平台层
    "mysql_connection_drop":   MySQLConnectionDrop,
    "redis_unavailable":       RedisUnavailable,
    "celery_worker_kill":      CeleryWorkerKill,
    "istio_gateway_kill":      IstioGatewayKill,
    # 服务层
    "inference_pod_kill":      InferencePodKill,
    "pipeline_workflow_cancel":PipelineWorkflowCancel,
    "notebook_pod_kill":       NotebookPodKill,
}
```

### 2.9 Session 状态与持久化

#### 2.9.1 Session 数据结构

```python
@dataclass
class Session:
    session_id: str                              # UUID
    config_hash: str                             # YAML 配置文件 SHA256
    started_at: datetime
    status: Literal["running", "paused", "completed", "failed"]
    phase: Literal["init", "baseline", "inject", "observe", "recover", "verify", "report"]
    active_faults: list[ActiveFault]             # 当前活跃的故障列表
    rollback_journal_path: str                   # 回滚日志文件路径
    metrics_baseline: dict[str, list[float]]     # 基线指标快照
    events: list[Event]                          # 完整事件时间线
    scenario_results: dict[str, ScenarioResult]  # 每个场景的执行结果
```

#### 2.9.2 持久化与恢复

```
Session 目录结构:
fault-reports/sessions/{session_id}/
├── session.json          # Session 元数据 (阶段、状态、活跃故障)
├── rollback.jsonl        # WAL 回滚日志 (append-only)
├── baseline.json         # 基线指标数据
├── events.jsonl          # 事件时间线 (append-only)
├── metrics/              # 观测期指标数据
│   ├── {timestamp}.json
│   └── ...
└── report/               # 最终报告
    ├── report.html
    ├── report.json
    └── charts/

恢复流程 (--resume <session_id>):
1. 加载 session.json 确定中断阶段
2. 加载 rollback.jsonl 确定活跃故障
3. 如果 phase ∈ {inject, observe}: 先恢复所有活跃故障，再决定是否继续
4. 如果 phase ∈ {recover, verify}: 继续恢复/验证流程
5. 如果 phase = report: 重新生成报告
```

---

## 3. Hardware Fault Agent — 硬件故障注入

### 3.1 BMC Redfish 接口

基于 EG8621G4 BMC Redfish 接口说明书 V1.0。

#### 3.1.1 认证

```python
# 获取 X-Auth-Token
POST https://{bmc_ip}/redfish/v1/SessionService/Sessions
Body: {
    "UserName": "admin",
    "Password": "${BMC_PASSWORD}"
}
# 响应 Header 中取 X-Auth-Token，后续请求携带:
Headers: { "X-Auth-Token": "<token>" }
```

#### 3.1.2 可用 Redfish 端点

| 资源 | URI | 用途 |
|------|-----|------|
| 系统信息 | `/redfish/v1/Systems/Self` | 查询/控制整机状态 |
| 处理器 | `/redfish/v1/Systems/Self/Processors/{id}` | CPU 状态查询 |
| 内存 | `/redfish/v1/Systems/Self/Memory/{id}` | 内存信息查询 |
| 存储 | `/redfish/v1/Systems/Self/Storage/{id}` | 磁盘状态查询 |
| 硬盘 | `/redfish/v1/Systems/Self/Storage/{id}/Drives/{drive_id}` | 单盘状态查询 |
| 机箱 | `/redfish/v1/Chassis/Self` | 机箱物理信息 |
| 电源 | `/redfish/v1/Chassis/Self/Power` | 电源/电压/功率 |
| 散热 | `/redfish/v1/Chassis/Self/Thermal` | 风扇/温度传感器 |
| 风扇控制 | `/redfish/v1/Chassis/Self/Thermal/ThermalManagement` | 风扇转速控制 |
| BMC 管理 | `/redfish/v1/Managers/Self` | BMC 信息与操作 |
| BMC 网口 | `/redfish/v1/Managers/Self/EthernetInterfaces/{id}` | BMC 网络配置 |
| PCIe 设备 | `/redfish/v1/Chassis/Self/PCIeDevices/{id}` | GPU/NIC PCIe 信息 |
| 网络适配器 | `/redfish/v1/Chassis/Self/NetworkAdapters/{id}` | NIC 信息 |
| 日志服务 | `/redfish/v1/Systems/Self/LogServices/{id}/Entries` | 硬件事件日志 |
| 系统重置 | `/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset` | 电源控制 |
| BMC 重置 | `/redfish/v1/Managers/Self/Actions/Manager.Reset` | BMC 重启 |

#### 3.1.3 电源控制 Action

```python
# 系统电源操作
POST https://{bmc_ip}/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset
Body: { "ResetType": "<type>" }

# 支持的 ResetType:
#   On              — 开机
#   ForceOff        — 强制关机
#   ForceRestart    — 强制重启
#   GracefulShutdown— 优雅关机
#   ForcePowerCycle — 强制电源循环
#   Nmi             — 发送 NMI 中断

# BMC 重启
POST https://{bmc_ip}/redfish/v1/Managers/Self/Actions/Manager.Reset
```

### 3.2 CPU 故障注入

| 故障场景 | 注入方法 | 通道 | 可恢复性 |
|----------|----------|------|----------|
| CPU 过载 | `stress-ng --cpu N --cpu-load P --timeout T` | SSH | 自动（超时恢复） |
| CPU 单核绑定 | `taskset -cp 0 <pid>` 将关键进程绑定到单核 | SSH | 手动解除 |
| CPU 频率限制 | `cpupower frequency-set --max F` | SSH | 手动恢复 |
| NUMA 不均衡 | `numactl --cpunodebind=0 --membind=0 <process>` | SSH | 重启进程 |
| CPU 热降频 | 风扇转速降低 → 温度升高 → CPU throttle | Redfish | 恢复风扇转速 |
| 整机重启 | `ComputerSystem.Reset` (ForceRestart) | Redfish | 自动 |
| NMI 注入 | `ComputerSystem.Reset` (Nmi) | Redfish | 需排查 |

#### 风扇控制制造热降频

```python
# 查询当前风扇状态
GET https://{bmc_ip}/redfish/v1/Chassis/Self/Thermal/ThermalManagement
# 返回: FanControlMode, FanCurrentPWM, FanSetPWM

# 降低风扇转速（触发 CPU/GPU 热降频）
PATCH https://{bmc_ip}/redfish/v1/Chassis/Self/Thermal/ThermalManagement
Body: {
    "FanSpeedManualControl": [{
        "FanControlMode": "Manual",
        "FanIndex": 1,
        "FanSetPWM": 20      # 降至 20%（正常约 40-80%）
    }]
}

# 监控温度传感器
GET https://{bmc_ip}/redfish/v1/Chassis/Self/Thermal
# 关注: Temperatures[].ReadingCelsius vs UpperThresholdCritical

# 恢复
PATCH ... Body: { "FanSpeedManualControl": [{"FanControlMode": "Auto", ...}] }
```

### 3.3 GPU 故障注入

针对 NVIDIA RTX 5090 ×4（每计算节点），智算数据中心常见 GPU 故障模式：

| 故障场景 | 注入方法 | 通道 | 影响 |
|----------|----------|------|------|
| GPU 显存压力 | `gpu-burn` 或 `cuda-memtest` 填满显存 | SSH | OOM、推理失败 |
| GPU 计算饱和 | `gpu-burn -d <seconds>` 全 SM 持续 GEMM 运算 | SSH | 推理延迟飙升 |
| GPU ECC 错误模拟 | `nvidia-smi --ecc-config=0` 关闭 ECC → 注入位翻转 | SSH | 计算结果异常 |
| GPU 热降频 | 风扇降速（Redfish）+ GPU 满载 | Redfish+SSH | 频率下降、延迟升高 |
| GPU 掉卡 | `echo 1 > /sys/bus/pci/devices/{bdf}/remove` | SSH | 设备消失 |
| GPU 驱动卸载 | `rmmod nvidia` | SSH | 所有 GPU 任务失败 |
| GPU 部分不可用 | NVIDIA Device Plugin 修改 → 减少可调度 GPU | K8s API | 调度失败 |
| NVLink 降级 | `nvidia-smi nvlink --status` 检查后 disable link | SSH | 多卡通信降速 |
| GPU PCIe 信息 | `GET /redfish/v1/Chassis/Self/PCIeDevices/{gpu_bdf}` | Redfish | 监控 PCIe 状态 |
| Xid 错误注入 | 人为触发 GPU 超频或异常负载诱发 Xid 事件 | SSH | 驱动报错、任务中断 |

#### GPU 掉卡与恢复

```bash
# 注入：移除 GPU（BDF 示例 0000:41:00.0）
echo 1 > /sys/bus/pci/devices/0000:41:00.0/remove

# 验证
nvidia-smi   # 应少一张卡

# 恢复：PCIe 总线重新扫描
echo 1 > /sys/bus/pci/rescan
nvidia-smi   # 卡重新出现
```

#### GPU 显存 OOM 场景

```bash
# 持续分配显存直到 OOM
python3 -c "
import torch
tensors = []
try:
    while True:
        tensors.append(torch.zeros(1024, 1024, 1024, dtype=torch.float16, device='cuda:0'))
except RuntimeError as e:
    print(f'OOM triggered: {e}')
    import time; time.sleep(300)  # 保持占用 5 分钟
"
```

### 3.4 内存故障注入

| 故障场景 | 注入方法 | 通道 | 可恢复性 |
|----------|----------|------|----------|
| 内存压力 | `stress-ng --vm N --vm-bytes P% --timeout T` | SSH | 自动超时 |
| 内存泄漏模拟 | `stress-ng --vm-hang 0 --vm 1 --vm-bytes 80%` | SSH | Kill 进程 |
| OOM Killer 触发 | 将进程 cgroup memory.limit 设低 | SSH | 自动（OOM Kill） |
| NUMA 不均衡 | `numactl --membind=0` 强制单 NUMA 分配 | SSH | 重启进程 |
| 内存带宽饱和 | `stress-ng --stream N --timeout T` | SSH | 自动超时 |
| 内存信息查询 | `GET /redfish/v1/Systems/Self/Memory/{dimm_id}` | Redfish | 只读监控 |

### 3.5 网络故障注入

| 故障场景 | 注入方法 | 通道 | 可恢复性 |
|----------|----------|------|----------|
| 网络延迟 | `tc qdisc add dev eth0 root netem delay Xms Yms` | SSH | `tc qdisc del` |
| 丢包 | `tc qdisc add dev eth0 root netem loss X%` | SSH | `tc qdisc del` |
| 带宽限制 | `tc qdisc add dev eth0 root tbf rate Xmbit` | SSH | `tc qdisc del` |
| 网络分区 | `iptables -A INPUT -s <target_ip> -j DROP` | SSH | `iptables -D` |
| DNS 故障 | `echo "nameserver 127.0.0.1" > /etc/resolv.conf` | SSH | 恢复原文件 |
| 网口 Down | `ip link set eth0 down` | SSH | `ip link set eth0 up` |
| MTU 错配 | `ip link set dev eth0 mtu 1400` | SSH | 恢复原 MTU |
| ~~BMC 网络修改~~ | ~~`PATCH .../EthernetInterfaces/{id}`~~ | ~~Redfish~~ | ~~PATCH 恢复~~ | **已移除：与安全策略冲突，见 13.2** |

#### BMC 网络配置变更（Redfish）— 仅保留设计，不纳入 Demo 演示

> **安全冲突说明：** BMC 网络修改可能导致 BMC 不可达（需物理接入恢复），与 13.2 节不可执行操作中"修改 BMC 网口 IP 地址"的硬编码拦截规则冲突。**Demo 阶段不演示此场景**，仅保留设计供长期产品化评估。

```python
# 查询 BMC 网口（只读，可用于诊断）
GET https://{bmc_ip}/redfish/v1/Managers/Self/EthernetInterfaces
# 返回: eth0, eth1, bond0, usb0, usb1

# 查询特定网口详情（只读，可用于诊断）
GET https://{bmc_ip}/redfish/v1/Managers/Self/EthernetInterfaces/eth0
# 返回: IPv4Addresses, MACAddress, SpeedMbps, LinkStatus, VLAN, MTUSize

# ⛔ 以下修改操作被 13.2 安全策略硬编码拦截，Demo 不执行
# PATCH https://{bmc_ip}/redfish/v1/Managers/Self/EthernetInterfaces/{id}
# Headers: { "X-Auth-Token": "...", "If-Match": "<etag>" }
# Body: { "VLAN": { "VLANEnable": true, "VLANId": 999 } }
# 原因: 修改 BMC 网络可能导致 BMC 不可达，需物理接入恢复

# 修改 MTU
PATCH ... Body: { "MTUSize": 1280 }

# 恢复
PATCH ... Body: { "VLAN": { "VLANEnable": false }, "MTUSize": 1500 }
```

### 3.6 交换机故障注入

#### 3.6.1 200G 交换机 — H3C S9855-24B8D

Comware 平台，支持 NETCONF / REST API / SSH CLI / SNMP。具备 RDMA (RoCEv2) 流量管理能力，支持 ERSPAN 和 gRPC 遥测。

| 故障场景 | 注入方法 | 通道 | 可恢复性 |
|----------|----------|------|----------|
| 端口 Down | `interface HundredGigE X/Y/Z` → `shutdown` | SSH CLI | `undo shutdown` |
| PFC 风暴 | 修改 PFC 配置，制造 PFC deadlock | SSH CLI | 恢复 PFC 配置 |
| ECN 阈值错配 | 修改 ECN marking threshold 过低 → 吞吐下降 | SSH CLI | 恢复阈值 |
| VLAN 隔离 | 将端口移到错误 VLAN | SSH CLI | 恢复 VLAN |
| ACL 丢弃 | 添加 ACL 规则丢弃特定流量 | SSH CLI | 删除 ACL |
| QoS 降级 | 修改 DSCP/TC 映射 → RDMA 流量降级到低优先级 | SSH CLI | 恢复映射 |
| 端口限速 | `line-rate` 限制端口带宽 | SSH CLI | 恢复限速 |
| BGP 邻居 reset | `reset bgp all` | SSH CLI | 自动重建 |

```
# SSH 连接到交换机
ssh admin@<switch_ip>

# 200G 端口 shutdown（制造 RDMA 链路中断）
system-view
interface HundredGigE 1/0/1
shutdown
quit

# 恢复
interface HundredGigE 1/0/1
undo shutdown
quit
```

#### 3.6.2 25G 交换机 — H3C S6850-56HF

Comware 平台，48×25G SFP28 + 8×100G QSFP28，支持 NETCONF / REST API / SSH CLI / SNMP。

| 故障场景 | 注入方法 | 通道 | 可恢复性 |
|----------|----------|------|----------|
| 管理网络中断 | 关闭连接控制节点的 25G 端口 | SSH CLI | `undo shutdown` |
| STP 重收敛 | 修改 STP priority 触发拓扑变更 | SSH CLI | 恢复 priority |
| LACP 降级 | 关闭 LAG 组中部分端口 | SSH CLI | `undo shutdown` |
| 路由黑洞 | 添加静态路由指向 Null0 | SSH CLI | 删除路由 |
| ARP 表清除 | `reset arp all` | SSH CLI | 自动重建 |

#### 3.6.3 H3C NETCONF/REST 接口

```python
# H3C REST API（基于 NETCONF 映射）
# 认证: Basic Auth 或 Token
# 端口: 通常 80/443

# 查询端口状态
GET https://<switch_ip>/restconf/data/ifm/interfaces/interface

# 修改端口状态（shutdown）
PUT https://<switch_ip>/restconf/data/ifm/interfaces/interface={name}
Body: { "adminStatus": "down" }

# NETCONF (RFC 6241)
# 通过 ncclient Python 库操作
from ncclient import manager
with manager.connect(host=switch_ip, port=830, username='admin',
                     password='${SWITCH_PASSWORD}', hostkey_verify=False) as m:
    # 关闭端口
    config = """
    <config>
      <top xmlns="http://www.h3c.com/netconf/config:1.0">
        <Ifmgr>
          <Interfaces>
            <Interface>
              <IfIndex>1</IfIndex>
              <AdminStatus>2</AdminStatus>  <!-- 2=down -->
            </Interface>
          </Interfaces>
        </Ifmgr>
      </top>
    </config>
    """
    m.edit_config(target='running', config=config)
```

### 3.7 存储硬件故障

| 故障场景 | 注入方法 | 通道 | 可恢复性 |
|----------|----------|------|----------|
| 磁盘状态查询 | `GET /redfish/v1/Systems/Self/Storage/{id}/Drives/{drive}` | Redfish | 只读 |
| 存储 I/O 饱和 | `fio --name=stress --rw=randrw --bs=4k --iodepth=256 --numjobs=16 --size=10G` | SSH | Kill fio |
| 存储路径中断 | `echo offline > /sys/block/sdX/device/state` | SSH | `echo running > ...` |
| 文件系统满 | `fallocate -l $(df --output=avail /mnt \| tail -1)k /mnt/fill` | SSH | 删除填充文件 |
| Ceph OSD Down | `systemctl stop ceph-osd@{id}` | SSH (存储节点) | `systemctl start` |
| Ceph 降级 | 停止 1-2 个 OSD 触发数据重平衡 | SSH | 启动 OSD |

---

## 4. OS Fault Agent — 操作系统故障注入

### 4.1 内核级故障

| 故障场景 | 注入方法 | 可恢复性 |
|----------|----------|----------|
| 内核参数异常 | `sysctl -w net.core.somaxconn=1` （TCP 连接队列极小化） | `sysctl -w net.core.somaxconn=128` |
| 文件描述符耗尽 | `ulimit -n 64` + 大量连接 | Kill 进程 |
| inotify 耗尽 | `sysctl -w fs.inotify.max_user_watches=1` | `sysctl` 恢复 |
| conntrack 溢出 | `sysctl -w net.netfilter.nf_conntrack_max=100` | `sysctl` 恢复 |
| 时钟偏移 | `date -s "+2 hours"` 或 `chronyc makestep` 大幅偏移 | `chronyc makestep` 恢复 NTP |
| 僵尸进程泛滥 | 创建大量不回收子进程的 fork 程序 | Kill 父进程 |
| 内核 OOM 配置 | `sysctl -w vm.overcommit_memory=2` + 低 overcommit_ratio | `sysctl` 恢复 |

### 4.2 文件系统故障

| 故障场景 | 注入方法 | 可恢复性 |
|----------|----------|----------|
| 磁盘空间耗尽 | `fallocate -l <size> /tmp/fill_disk` | `rm /tmp/fill_disk` |
| inode 耗尽 | `for i in $(seq 1 999999); do touch /tmp/inode_$i; done` | `rm /tmp/inode_*` |
| 只读文件系统 | `mount -o remount,ro /data` | `mount -o remount,rw /data` |
| I/O 延迟注入 | `dmsetup create delay_dev ...`（device-mapper delay） | `dmsetup remove` |

### 4.3 进程级故障

| 故障场景 | 注入方法 | 可恢复性 |
|----------|----------|----------|
| 进程 Kill | `kill -9 <pid>` | 依赖 K8s 重启策略 |
| 进程 Hang | `kill -STOP <pid>` | `kill -CONT <pid>` |
| 进程 CPU 亲和 | `taskset -cp 0 <pid>` | `taskset -cp 0-N <pid>` |
| 进程 OOM Score | `echo 1000 > /proc/<pid>/oom_score_adj` | 重置 score |
| cgroup 限制 | 修改容器 cgroup memory/cpu limit | K8s 下次调度恢复 |

### 4.4 网络栈故障

| 故障场景 | 注入方法 | 可恢复性 |
|----------|----------|----------|
| TCP 连接 Reset | `conntrack -D -p tcp --dport <port>` | 连接自动重建 |
| iptables 规则注入 | `iptables -I INPUT -p tcp --dport <port> -j DROP` | `iptables -D` |
| ARP 缓存污染 | `arp -s <ip> <wrong_mac>` | `arp -d <ip>` |
| 路由表注入 | `ip route add <dest> via <wrong_gw>` | `ip route del` |
| TC 流量整形 | `tc qdisc add dev eth0 root netem delay/loss/corrupt` | `tc qdisc del` |

---

## 5. Platform Fault Agent — 平台组件故障注入

### 5.1 Kubernetes 核心组件

基于 Cube Studio 实际部署（`install/kubernetes/`），关键组件及故障注入：

#### 5.1.1 kubeflow-dashboard（后端 Flask 应用）

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| 后端 Pod 删除 | `kubectl delete pod -l app=kubeflow-dashboard -n infra` | API 不可用直到重建 |
| 后端 Pod 限流 | 修改 Pod resources.limits.cpu 至极低值 | API 延迟飙升 |
| 健康检查故障 | `kubectl exec` 阻断 `/health` 端口 | Pod 被 K8s 重启 |
| Gunicorn Worker 耗尽 | 通过 Load Simulator 发送大量慢请求 | HTTP 503 |
| 副本数归零 | `kubectl scale deploy kubeflow-dashboard --replicas=0 -n infra` | 平台完全不可用 |

健康检查探针配置（来自 `deploy-backend.yaml`）：
- Readiness: `GET /health`，10s 初始延迟，60s 周期，2 次失败阈值
- Liveness: `GET /health`，500s 初始延迟，60s 周期，2 次失败阈值

#### 5.1.2 Celery Worker / Beat

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Worker 进程 Kill | `kubectl delete pod -l app=kubeflow-dashboard-worker -n infra` | 异步任务中断 |
| Beat 进程 Kill | `kubectl delete pod -l app=kubeflow-dashboard-schedule -n infra` | 定时任务不调度 |
| Worker 队列积压 | 通过 Load Simulator 大量提交任务超过 Worker 处理能力 | 任务延迟 |
| Redis Broker 中断 | 见 5.2 Redis 故障 | Worker 全部挂起 |

#### 5.1.3 Training Operator

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Operator Pod Kill | `kubectl delete pod -l control-plane=kubeflow-training-operator` | 新训练任务不调度 |
| CRD 删除 | `kubectl delete crd pytorchjobs.kubeflow.org` | PyTorch 任务无法创建 |
| Webhook 故障 | 注入网络延迟到 Webhook endpoint | CRD 验证超时 |

健康检查：`/healthz` (8081)，15s 初始延迟；`/readyz` (8081)，10s 初始延迟

#### 5.1.4 Argo Workflow Controller

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Controller Kill | 删除 Argo workflow-controller Pod | 运行中 Pipeline 状态不更新 |
| MinIO 不可用 | `kubectl scale deploy minio --replicas=0 -n kubeflow` | Artifact 存储失败 |
| Workflow CRD 超时 | 注入网络延迟到 K8s API Server | Workflow 提交失败 |

#### 5.1.5 Istio 服务网格

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Ingress Gateway Kill | `kubectl delete pod -l app=istio-ingress -n istio-ingress` | 所有推理服务外部不可达 |
| Sidecar 注入失败 | 修改 namespace label 移除 istio-injection | 新 Pod 无 Envoy sidecar |
| VirtualService 删除 | `kubectl delete vs <service_name> -n service` | 推理服务路由丢失 |
| Istio mTLS 强制 | 设置 PeerAuthentication 为 STRICT | 未注入 sidecar 的 Pod 被拒 |
| 超时配置异常 | 修改 VirtualService timeout 为 1s | 推理请求超时 |

### 5.2 MySQL 数据库

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| 连接中断 | `iptables -A INPUT -p tcp --dport 3306 -j DROP`（在 MySQL 节点） | 所有 DB 操作失败 |
| 连接池耗尽 | 通过外部工具占满 MySQL 连接数 | 新连接被拒 |
| 慢查询注入 | `SELECT SLEEP(30)` 占用连接 | 连接池压力 |
| 只读模式 | `SET GLOBAL read_only = ON` | 写操作失败 |
| 表锁 | `LOCK TABLES <critical_table> WRITE` | 相关操作 hang |
| MySQL 进程重启 | `systemctl restart mysql` 或 `kubectl delete pod` | 短暂不可用 |
| 数据损坏模拟 | 修改非关键表数据制造不一致 | 业务逻辑异常 |

Cube Studio DB 关键参数：
- POOL_SIZE = 300, MAX_OVERFLOW = 800, POOL_RECYCLE = 300s
- URI: `mysql+pymysql://root:admin@mysql:3306/kubeflow?charset=utf8mb4`

### 5.3 Redis 缓存/消息队列

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Redis 不可用 | `redis-cli -a admin SHUTDOWN` | 缓存+Celery Broker 全失效 |
| Redis 延迟 | `redis-cli -a admin DEBUG SLEEP 5` | 所有缓存/消息操作延迟 |
| Redis 内存满 | `redis-cli -a admin CONFIG SET maxmemory 1mb` | 写入失败 |
| Redis 网络分区 | iptables 阻断 6379 端口 | 部分节点无法访问 |
| 指定 DB 清空 | `redis-cli -a admin -n 0 FLUSHDB`（Celery Broker DB） | 所有待执行任务丢失 |

Redis 配置（来自 config.py）：
- 默认密码: `admin`，端口: 6379
- DB 0: Celery Broker，DB 1: Cache，DB 2: Socket.IO

### 5.4 Ceph 分布式存储

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| OSD 下线 | `systemctl stop ceph-osd@{id}` | 数据重平衡、I/O 降速 |
| MON 下线 | 停止 1 个 Ceph Monitor（3 中停 1） | 仲裁仍可用但冗余丧失 |
| MON 多数失效 | 停止 2 个 Monitor（3 中停 2） | 集群无法写入 |
| MDS 下线 | 停止 Ceph MDS（CephFS 元数据服务） | 文件系统元数据操作失败 |
| 网络分区 | 隔离存储集群的 200G 数据网络 | 存储 I/O 完全中断 |

### 5.5 Kafka 消息队列

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Broker 下线 | `kubectl delete pod kafka-0 -n kafka` | 分区 Leader 重选举 |
| ZooKeeper 下线 | `kubectl delete pod kafka-zookeeper-0 -n kafka` | 元数据服务中断 |
| Topic 删除 | `kafka-topics.sh --delete --topic <name>` | 消息丢失 |
| 分区数异常 | 将分区数设为 1 | 并发消费退化 |

### 5.6 Prometheus / 监控

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Prometheus 下线 | `kubectl scale statefulset prometheus-k8s --replicas=0 -n monitoring` | 监控盲区 |
| DCGM Exporter 下线 | `kubectl delete pod -l app=dcgm-exporter -n monitoring` | GPU 指标丢失 |
| Scrape 超时 | iptables 阻断 9090/9400 端口 | 指标采集失败 |

---

## 6. Service Fault Agent — 服务级故障注入

### 6.1 推理服务故障

通过 Cube Studio API (`/inferenceservice_modelview/api/`) 和 K8s 直接操作：

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| 推理 Pod 删除 | `kubectl delete pod -l app=<service_name> -n service` | 请求失败直到 Pod 重建 |
| 推理 Pod OOM | 设置极低的 memory limit + 发送长 token 请求 | Pod OOMKilled |
| 模型路径损坏 | `mv /mnt/models/<model> /mnt/models/<model>.bak` | 推理加载失败 |
| 推理副本归零 | `POST /inferenceservice_modelview/api/deploy/update/` min_replicas=0 | 服务无实例 |
| HPA 禁用 | 删除对应 HPA 资源 | 无法自动扩容 |
| Sidecar 故障 | Kill Envoy sidecar container | 流量路由失败 |
| Canary 流量异常 | 修改 VirtualService weight 全部导向有问题的版本 | 全部请求走故障版本 |
| 服务配置错误 | 修改 ConfigMap 中的推理参数 | 启动失败或行为异常 |

### 6.2 训练 Pipeline 故障

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Workflow 中途取消 | `kubectl delete workflow <name> -n pipeline` | 训练任务中断 |
| Task Pod 资源不足 | 提交超出集群容量的资源请求 | Pod 永远 Pending |
| DAG 依赖错误 | 修改 Pipeline dag_json 制造循环依赖 | Pipeline 提交失败 |
| 训练数据不可达 | 卸载 PVC 或修改 volume mount | 数据读取失败 |
| Checkpoint 目录只读 | `chmod 444 /mnt/output/` | 保存 checkpoint 失败 |

### 6.3 Notebook 故障

| 故障场景 | 注入方法 | 影响 |
|----------|----------|------|
| Notebook Pod Kill | `kubectl delete pod <notebook_pod> -n jupyter` | Kernel 中断、未保存工作丢失 |
| Kernel 挂起 | 在 Notebook 中执行死循环 | Kernel 无响应 |
| PVC 挂载故障 | 修改 PV reclaim policy 为 Delete 后触发 | 数据丢失 |
| 镜像拉取失败 | 删除 image pull secret | 新 Notebook 创建失败 |

---

## 7. 必选场景：vLLM 推理 Latency 不稳定

需求要求必须通过多种 root cause 制造 vLLM 推理延迟不稳定现象。

### 7.1 Root Cause 矩阵

```
               vLLM Latency 不稳定
                      │
    ┌────────┬────────┼────────┬────────┬────────┐
    │        │        │        │        │        │
  RC-1     RC-2     RC-3     RC-4     RC-5     RC-6
  GPU层    网络层   存储层   平台层   OS层    热力学层
```

#### RC-1: GPU 资源争抢

```yaml
scenario: gpu_contention
description: 同一 GPU 上运行多个计算任务争抢 SM 和显存带宽
inject:
  # 在推理服务所在节点启动 GPU 背景负载
  - ssh: "gpu-burn -d 600"        # 后台 GPU 满载
  # 或: 在同一 GPU 上启动另一个推理实例
  - ssh: "python3 gpu_mem_pressure.py --gpu 0 --alloc 60%"
effect:
  - 推理延迟 P50 增加 3-5x
  - P99 尾延迟出现周期性波动（与 GPU 调度交替）
  - TTFT (Time To First Token) 不稳定
recovery:
  - kill gpu-burn / gpu_mem_pressure.py
```

#### RC-2: 网络链路抖动

```yaml
scenario: network_jitter
description: 推理请求链路上引入不确定延迟
inject:
  # Istio Ingress 到推理 Pod 之间注入延迟抖动
  - ssh_on_node: "tc qdisc add dev eth0 root netem delay 50ms 100ms distribution pareto"
  # 或: 在交换机层引入微突发丢包
  - switch_cli: "interface HundredGigE 1/0/1 → qos car inbound cir 1000000"  # 限速
effect:
  - 延迟分布从正态变为长尾 (pareto)
  - P95/P50 比值 > 10
  - Stream 模式下 token 输出断断续续
recovery:
  - "tc qdisc del dev eth0 root"
  - 恢复交换机 QoS 配置
```

#### RC-3: 模型/KV Cache 存储 I/O 干扰

```yaml
scenario: storage_io_interference
description: 模型权重加载或 KV Cache 落盘时的 I/O 争抢
inject:
  # 在推理节点的数据盘发起大量随机 I/O
  - ssh: "fio --name=disturb --filename=/data/testfile --rw=randwrite --bs=4k --iodepth=128 --numjobs=8 --size=10G --time_based --runtime=300"
  # 同时启动训练任务写 checkpoint
  - load_simulator: "pipeline.storage_io_test.write_size_mb=2048"
effect:
  - 模型冷启动时间从 10s 增加到 30-60s
  - 推理过程中若发生 KV Cache swap，延迟出现间歇性尖峰
recovery:
  - kill fio
  - 停止训练任务
```

#### RC-4: 平台组件级联延迟

```yaml
scenario: platform_cascade
description: 平台组件（DB/Redis/API）延迟传导到推理服务管理面
inject:
  # 方案 A: MySQL 慢查询阻塞 API
  - mysql: "SELECT SLEEP(10)" × 100 个并发连接
  # 方案 B: Redis 延迟
  - redis: "DEBUG SLEEP 3"
  # 方案 C: Gunicorn Worker 饱和
  - load_simulator: "inference.load.ramp_up.stages[-1].concurrency=500"
effect:
  - 推理 API 的 deploy/update 操作延迟
  - HPA 扩缩容决策延迟
  - 新请求在 Istio 层排队
  - 推理本身不慢，但端到端延迟不稳定
recovery:
  - 释放 MySQL 连接
  - 恢复 Redis
  - 降低并发
```

#### RC-5: OS 资源压力

```yaml
scenario: os_resource_pressure
description: 推理节点 OS 层资源紧张
inject:
  # 内存压力 → swap 启用 → 推理进程被 swap
  - ssh: "stress-ng --vm 4 --vm-bytes 80% --timeout 300"
  # CPU 压力 → 推理前处理/后处理延迟
  - ssh: "stress-ng --cpu 64 --cpu-load 90 --timeout 300"
  # conntrack 表满 → 新连接建立延迟
  - ssh: "sysctl -w net.netfilter.nf_conntrack_max=1024"
effect:
  - 延迟整体升高且波动大
  - 出现间歇性超时
  - OOM Killer 可能杀死推理进程
recovery:
  - kill stress-ng
  - sysctl 恢复
```

#### RC-6: 热降频

```yaml
scenario: thermal_throttling
description: 通过 BMC 降低风扇转速，使 CPU/GPU 触发热保护降频
inject:
  # 降低风扇转速
  - redfish: "PATCH .../ThermalManagement  FanSetPWM=15, FanControlMode=Manual"
  # 同时施加计算负载加速升温
  - ssh: "stress-ng --cpu 128 --timeout 600"
  - ssh: "gpu-burn -d 600"
monitor:
  # 监控温度
  - redfish: "GET .../Chassis/Self/Thermal"
  # 观察温度传感器 ReadingCelsius vs UpperThresholdCritical
effect:
  - CPU/GPU 频率阶梯式下降
  - 推理延迟呈锯齿波动（降频→冷却→恢复→再降频）
recovery:
  - "PATCH .../ThermalManagement FanControlMode=Auto"
  - kill stress-ng, gpu-burn
```

### 7.2 组合注入

Agent 智能组合多个 root cause 制造更真实的场景：

```yaml
# 生产环境典型故障模式: GPU + 存储 + 网络同时出问题
combined_scenario:
  name: "realistic_latency_instability"
  phases:
    - time: 0s
      inject: [RC-2_network_jitter_mild]    # 轻微网络抖动
    - time: 60s
      inject: [RC-3_storage_io_mild]        # 同时有存储 I/O 干扰
    - time: 120s
      inject: [RC-1_gpu_contention_mild]    # 再叠加 GPU 争抢
    # 观察延迟退化的叠加效应
    - time: 300s
      recover: [RC-1]                       # 逐个恢复，观察哪个是主因
    - time: 360s
      recover: [RC-3]
    - time: 420s
      recover: [RC-2]
```

---

## 8. 必选场景：RDMA 网络异常

### 8.1 RDMA (RoCEv2) 异常场景

计算集群通过 200G NIC (RoCEv2) 进行 GPU 间通信（NCCL），交换机为 H3C S9855-24B8D。

#### F-1: PFC 死锁

```yaml
scenario: pfc_deadlock
description: 修改 PFC 配置制造优先级流控死锁
inject:
  - switch_cli: |
      system-view
      interface HundredGigE 1/0/1
      priority-flow-control enable
      priority-flow-control no-drop dot1p 0 1 2 3 4 5 6 7  # 所有优先级都不丢包
      quit
  # PFC 全部优先级 no-drop → 易触发 PFC 风暴
  # 同时施加大量 RDMA 流量
  - ssh_spoke1_node1: "ib_write_bw -d mlx5_0 --report_gbits"
  - ssh_spoke1_node2: "ib_write_bw -d mlx5_0 <node1_ip> --report_gbits --duration 300"
effect:
  - PFC pause frame 导致端口暂停
  - RDMA 吞吐从 200Gbps 降至接近 0
  - NCCL allreduce 超时
  - 多卡训练 hang
recovery:
  - switch_cli: "undo priority-flow-control no-drop dot1p 0 1 2 3"
  - 或: "undo priority-flow-control enable"
```

#### F-2: ECN 标记阈值错配

```yaml
scenario: ecn_misconfiguration
description: ECN 阈值设置过低导致过度拥塞标记，DCQCN 频繁降速
inject:
  - switch_cli: |
      system-view
      interface HundredGigE 1/0/1
      qos wred apply ecn  # 如需开启 ECN
      qos wred queue 3 ecn
      # 将 ECN 标记阈值设为极低值
      qos queue 3 wrr weight 1
      quit
effect:
  - DCQCN (Data Center QCN) 频繁触发速率降低
  - RDMA 吞吐不稳定，出现周期性波动
  - 训练 allreduce 延迟波动
recovery:
  - 恢复默认 ECN 阈值配置
```

#### F-3: 不均衡 RDMA 负载

```yaml
scenario: rdma_load_imbalance
description: 制造 RDMA 流量不均衡，部分链路拥塞
inject:
  # 方案 A: 关闭 LAG/ECMP 中的部分路径
  - switch_cli: |
      system-view
      interface HundredGigE 1/0/2
      shutdown                     # 关闭一条 uplink
      quit
  # 流量集中到剩余链路 → 拥塞

  # 方案 B: 修改 ECMP hash 使流量不均
  - switch_cli: |
      system-view
      ip load-sharing mode per-flow    # 改为 per-destination 使流量集中
      quit

  # 方案 C: Load Simulator 制造不均衡通信模式
  - load_simulator: |
      # 4 张卡中仅 GPU0-GPU1 大量通信，GPU2-GPU3 空闲
      # 制造通信拓扑不均衡
effect:
  - 部分 GPU 对之间通信延迟显著高于其他
  - 训练 allreduce 受最慢链路制约
  - 整体训练吞吐下降
recovery:
  - "undo shutdown" 恢复端口
  - 恢复 ECMP hash 配置
```

#### F-4: RDMA 链路间歇性中断

```yaml
scenario: rdma_link_flap
description: 200G 端口间歇性 up/down 制造链路抖动
inject:
  - switch_cli_script: |
      # 每 30 秒 shutdown/undo shutdown 一次
      while true; do
        system-view
        interface HundredGigE 1/0/1
        shutdown
        quit
        sleep 5
        system-view
        interface HundredGigE 1/0/1
        undo shutdown
        quit
        sleep 25
      done
effect:
  - RDMA 连接周期性断开重建
  - NCCL 通信超时重试
  - 训练任务可能 hang 或 crash
  - 链路恢复后需要重新建立 QP (Queue Pair)
recovery:
  - 停止 flap 脚本
  - 确保端口稳定 up
```

#### F-5: RoCE 网络 MTU 不一致

```yaml
scenario: roce_mtu_mismatch
description: RDMA 路径上 MTU 不一致导致分片或丢包
inject:
  # 修改部分节点的 RDMA 接口 MTU
  - ssh_spoke1_node1: "ip link set dev enp65s0f0 mtu 4096"  # 其他节点仍为 9000
  # 或修改交换机端口 MTU
  - switch_cli: |
      system-view
      interface HundredGigE 1/0/1
      jumbo enable 4096    # 降低 jumbo frame 大小
      quit
effect:
  - RDMA 大消息需要分片
  - 吞吐下降 50-70%
  - 部分消息可能因超 MTU 被丢弃
recovery:
  - "ip link set dev enp65s0f0 mtu 9000"
  - 恢复交换机 jumbo frame 配置
```

#### F-6: RDMA QoS 降级

```yaml
scenario: rdma_qos_downgrade
description: 将 RDMA (RoCEv2) 流量的 DSCP/TC 映射到低优先级队列
inject:
  - switch_cli: |
      system-view
      # 将 DSCP 26 (RoCEv2 默认) 映射到 TC0 (最低优先级)
      qos map-table dscp-dot1p
      import 26 export 0
      quit
effect:
  - RDMA 流量与普通以太网流量竞争
  - PFC 不再保护 RDMA 流量 → 丢包
  - 吞吐严重下降
recovery:
  - 恢复 DSCP 到 TC 映射 (DSCP 26 → TC 3)
```

### 8.2 RDMA 异常验证与运维恢复

每个 RDMA 故障场景都附带运维恢复手段：

```bash
# 验证 RDMA 连通性
## 服务端
ib_write_bw -d mlx5_0 --report_gbits

## 客户端
ib_write_bw -d mlx5_0 <server_ip> --report_gbits

# 验证 NCCL 通信
## 使用 nccl-tests
mpirun -np 4 --hostfile hosts ./all_reduce_perf -b 8 -e 128M -f 2 -g 1

# 查看 RDMA 计数器（丢包/重传）
rdma statistic show

# 查看 mlx5 端口计数器
ethtool -S enp65s0f0 | grep -E "rx_discards|tx_errors|rx_pci_signal"

# 查看 PFC 计数器
ethtool -S enp65s0f0 | grep -E "rx_pause|tx_pause"

# 交换机端查看
display interface HundredGigE 1/0/1
display pfc statistics interface HundredGigE 1/0/1
```

---

## 9. Load Simulator 联动

### 9.1 过载制造故障

通过 Load Simulator 制造系统过载，诱发自然故障：

| 过载场景 | Load Simulator 配置 | 预期故障 |
|----------|---------------------|----------|
| API 过载 → Worker 饱和 | inference.concurrency=500, pipeline.concurrent=50 | HTTP 503, 任务排队 |
| DB 连接耗尽 | 混合模式全部启用，极高并发 | SQLAlchemy OperationalError |
| GPU 全占用 | 多个大模型推理 + 训练 + 微调同时运行 | 调度 Pending, OOM |
| 存储 I/O 饱和 | pipeline.storage_io_test + finetune + notebook 同时读写 | I/O 延迟 > 1s |
| Redis 内存溢出 | 大量 Celery 任务 + 缓存写入 | Redis OOM |
| Istio 连接数超限 | 推理服务高并发 + 大量 Notebook 访问 | Envoy 503 |

### 9.2 联动契约（Integration Contract）

Fault Injector 与 Load Simulator 的联动遵循以下最小契约，确保 Demo 演示稳定可控：

| 契约项 | 规则 | 说明 |
|--------|------|------|
| 成功判定 | Load Simulator 进程 exit code = 0 | 非零视为失败 |
| 超时 | 可配置，默认 `integration_timeout: 300` 秒 | 超时等同失败 |
| 重试 | Demo 阶段不重试（fail-fast） | 长期可配置 retry_count |
| 失败处理 | 立即停止注入 → 触发 WAL 回滚 → 生成部分报告 | 保证环境恢复 |
| 通信方式 | 子进程调用（`asyncio.create_subprocess_exec`） | 不依赖网络 RPC |
| 状态传递 | Load Simulator stdout 输出 JSON 摘要，fault-injector 解析 | 单向，无双向握手 |

```python
# 联动执行逻辑（OrchestrationEngine 内部）
async def _run_load_simulator(self, params: dict, timeout: int = 300) -> dict:
    """启动 Load Simulator 子进程，返回执行摘要"""
    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "load_simulator", "run",
        "--config", params["config_path"],
        "--mode", params.get("mode", "stress"),
        "--output-format", "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise IntegrationTimeoutError(f"Load Simulator 超时 ({timeout}s)")

    if proc.returncode != 0:
        raise IntegrationFailedError(
            f"Load Simulator 失败 (exit={proc.returncode}): {stderr.decode()}"
        )
    return json.loads(stdout.decode())  # 解析 JSON 摘要
```

> **失败时行为：** `IntegrationTimeoutError` 或 `IntegrationFailedError` 均触发 `rollback_journal.recover_all()`，确保已注入的故障被恢复。部分收集到的指标仍会写入报告（标记为 `incomplete`）。

### 9.3 联动配置

```yaml
# 在 fault-injector 配置中引用 load-simulator
load_simulator_integration:
  enabled: true
  config_path: "./load-simulator-config.yaml"  # Load Simulator 的配置文件
  integration_timeout: 300                      # 联动超时（秒）
  on_failure: "rollback_and_report"             # 失败策略: rollback_and_report | abort_only
  # 联动策略
  strategies:
    - name: "overload_then_inject"
      description: "先通过 Load Simulator 加压到 80%，再注入故障观察系统行为"
      steps:
        - action: "load_simulator.start"
          params: { mode: "stress", target_utilization: 0.8 }
        - wait_until: "monitor.system_utilization > 0.75"
        - action: "fault.inject"
          params: { scenario: "mysql_connection_drop" }
        - observe: 300  # 秒
        - action: "fault.recover"
        - action: "load_simulator.stop"

    - name: "fault_under_load"
      description: "在正常负载下注入故障，观察降级能力"
      steps:
        - action: "load_simulator.start"
          params: { mode: "mixed", components: ["inference", "pipeline"] }
        - wait: 120
        - action: "fault.inject"
          params: { scenario: "gpu_card_removal" }
        - observe: 600
        - action: "fault.recover"
        - observe: 300  # 观察恢复过程
        - action: "load_simulator.stop"
```

---

## 10. 配置 Schema

### 10.1 完整 YAML 配置

```yaml
# fault-injector-config.yaml

global:
  cube_studio_url: "http://cube-studio.example.com"
  auth:
    method: "jwt"
    username: "admin"
    jwt_password: "${JWT_SECRET}"
  prometheus_url: "http://prometheus-k8s.monitoring:9090"
  log_level: "INFO"
  report_output: "./fault-reports/"
  # 安全保护: 防止误操作
  safety:
    require_confirmation: true          # 危险操作需确认
    max_concurrent_faults: 3            # 同时最多注入 3 个故障
    auto_recover_timeout: 600           # 600s 后自动恢复所有故障
    excluded_nodes: []                  # 不允许注入的节点列表
    dry_run: false                      # 仅打印操作不实际执行

# 编排引擎配置
orchestrator:
  max_parallel_agents: 4                # 同时运行的最大 Agent 数
  observe_interval: 15                  # 观测期每 15 秒采集指标
  session_dir: "./fault-reports/sessions/"  # Session 状态持久化目录

# Channel 连接配置
channels:
  ssh:
    pool_size: 5                        # 每节点 SSH 连接池大小
    command_timeout: 60                 # 默认命令超时 (秒)
    connect_timeout: 10                 # SSH 连接超时 (秒)
    retry_count: 2                      # 连接失败重试次数
  redfish:
    request_timeout: 30                 # Redfish HTTP 请求超时 (秒)
    session_refresh_interval: 600       # Token 刷新间隔 (秒)
    verify_ssl: false                   # BMC 证书通常自签名
  switch:
    cli_timeout: 30                     # SSH CLI 命令超时 (秒)
    netconf_timeout: 30                 # NETCONF 操作超时 (秒)
    netconf_port: 830                   # NETCONF 端口
  kubernetes:
    kubeconfig: "~/.kube/config"        # kubeconfig 路径 (支持多集群)
    request_timeout: 30                 # K8s API 请求超时 (秒)
    namespaces:                         # Cube Studio 命名空间映射
      backend: "infra"
      pipeline: "pipeline"
      service: "service"
      notebook: "jupyter"
      monitoring: "monitoring"
      istio: "istio-ingress"

# Diagnosis Agent (唯一 LLM 组件) 配置
diagnosis:
  enabled: true
  llm:
    provider: "minimax"                 # minimax | openai-compatible
    model: "minimax-2.1"
    api_base: "https://api.minimax.chat/v1"
    api_key_env: "MINIMAX_API_KEY"      # 从环境变量读取 API Key
    max_tokens: 8192                    # 诊断报告最大 token 数
    temperature: 0.3                    # 低温度确保分析稳定性
  resilience_scoring:
    enabled: true                       # 是否生成韧性评分
    categories:                         # 评分维度
      - "auto_recovery"                 # 自动恢复能力
      - "degradation_handling"          # 降级处理能力
      - "fault_isolation"               # 故障隔离能力
      - "data_integrity"                # 数据完整性保护

# 节点清单
inventory:
  hub:
    control_nodes:
      - name: "ctrl-1"
        ssh: { host: "10.0.1.1", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.0.100.1", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["k8s-master", "etcd"]
      - name: "ctrl-2"
        ssh: { host: "10.0.1.2", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.0.100.2", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["k8s-master", "etcd"]
      - name: "ctrl-3"
        ssh: { host: "10.0.1.3", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.0.100.3", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["k8s-master", "etcd"]
    storage_nodes:
      - name: "stor-1"
        ssh: { host: "10.0.2.1", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.0.100.4", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["ceph-osd", "ceph-mon"]
      - name: "stor-2"
        ssh: { host: "10.0.2.2", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.0.100.5", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["ceph-osd", "ceph-mon"]
      - name: "stor-3"
        ssh: { host: "10.0.2.3", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.0.100.6", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["ceph-osd", "ceph-mon"]
  spoke1:
    compute_nodes:
      - name: "gpu-1-1"
        ssh: { host: "10.1.1.1", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.1.100.1", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["k8s-worker", "gpu"]
        gpu: { count: 4, type: "RTX5090" }
        rdma: { device: "mlx5_0", interface: "enp65s0f0" }
      - name: "gpu-1-2"
        ssh: { host: "10.1.1.2", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.1.100.2", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["k8s-worker", "gpu"]
        gpu: { count: 4, type: "RTX5090" }
        rdma: { device: "mlx5_0", interface: "enp65s0f0" }
  spoke2:
    compute_nodes:
      - name: "gpu-2-1"
        ssh: { host: "10.2.1.1", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.2.100.1", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["k8s-worker", "gpu"]
        gpu: { count: 4, type: "RTX5090" }
        rdma: { device: "mlx5_0", interface: "enp65s0f0" }
      - name: "gpu-2-2"
        ssh: { host: "10.2.1.2", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.2.100.2", user: "admin", password: "${BMC_PASSWORD}" }
        role: ["k8s-worker", "gpu"]
        gpu: { count: 4, type: "RTX5090" }
        rdma: { device: "mlx5_0", interface: "enp65s0f0" }

  switches:
    - name: "sw-200g"
      type: "H3C S9855-24B8D"
      management: { host: "10.0.200.1", protocol: "ssh", user: "admin", password: "${SWITCH_PASSWORD}" }
      role: "200g-data-network"
    - name: "sw-25g"
      type: "H3C S6850-56HF"
      management: { host: "10.0.200.2", protocol: "ssh", user: "admin", password: "${SWITCH_PASSWORD}" }
      role: "25g-management-network"

# 故障场景配置
scenarios:
  # ---- 硬件故障 ----
  hardware:
    cpu_stress:
      enabled: true
      target_nodes: ["gpu-1-1"]
      params:
        workers: 64
        load_percent: 95
        duration: 300
    gpu_removal:
      enabled: false
      target_nodes: ["gpu-1-1"]
      params:
        gpu_bdf: "0000:41:00.0"   # 目标 GPU 的 PCIe BDF
    thermal_throttle:
      enabled: false
      target_nodes: ["gpu-1-1"]
      params:
        fan_pwm: 15               # 风扇占空比（%）
        duration: 600
    network_delay:
      enabled: true
      target_nodes: ["gpu-1-1", "gpu-1-2"]
      params:
        delay_ms: 50
        jitter_ms: 100
        interface: "eth0"
        duration: 300

  # ---- OS 故障 ----
  os:
    memory_pressure:
      enabled: true
      target_nodes: ["gpu-1-1"]
      params:
        vm_bytes_percent: 85
        duration: 300
    disk_full:
      enabled: false
      target_nodes: ["ctrl-1"]
      params:
        mount_point: "/data"
        fill_percent: 98
    time_skew:
      enabled: false
      target_nodes: ["ctrl-2"]
      params:
        offset: "+2 hours"

  # ---- 平台故障 ----
  platform:
    mysql_connection_drop:
      enabled: true
      params:
        method: "iptables"        # iptables | slow_query | readonly
        duration: 60
    redis_unavailable:
      enabled: false
      params:
        method: "shutdown"        # shutdown | memory_limit | network
        duration: 30
    celery_worker_kill:
      enabled: false
      params:
        target: "worker"          # worker | beat | both
    istio_gateway_kill:
      enabled: false
    k8s_node_drain:
      enabled: false
      target_nodes: ["gpu-1-1"]

  # ---- 服务故障 ----
  service:
    inference_pod_kill:
      enabled: true
      params:
        service_name: "vllm-load-test"
        namespace: "service"
    pipeline_workflow_cancel:
      enabled: false
      params:
        pipeline_name: "training-*"
    notebook_pod_kill:
      enabled: false
      params:
        notebook_name: "load-nb-*"

  # ---- 必选场景 ----
  mandatory:
    vllm_latency_instability:
      enabled: true
      root_causes:                # 可选择启用哪些 root cause
        - "gpu_contention"
        - "network_jitter"
        - "storage_io_interference"
        - "platform_cascade"
        - "os_resource_pressure"
        - "thermal_throttling"
      params:
        inference_service: "vllm-load-test"
        duration_per_rc: 300      # 每个 root cause 测试时长
        combined_test: true       # 是否进行组合测试

    rdma_network_anomaly:
      enabled: true
      sub_scenarios:
        - "pfc_deadlock"
        - "ecn_misconfiguration"
        - "rdma_load_imbalance"
        - "rdma_link_flap"
        - "roce_mtu_mismatch"
        - "rdma_qos_downgrade"
      params:
        target_switch: "sw-200g"
        target_nodes: ["gpu-1-1", "gpu-1-2"]
        duration_per_scenario: 300
        verify_recovery: true     # 每次恢复后验证 RDMA 连通性

# Load Simulator 联动
load_simulator:
  enabled: true
  config_path: "./load-simulator-config.yaml"

# 监控配置
monitor:
  prometheus_scrape_interval: 10
  baseline_duration: 120          # 故障注入前的基线采集时长
  post_recovery_duration: 120     # 恢复后的观察时长
```

### 10.2 快速启动模板

```yaml
# quick-start-vllm-latency.yaml
# 仅测试 vLLM latency 不稳定（必选场景）
global:
  cube_studio_url: "http://localhost"
  auth: { method: "username", username: "admin" }
  prometheus_url: "http://prometheus-k8s.monitoring:9090"
  safety:
    require_confirmation: false
    auto_recover_timeout: 300
    dry_run: false

inventory:
  spoke1:
    compute_nodes:
      - name: "gpu-1-1"
        ssh: { host: "10.1.1.1", user: "root", key_file: "~/.ssh/id_rsa" }
        bmc: { host: "10.1.100.1", user: "admin", password: "${BMC_PASSWORD}" }
        gpu: { count: 4, type: "RTX5090" }

scenarios:
  mandatory:
    vllm_latency_instability:
      enabled: true
      root_causes: ["gpu_contention", "network_jitter", "os_resource_pressure"]
      params:
        inference_service: "vllm-load-test"
        duration_per_rc: 180
        combined_test: false

load_simulator:
  enabled: true
  config_path: "./load-simulator-config.yaml"
```

---

## 11. 执行模式

### 11.1 命令行接口

```bash
# 标准运行（按配置文件执行所有启用的场景）
fault-injector --config config.yaml

# 仅运行指定场景（从 scenarios/registry.py 注册表查找）
fault-injector --config config.yaml --scenario vllm_latency_instability
fault-injector --config config.yaml --scenario rdma_network_anomaly
fault-injector --config config.yaml --scenario gpu_contention,network_jitter  # 多个场景

# 仅运行指定层级
fault-injector --config config.yaml --layer hardware
fault-injector --config config.yaml --layer platform,service

# 干运行（验证配置 + 节点连通性，不实际注入）
fault-injector --config config.yaml --dry-run

# 手动触发恢复所有注入的故障（从 rollback.jsonl 读取）
fault-injector --recover-all --session <session_id>

# 列出当前活跃的故障注入
fault-injector --status --session <session_id>

# 恢复中断的会话（从 session.json 恢复阶段状态）
fault-injector --resume <session_id>

# 列出所有可用场景
fault-injector --list-scenarios

# 验证配置文件（Pydantic 校验 + 节点 SSH/BMC 连通性检查）
fault-injector --validate --config config.yaml
```

### 11.2 执行流程

```
┌──────────────────┐
│ 1. 配置解析       │  Pydantic 校验 YAML，创建 Session 目录
│    Session 初始化  │  初始化 Channel 连接池，启动回滚看门狗
└──────┬───────────┘
       │
┌──────▼───────────┐
│ 2. 连通性预检     │  SSH 连接测试、BMC Redfish 认证、K8s API 可达性
│    (可选 dry-run) │  交换机 SSH 连接测试、Prometheus 查询测试
└──────┬───────────┘
       │
┌──────▼───────────┐
│ 3. 基线采集       │  Monitor Agent 采集 baseline_duration 秒基线指标
│                   │  Prometheus PromQL 查询 + CubeStudio API 状态
│                   │  结果写入 session_dir/baseline.json
└──────┬───────────┘
       │
┌──────▼───────────┐
│ 4. 故障注入       │  Orchestrator 按场景调度 Agent.inject()
│                   │  每次注入前: 回滚日志 WAL 写入 (fsync)
│                   │  Channel.execute() 实际执行注入操作
│                   │  并发控制: max_concurrent_faults 限制
└──────┬───────────┘
       │
┌──────▼───────────┐
│ 5. 观测期         │  Monitor Agent 按 observe_interval 持续采集
│                   │  指标数据写入 session_dir/metrics/
│                   │  Scenario.monitor_queries() 提供 PromQL
└──────┬───────────┘
       │
┌──────▼───────────┐
│ 6. 故障恢复       │  回滚日志按注入逆序执行 recover_action
│                   │  Channel 执行恢复命令
│                   │  更新回滚条目状态: active → recovered
└──────┬───────────┘
       │
┌──────▼───────────┐
│ 7. 恢复验证       │  Scenario.verify() 对比指标与基线
│                   │  Monitor Agent 采集 post_recovery_duration 秒
│                   │  确认所有指标恢复到基线 ± 15% 容差
└──────┬───────────┘
       │
┌──────▼───────────┐
│ 8. 诊断分析       │  Diagnosis Agent (MiniMax-2.1) 接收结构化上下文:
│    (LLM)          │  {基线, 注入时间线, 观测指标, 恢复时间线}
│                   │  生成: 传播链、根因、韧性评分、建议
└──────┬───────────┘
       │
┌──────▼───────────┐
│ 9. 报告生成       │  Jinja2 HTML + plotly 图表
│                   │  输出到 session_dir/report/
└──────────────────┘
```

### 11.3 Orchestrator 引擎核心逻辑

```python
class OrchestrationEngine:
    """确定性编排引擎"""

    async def run(self, config: FaultInjectorConfig) -> SessionResult:
        session = Session.create(config)
        watchdog = asyncio.create_task(
            self.rollback.auto_recover_watchdog(config.global_.safety.auto_recover_timeout)
        )

        try:
            # Phase 1-2: 初始化 + 连通性检查
            channels = await self._init_channels(config)
            await self._preflight_check(channels, config.inventory)

            # Phase 3: 基线采集
            session.phase = "baseline"
            session.save()
            baseline = await self.monitor.collect_baseline(
                queries=self._aggregate_monitor_queries(config),
                duration=config.monitor.baseline_duration)
            session.metrics_baseline = baseline

            # Phase 4-5: 按场景循环执行
            for scenario_config in self._resolve_scenarios(config):
                scenario = SCENARIO_REGISTRY[scenario_config.name]()
                ctx = FaultContext(channels=channels, baseline=baseline,
                                  params=scenario_config.params, ...)

                # 注入
                session.phase = "inject"
                session.save()
                await scenario.inject(ctx)

                # 观测
                session.phase = "observe"
                session.save()
                metrics = await self.monitor.observe(
                    queries=scenario.monitor_queries(),
                    duration=scenario_config.params.get("duration", 300))

                # 恢复
                session.phase = "recover"
                session.save()
                await scenario.recover(ctx)
                await self.rollback.recover_scenario(scenario_config.name)

                # 验证
                session.phase = "verify"
                session.save()
                verified = await scenario.verify(ctx)
                post_metrics = await self.monitor.observe(
                    queries=scenario.monitor_queries(),
                    duration=config.monitor.post_recovery_duration)

                session.scenario_results[scenario_config.name] = ScenarioResult(
                    baseline=baseline, during=metrics, post=post_metrics,
                    verified=verified)

            # Phase 8-9: 诊断 + 报告
            session.phase = "report"
            session.save()
            if config.diagnosis.enabled:
                diagnosis = await self.diagnosis_agent.analyze(session)
                session.diagnosis = diagnosis
            report = self.reporter.generate(session)
            session.status = "completed"

        except Exception as e:
            session.status = "failed"
            await self.rollback.recover_all()  # 失败时恢复所有故障
            raise
        finally:
            watchdog.cancel()
            session.save()

        return session
```

---

## 12. Diagnosis Agent — 故障诊断与报告

> **Diagnosis Agent 是系统中唯一使用 LLM (MiniMax-2.1) 的组件。**
> 它是只读分析器：接收结构化数据（基线指标、注入时间线、观测指标、恢复结果），
> 通过 LLM 推理生成诊断报告。即使 LLM 分析出错，也不会对系统造成任何损害。

### 12.1 架构：结构化上下文 → LLM 推理 → 结构化输出

```
┌──────────────────────────────────────────────┐
│            结构化上下文组装                     │
│                                              │
│  baseline.json ──┐                           │
│  events.jsonl ───┼──→ DiagnosisPrompt ──────→│──→ MiniMax-2.1 API
│  metrics/*.json ─┤    (Jinja2 模板渲染)       │      │
│  rollback.jsonl ─┘                           │      │
│                                              │      ▼
│                                              │  结构化 JSON 响应
│                                              │      │
│                                              │      ▼
│                              DiagnosisResult │◄─ Pydantic 解析校验
│                              · propagation_chain   │
│                              · root_cause          │
│                              · resilience_scores   │
│                              · recommendations     │
│                              · unexpected_findings │
└──────────────────────────────────────────────┘
```

**不使用 LLM Agent 循环（ReAct/tool-use）**，而是单次结构化 prompt → 结构化输出。原因：
- 诊断数据在调用时已完整收集，无需 LLM 主动获取信息
- 单次调用延迟可控（vs. 多轮 tool-use 循环延迟不可预测）
- 输出通过 Pydantic 严格校验，格式不合规则回退到模板报告

### 12.2 Prompt 工程

```python
class DiagnosisAgent:
    """唯一的 LLM 组件：故障诊断分析器"""

    SYSTEM_PROMPT = """
你是 Cube Studio 平台的故障诊断专家。你将收到一次故障注入实验的完整数据，
包括基线指标、注入操作时间线、观测期指标、恢复结果。

你的任务：
1. 分析故障传播链：从注入点到最终影响，重建因果路径
2. 识别根因：区分表面症状与真实根因
3. 发现未预期影响：对比预期影响与实际观测，找出意外传播路径
4. 评估韧性：对每个受影响组件的恢复能力打分 (0-100)
5. 提供建议：给出具体可操作的改进建议

你必须以指定的 JSON 格式输出。不要输出 JSON 以外的内容。
"""

    async def analyze(self, session: Session) -> DiagnosisResult:
        # 1. 组装结构化上下文
        context = self._build_context(session)

        # 2. 渲染 prompt
        user_prompt = DIAGNOSIS_TEMPLATE.render(
            scenarios=context.scenarios,
            baseline=context.baseline,
            timeline=context.events,
            metrics_during=context.metrics_during,
            metrics_post=context.metrics_post,
            recovery_results=context.recovery_results,
            inventory=context.inventory_summary,
        )

        # 3. 调用 LLM (单次，非 Agent 循环)
        response = await self.llm_client.chat_completion(
            model=self.config.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            response_format={"type": "json_object"},  # 强制 JSON 输出
        )

        # 4. Pydantic 解析校验
        try:
            result = DiagnosisResult.model_validate_json(response.content)
        except ValidationError:
            logger.warning("LLM output failed validation, falling back to template report")
            result = self._fallback_template_report(session)

        return result
```

### 12.3 诊断输出结构

```python
class PropagationStep(BaseModel):
    timestamp: datetime
    component: str          # e.g. "GPU-1-1", "vLLM", "Istio Gateway"
    layer: str              # hardware | os | platform | service
    metric: str             # e.g. "temperature", "inference_p50"
    value_before: float
    value_after: float
    description: str        # 自然语言描述

class DiagnosisResult(BaseModel):
    # 故障传播链
    propagation_chain: list[PropagationStep]

    # 根因分析
    root_cause: str                     # 简要根因描述
    root_cause_layer: str               # 根因所在层级
    root_cause_detail: str              # 详细分析

    # 韧性评分
    resilience_scores: dict[str, int]   # 组件 → 0-100 评分
    overall_resilience: int             # 总体韧性评分
    weakest_components: list[str]       # 最薄弱环节

    # 未预期发现
    unexpected_findings: list[UnexpectedFinding]

    # 恢复分析
    ttr_seconds: int                    # Time To Recovery
    recovery_completeness: float        # 0.0 ~ 1.0

    # 改进建议
    recommendations: list[Recommendation]
```

### 12.4 故障传播链示例

```
[故障传播链示例]

注入: GPU-1-1 风扇降速 (PWM 15%)
  → 温度升高: CPU 78°C → 85°C → 92°C (5分钟内)
  → GPU 温度: 72°C → 83°C → 89°C
  → CPU 热降频: 3.8GHz → 2.5GHz → 1.8GHz
  → GPU 热降频: 2.5GHz → 2.0GHz → 1.5GHz
  → vLLM 推理延迟: P50 120ms → 350ms → 680ms
  → Istio QPS 下降: 850 req/s → 320 req/s → 150 req/s
  → HPA 尝试扩容: 请求新 Pod
  → 新 Pod 调度到 GPU-1-2 (唯一有空闲 GPU 的节点)
  → GPU-1-2 负载升高
  → 集群整体推理能力下降 60%
```

### 12.5 报告结构

```
fault-reports/sessions/{session_id}/report/
├── report.html                        # 主报告 (Jinja2 渲染)
├── report.json                        # 机器可读的 DiagnosisResult
├── timeline.csv                       # 事件时间线 (events.jsonl → CSV)
└── charts/
    ├── fault-propagation.html         # 故障传播链交互图 (plotly)
    ├── metrics-comparison.html        # 指标对比: 基线 vs 故障期 vs 恢复后
    ├── recovery-timeline.html         # 恢复时间线
    └── resilience-radar.html          # 韧性评分雷达图
```

### 12.6 报告内容示例

```
┌────────────────────────────────────────────────────────┐
│              Fault Injection Report                     │
├────────────────────────────────────────────────────────┤
│ 场景: vLLM Latency Instability — RC-6 Thermal Throttle│
│ 时间: 2026-02-13 14:00 ~ 14:25                        │
│ 目标: gpu-1-1 (Spoke1)                                │
│ 持续时间: 25 分钟 (注入10分钟 + 观察5分钟 + 恢复10分钟) │
├────────────────────────────────────────────────────────┤
│                                                        │
│ 故障注入:                                               │
│   14:00 — 基线采集完成 (P50=120ms, P99=350ms)          │
│   14:02 — 风扇 PWM 设为 15%                             │
│   14:02 — gpu-burn 启动                                 │
│   14:05 — CPU 温度达到 UpperThresholdNonCritical (85°C) │
│   14:07 — GPU 温度达到 83°C，开始降频                    │
│   14:08 — 推理 P50 升至 450ms (+275%)                   │
│   14:10 — CPU 温度达到 UpperThresholdCritical (92°C)    │
│   14:10 — P99 达到 2.8s (+700%)                         │
│   14:12 — 故障注入停止                                   │
│                                                        │
│ 恢复过程:                                               │
│   14:12 — 风扇恢复 Auto 模式                             │
│   14:14 — 温度开始下降 (92°C → 78°C)                    │
│   14:16 — GPU 频率恢复正常                               │
│   14:18 — 推理延迟恢复基线 (P50=125ms)                   │
│   14:20 — 完全恢复，验证通过                             │
│                                                        │
│ TTR (Time To Recovery): 8 分钟                          │
│ 影响范围: 推理服务 QPS 下降 60%，持续 10 分钟            │
│                                                        │
│ 诊断 (MiniMax-2.1 分析):                                │
│   根因: BMC 风扇控制 → 散热不足 → CPU/GPU 热降频         │
│   传播路径: 热力学 → 计算性能 → 推理延迟 → 服务 QPS      │
│   建议:                                                 │
│   1. 配置温度告警阈值低于 UpperThresholdNonCritical      │
│   2. 推理服务配置跨节点 HPA 以实现热点迁移               │
│   3. BMC 风扇控制策略应设为不可远程修改 (安全加固)       │
└────────────────────────────────────────────────────────┘
```

### 12.7 智能分析能力

Diagnosis Agent 基于 MiniMax-2.1 提供以下智能分析（全部通过单次 prompt 实现）：

1. **根因推断**：从多层指标异常自动推断根本原因，区分表面症状与真实根因
2. **传播路径重建**：基于时间序列相关性重建故障传播链（哪个指标先异常 → 因果排序）
3. **韧性评分**：对系统各组件的故障韧性进行 0-100 评分

```
[韧性评分示例]

组件韧性评分 (0-100):
  推理服务 (HPA 自动扩缩):     72/100  — 可自动恢复但速度慢
  训练 Pipeline (Argo 重试):   68/100  — 支持重试但无断点续训
  MySQL (单实例):              35/100  — 无高可用，单点故障
  Redis (单实例):              30/100  — 无持久化，故障即丢失
  Celery Worker (K8s 重启):    65/100  — 自动重启但任务可能丢失
  Ceph 存储 (3副本):           85/100  — 支持单 OSD 故障
  Istio Gateway (HPA):        78/100  — 自动扩容但切换有延迟
  RDMA 网络:                  40/100  — 无自动恢复，需人工介入

总体韧性: 59/100
最薄弱环节: Redis (单点), MySQL (单点)
```

4. **未预期影响发现**：对比已知影响范围与实际观测指标，识别意外传播路径

```
[自动发现]

注入: Redis 不可用 (30s)
预期影响: Celery 任务暂停
实际观察:
  - ✓ Celery 任务暂停
  - ⚠ 发现额外影响: Cube Studio 前端登录失败
    原因: Session 存储依赖 Redis (DB 2)，未配置降级策略
  - ⚠ 发现额外影响: 推理服务 HPA 决策延迟
    原因: HPA 指标缓存在 Redis (DB 1) 中

建议: 为 Session 存储和 HPA 指标配置非 Redis 的降级方案
```

5. **跨场景对比**：多个场景执行后，对比不同 root cause 对同一指标的影响程度，帮助排序修复优先级

### 12.8 降级策略

当 LLM 不可用或输出不合规时，系统自动降级到**模板报告**：

| 功能 | LLM 可用 | LLM 不可用 (降级) |
|------|----------|-------------------|
| 传播链 | 因果推理 + 自然语言 | 按时间排序的指标异常列表 |
| 根因 | 智能推断 | "根因: 用户配置的注入操作" (直接引用) |
| 韧性评分 | 综合分析 | 基于 TTR 的简单公式: `max(0, 100 - TTR_seconds)` |
| 建议 | 具体可操作 | 通用建议模板 |
| 未预期发现 | 智能识别 | 仅列出偏离基线 >50% 的非预期指标 |

---

## 13. 安全保护机制

### 13.1 多层防护

安全机制分布在三个层级：配置层、Channel 层、回滚层。

| 保护项 | 实现层级 | 机制 | 说明 |
|--------|----------|------|------|
| 操作确认 | 配置层 | `safety.require_confirmation: true` | 危险操作需终端交互确认 |
| 自动恢复 | 回滚层 | `safety.auto_recover_timeout: 600` | 看门狗协程超时后自动 `recover_all()` |
| 并发限制 | Orchestrator | `safety.max_concurrent_faults: 3` | asyncio.Semaphore 控制 |
| 节点排除 | 配置层 | `safety.excluded_nodes: [...]` | Agent 预检时拒绝排除节点 |
| 干运行 | Channel 层 | `safety.dry_run: true` | `BaseChannel.execute()` 仅日志不执行 |
| WAL 回滚 | 回滚层 | `rollback.jsonl` (fsync) | 注入前写、崩溃后可恢复 |
| 操作审计 | 回滚层 | `events.jsonl` (append-only) | 完整操作时间线 |
| 禁止操作 | Channel 层 | `FORBIDDEN_OPERATIONS` 硬编码列表 | Channel 方法内部拦截 |
| 无 LLM 执行 | 架构层 | 确定性 Agent 设计 | LLM 仅做只读诊断分析 |

### 13.2 不可执行操作

以下操作在 Channel 层**硬编码拦截**，无论配置如何都不执行：

**Redfish Channel:**
- BMC 恢复出厂设置 (`Manager.RestoreFactory`)
- 修改 BMC 网口 IP 地址（可能导致 BMC 不可达）

**K8s Channel:**
- 删除 K8s namespace（级联删除所有资源）
- 删除 etcd 数据
- K8s master 节点 ForceOff（除非 `safety.allow_master_poweroff: true`）

**SSH Channel:**
- `rm -rf /` 及类似全盘删除命令（正则匹配拦截）
- 修改 SSH 服务配置（可能导致节点不可达）

**Switch Channel:**
- 删除管理 VLAN（可能导致交换机不可达）
- 恢复出厂设置

**跨 Channel:**
- 存储集群所有 OSD 同时下线（`SafetyGuard` 检查活跃 OSD 数量）
- 同时注入故障数超过 `max_concurrent_faults`

### 13.3 崩溃安全保证

```
正常流程:
  1. WAL 写入恢复操作 → fsync
  2. Channel 执行注入操作
  3. Session 状态更新

崩溃场景:
  A. WAL 写入前崩溃 → 注入未执行，无需恢复 ✓
  B. WAL 写入后、注入前崩溃 → 恢复操作已记录，--resume 可安全清理 ✓
  C. 注入后、Session 更新前崩溃 → WAL 已记录，--resume 恢复所有 active 条目 ✓
  D. 观测期崩溃 → 同 C，--resume 恢复 ✓

唯一不可恢复场景:
  - 网络完全中断且无带外访问 → 需物理干预
  - BMC 网口 IP 被修改（已硬编码禁止）
```

---

## 附录 A: BMC Redfish API 速查

### 认证

```
POST https://{bmc_ip}/redfish/v1/SessionService/Sessions
Body: { "UserName": "admin", "Password": "${BMC_PASSWORD}" }
响应 Header: X-Auth-Token → 后续请求携带
```

### 关键端点

| 操作 | 方法 | URI |
|------|------|-----|
| 系统信息 | GET | `/redfish/v1/Systems/Self` |
| 系统重置 | POST | `/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset` |
| 处理器列表 | GET | `/redfish/v1/Systems/Self/Processors` |
| 内存列表 | GET | `/redfish/v1/Systems/Self/Memory` |
| 存储信息 | GET | `/redfish/v1/Systems/Self/Storage/{id}` |
| 硬盘信息 | GET | `/redfish/v1/Systems/Self/Storage/{id}/Drives/{drive}` |
| 机箱信息 | GET | `/redfish/v1/Chassis/Self` |
| 电源信息 | GET | `/redfish/v1/Chassis/Self/Power` |
| 温度/风扇 | GET | `/redfish/v1/Chassis/Self/Thermal` |
| 风扇控制 | GET/PATCH | `/redfish/v1/Chassis/Self/Thermal/ThermalManagement` |
| PCIe 设备 | GET | `/redfish/v1/Chassis/Self/PCIeDevices/{id}` |
| BMC 信息 | GET | `/redfish/v1/Managers/Self` |
| BMC 重启 | POST | `/redfish/v1/Managers/Self/Actions/Manager.Reset` |
| BMC 网口列表 | GET | `/redfish/v1/Managers/Self/EthernetInterfaces` |
| BMC 网口配置 | GET/PATCH | `/redfish/v1/Managers/Self/EthernetInterfaces/{id}` |
| 用户管理 | GET/POST/PATCH/DELETE | `/redfish/v1/AccountService/Accounts/{id}` |
| 事件日志 | GET | `/redfish/v1/Systems/Self/LogServices/{id}/Entries` |
| 查询扩展 | GET | `?$expand=.($levels=N)` |
| 属性筛选 | GET | `?$select=field1,field2` |

### 系统重置类型

```
On, ForceOff, ForceRestart, GracefulShutdown, ForcePowerCycle, Nmi
```

---

## 附录 B: H3C 交换机操作速查

### SSH CLI 常用命令

```bash
# 进入系统视图
system-view

# 端口操作
interface HundredGigE 1/0/1     # 200G 端口
interface Twenty-FiveGigE 1/0/1 # 25G 端口
shutdown                         # 关闭端口
undo shutdown                    # 开启端口

# PFC 配置
priority-flow-control enable
priority-flow-control no-drop dot1p 3    # 仅 TC3 不丢包
undo priority-flow-control enable

# QoS / DSCP 映射
qos map-table dscp-dot1p
import 26 export 3              # DSCP 26 → TC3 (RoCEv2 默认)

# VLAN
vlan 100
port HundredGigE 1/0/1

# ACL
acl advanced 3000
rule permit ip source 10.1.1.0 0.0.0.255
rule deny ip

# 查看状态
display interface HundredGigE 1/0/1
display pfc statistics interface HundredGigE 1/0/1
display qos map-table dscp-dot1p
display vlan all
```

### NETCONF (ncclient)

```python
from ncclient import manager
m = manager.connect(host='<switch_ip>', port=830,
                    username='admin', password='${SWITCH_PASSWORD}',
                    hostkey_verify=False)
# 获取配置
config = m.get_config(source='running')
# 编辑配置
m.edit_config(target='running', config='<config>...</config>')
```

---

## 附录 C: 故障场景清单汇总

| # | 层级 | 场景 | 必选 | 工具/通道 |
|---|------|------|------|-----------|
| H-1 | 硬件/CPU | CPU 过载 | | stress-ng / SSH |
| H-2 | 硬件/CPU | CPU 热降频 | | Redfish 风扇控制 |
| H-3 | 硬件/CPU | NMI 注入 | | Redfish Reset(Nmi) |
| H-4 | 硬件/GPU | GPU 显存 OOM | | CUDA 脚本 / SSH |
| H-5 | 硬件/GPU | GPU 掉卡 | | PCIe remove / SSH |
| H-6 | 硬件/GPU | GPU 驱动卸载 | | rmmod / SSH |
| H-7 | 硬件/GPU | GPU 计算饱和 | | gpu-burn / SSH |
| H-8 | 硬件/GPU | GPU 热降频 | | Redfish + gpu-burn |
| H-9 | 硬件/GPU | NVLink 降级 | | nvidia-smi / SSH |
| H-10 | 硬件/内存 | 内存压力 | | stress-ng / SSH |
| H-11 | 硬件/内存 | OOM Killer | | cgroup / SSH |
| H-12 | 硬件/网络 | 网络延迟/丢包 | | tc netem / SSH |
| H-13 | 硬件/网络 | 网络分区 | | iptables / SSH |
| H-14 | 硬件/网络 | 网口 Down | | ip link / SSH |
| H-15 | 硬件/交换机 | 端口 Shutdown | | H3C CLI / SSH |
| H-16 | 硬件/交换机 | PFC 错配 | | H3C CLI / SSH |
| H-17 | 硬件/交换机 | ACL 丢弃 | | H3C CLI / SSH |
| H-18 | 硬件/存储 | 存储 I/O 饱和 | | fio / SSH |
| H-19 | 硬件/存储 | 存储路径中断 | | sysfs / SSH |
| O-1 | OS | 文件描述符耗尽 | | ulimit / SSH |
| O-2 | OS | conntrack 溢出 | | sysctl / SSH |
| O-3 | OS | 时钟偏移 | | date / SSH |
| O-4 | OS | 磁盘空间耗尽 | | fallocate / SSH |
| O-5 | OS | 只读文件系统 | | mount / SSH |
| O-6 | OS | 进程 Kill/Hang | | kill / SSH |
| P-1 | 平台 | Dashboard Pod 删除 | | kubectl |
| P-2 | 平台 | Celery Worker Kill | | kubectl |
| P-3 | 平台 | MySQL 连接中断 | | iptables / SSH |
| P-4 | 平台 | MySQL 连接池耗尽 | | SQL 脚本 |
| P-5 | 平台 | Redis 不可用 | | redis-cli |
| P-6 | 平台 | Istio Gateway Kill | | kubectl |
| P-7 | 平台 | VirtualService 删除 | | kubectl |
| P-8 | 平台 | Training Operator Kill | | kubectl |
| P-9 | 平台 | Ceph OSD 下线 | | systemctl / SSH |
| P-10 | 平台 | MinIO 不可用 | | kubectl |
| P-11 | 平台 | Prometheus 下线 | | kubectl |
| S-1 | 服务 | 推理 Pod Kill | | kubectl |
| S-2 | 服务 | 推理 OOM | | K8s resource limit |
| S-3 | 服务 | 模型路径损坏 | | mv / SSH |
| S-4 | 服务 | Pipeline Workflow 取消 | | kubectl |
| S-5 | 服务 | Notebook Pod Kill | | kubectl |
| **M-1** | **必选** | **vLLM Latency: GPU 争抢** | **✓** | stress / SSH |
| **M-2** | **必选** | **vLLM Latency: 网络抖动** | **✓** | tc netem / SSH |
| **M-3** | **必选** | **vLLM Latency: 存储 I/O** | **✓** | fio / SSH |
| **M-4** | **必选** | **vLLM Latency: 平台级联** | **✓** | 多工具组合 |
| **M-5** | **必选** | **vLLM Latency: OS 资源压力** | **✓** | stress-ng / SSH |
| **M-6** | **必选** | **vLLM Latency: 热降频** | **✓** | Redfish + SSH |
| **M-7** | **必选** | **RDMA: PFC 死锁** | **✓** | H3C CLI / SSH |
| **M-8** | **必选** | **RDMA: ECN 错配** | **✓** | H3C CLI / SSH |
| **M-9** | **必选** | **RDMA: 负载不均衡** | **✓** | H3C CLI + ib_tools |
| **M-10** | **必选** | **RDMA: 链路 Flap** | **✓** | H3C CLI / SSH |
| **M-11** | **必选** | **RDMA: MTU 不一致** | **✓** | ip link + H3C CLI |
| **M-12** | **必选** | **RDMA: QoS 降级** | **✓** | H3C CLI / SSH |
