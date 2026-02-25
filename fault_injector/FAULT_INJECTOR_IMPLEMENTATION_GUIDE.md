# Fault Injector 实现指南

> **目标**：指导 AI Coding Agent 分步骤实现 Fault Injector 完整功能  
> **参考文档**：`fault-injector.md` (设计规格), `channel.md` (Channel 层设计)  
> **更新日期**：2026-02-25

---

## 1. 项目概述

### 1.1 目标

为 Cube Studio 平台实现多维度故障注入系统，覆盖硬件、OS、平台、服务四个层次。

### 1.2 核心设计原则

1. **确定性编排**：故障注入/恢复由确定性 Python 引擎执行，不依赖 LLM
2. **WAL 先写原则**：任何注入操作的恢复命令必须在注入**之前**写入 WAL
3. **安全守卫**：硬编码拦截危险命令（`rm -rf /`, `dd if=/dev/zero` 等）
4. **Channel 层复用**：遵循 `channel.md` 设计，与其他模块共享 Channel

### 1.3 目录结构

```
fault_injector/
├── __init__.py
├── __main__.py                 # python -m fault_injector 入口
├── cli.py                      # Click CLI
├── config/
│   ├── __init__.py
│   ├── schema.py               # Pydantic v2 配置模型
│   ├── defaults.py             # 默认配置
│   └── loader.py               # YAML 加载 + 校验
├── orchestrator/
│   ├── __init__.py
│   ├── engine.py               # FaultOrchestrator（asyncio 确定性调度）
│   ├── session.py              # Session 状态管理
│   ├── scheduler.py            # 调度辅助
│   └── watchdog.py             # 回滚看门狗
├── agents/
│   ├── __init__.py
│   ├── base.py                 # BaseAgent / AgentResult
│   ├── hardware.py             # HardwareFaultAgent（Redfish BMC）
│   ├── os_fault.py             # OSFaultAgent（SSH + stress-ng/tc）
│   ├── platform.py             # PlatformFaultAgent（K8s/MySQL/Redis）
│   ├── service.py              # ServiceFaultAgent（推理/Pipeline/Notebook）
│   ├── monitor.py              # MonitorAgent（指标采集）
│   └── diagnosis.py            # DiagnosisAgent（MiniMax-2.1 LLM）
├── channels/
│   ├── __init__.py
│   ├── base.py                 # BaseChannel（来自 channel.md）
│   ├── ssh.py                  # SSHChannel
│   ├── redfish.py              # RedfishChannel（BMC）
│   ├── switch.py               # SwitchChannel（H3C CLI）
│   ├── kubernetes.py           # K8sChannel
│   ├── cube_studio.py          # CubeStudioChannel
│   └── prometheus.py           # PrometheusChannel
├── scenarios/
│   ├── __init__.py
│   ├── base.py                 # BaseScenario
│   ├── registry.py             # 场景注册表
│   ├── vllm_latency.py         # RC-1~RC-6 六个 vLLM 延迟场景
│   └── rdma_anomaly.py         # F-1~F-6 六个 RDMA 异常场景
├── safety/
│   ├── __init__.py
│   ├── guard.py                # SafetyGuard
│   └── rollback.py             # WAL 回滚日志
├── reporting/
│   ├── __init__.py
│   ├── timeline.py             # 故障时间线
│   ├── html_report.py          # HTML 报告
│   ├── charts.py               # Plotly 图表
│   └── resilience.py           # 韧性评分
└── tests/
    ├── __init__.py
    ├── conftest.py             # pytest fixtures
    ├── test_config.py          # 配置测试
    ├── test_channels.py        # Channel 测试
    ├── test_scenarios.py       # 场景测试
    ├── test_rollback.py        # 回滚测试
    ├── test_safety.py          # 安全测试
    └── test_integration.py     # 集成测试
```

---

## 2. 当前实现状态

### 2.1 已完成

| 模块 | 文件 | 状态 |
|------|------|------|
| CLI 入口 | `cli.py`, `__main__.py` | ✅ 完成 |
| 配置层 | `config/schema.py`, `loader.py`, `defaults.py` | ✅ 完成 |
| 安全层 | `safety/guard.py`, `rollback.py` | ✅ 完成 |
| SSH 通道 | `channels/ssh.py`, `base.py` | ✅ 完成 |
| 场景基类 | `scenarios/base.py`, `registry.py` | ✅ 完成 |
| RC-2 场景 | `scenarios/network_jitter.py` | ✅ 完成 |
| 测试指南 | `tests/TEST_GUIDE.md` | ✅ 完成 |

### 2.2 待实现

| 模块 | 文件 | 优先级 |
|------|------|--------|
| 编排引擎 | `orchestrator/*.py` | P0 |
| 更多 Channel | `channels/redfish.py`, `switch.py`, `kubernetes.py` 等 | P0 |
| 必选场景 | `scenarios/vllm_latency.py`, `rdma_anomaly.py` | P0 |
| Agent 层 | `agents/*.py` | P1 |
| 报告生成 | `reporting/*.py` | P2 |
| 测试代码 | `tests/test_*.py` | P1 |

---

## 3. 分步实现计划

### Phase 1: 完善基础设施 (优先级: P0)

#### Step 1.1: 完善 Channel 层

**文件**: `channels/base.py`

```python
"""
BaseChannel — Channel 基类

遵循 channel.md 设计原则，统一 dry_run 和 WAL 集成。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from fault_injector.safety.guard import SafetyViolationError
from fault_injector.safety.rollback import RollbackJournal

logger = logging.getLogger(__name__)


@dataclass
class ChannelResult:
    """Channel 执行结果"""
    success: bool
    output: str = ""
    error: str = ""
    dry_run: bool = False
    duration_ms: int = 0


class BaseChannel(ABC):
    """
    Channel 基类 — 统一 dry_run 和 WAL 集成
    
    设计原则 (来自 channel.md):
    - 所有 Agent 通过 Channel 发送请求
    - Channel 负责：认证、连接池、重试、dry-run、安全守卫、回滚注册
    """
    
    def __init__(
        self,
        dry_run: bool = False,
        wal: RollbackJournal | None = None,
        guard: Any = None,
    ):
        self.dry_run = dry_run
        self.wal = wal
        self.guard = guard
    
    async def execute(
        self,
        action: str,
        params: dict,
        recovery_action: str | None = None,
        recovery_params: dict | None = None,
    ) -> ChannelResult:
        """
        统一执行入口。
        
        流程:
        1. 禁止操作检查
        2. 写操作 → WAL 记录
        3. dry_run → 仅日志
        4. 实际执行
        """
        # 1. 禁止操作检查
        self._check_safety(action, params)
        
        # 2. WAL 记录（在执行之前）
        if recovery_action and self.wal:
            self.wal.record(
                fault_id=params.get("fault_id", ""),
                channel=self.__class__.__name__,
                target=params.get("target", ""),
                inject_action=action,
                inject_params=params,
                recover_action=recovery_action,
                recover_params=recovery_params or {},
            )
        
        # 3. dry_run → 仅日志
        if self.dry_run:
            logger.info(f"[DRY-RUN] {action}: {params}")
            return ChannelResult(success=True, dry_run=True)
        
        # 4. 实际执行
        return await self._execute_impl(action, params)
    
    @abstractmethod
    async def _execute_impl(self, action: str, params: dict) -> ChannelResult:
        """子类实现具体执行逻辑"""
        pass
    
    def _check_safety(self, action: str, params: dict) -> None:
        """
        安全检查。
        
        子类可重写以添加特定检查。
        """
        if self.guard:
            self.guard.check_command(str(params), self.__class__.__name__)
    
    def _is_forbidden(self, action: str, params: dict) -> bool:
        """检查是否为禁止操作"""
        return False
```

#### Step 1.2: 实现 PrometheusChannel

**文件**: `channels/prometheus.py`

```python
"""
PrometheusChannel — Prometheus HTTP API 查询

遵循 channel.md §3.2 设计。
只读操作，无需回滚。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from fault_injector.channels.base import BaseChannel, ChannelResult

logger = logging.getLogger(__name__)


class PrometheusChannel(BaseChannel):
    """
    Prometheus HTTP API 查询。
    
    只读操作，无需回滚。
    """
    
    def __init__(
        self,
        base_url: str,
        dry_run: bool = False,
        timeout: int = 30,
    ):
        super().__init__(dry_run=dry_run)
        self.base_url = base_url
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client
    
    async def _execute_impl(self, action: str, params: dict) -> ChannelResult:
        """执行 PromQL 查询"""
        if action == "query_instant":
            result = await self.query_instant(params["promql"])
            return ChannelResult(success=True, output=str(result))
        elif action == "query_range":
            result = await self.query_range(
                params["promql"],
                params["start"],
                params["end"],
                params.get("step", "15s"),
            )
            return ChannelResult(success=True, output=str(result))
        return ChannelResult(success=False, error=f"Unknown action: {action}")
    
    async def query_instant(self, promql: str) -> float:
        """
        即时查询。
        
        GET /api/v1/query?query=...
        
        Returns:
            float: 查询结果值
        """
        client = await self._get_client()
        resp = await client.get("/api/v1/query", params={"query": promql})
        resp.raise_for_status()
        data = resp.json()
        
        if data["status"] != "success":
            raise ValueError(f"Prometheus query failed: {data}")
        
        result = data["data"]["result"]
        if not result:
            return 0.0
        
        # 返回第一个值
        return float(result[0]["value"][1])
    
    async def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> list[tuple[float, float]]:
        """
        范围查询。
        
        GET /api/v1/query_range?query=...&start=...&end=...&step=...
        
        Returns:
            list[tuple[timestamp, value]]: 时序数据
        """
        client = await self._get_client()
        resp = await client.get("/api/v1/query_range", params={
            "query": promql,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        })
        resp.raise_for_status()
        data = resp.json()
        
        if data["status"] != "success":
            raise ValueError(f"Prometheus query failed: {data}")
        
        result = data["data"]["result"]
        if not result:
            return []
        
        return [(float(v[0]), float(v[1])) for v in result[0]["values"]]
    
    async def collect_baseline(
        self,
        queries: dict[str, str],
        duration: int = 120,
        interval: int = 15,
    ) -> dict[str, list[float]]:
        """
        采集基线指标。
        
        运行 duration 秒，按 interval 采样。
        
        Args:
            queries: {metric_name: promql}
            duration: 采集时长（秒）
            interval: 采样间隔（秒）
            
        Returns:
            dict: {metric_name: [values...]}
        """
        baseline: dict[str, list[float]] = {name: [] for name in queries}
        start = datetime.now()
        
        while (datetime.now() - start).total_seconds() < duration:
            for name, promql in queries.items():
                try:
                    value = await self.query_instant(promql)
                    baseline[name].append(value)
                except Exception as e:
                    logger.warning(f"Failed to query {name}: {e}")
                    baseline[name].append(0.0)
            
            await asyncio.sleep(interval)
        
        return baseline
    
    async def close(self) -> None:
        """关闭连接"""
        if self._client:
            await self._client.aclose()
            self._client = None
```

#### Step 1.3: 实现 K8sChannel

**文件**: `channels/kubernetes.py`

```python
"""
K8sChannel — Kubernetes API 操作

遵循 channel.md §3.6 设计。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from kubernetes import client, config
from kubernetes.client import ApiException

from fault_injector.channels.base import BaseChannel, ChannelResult
from fault_injector.safety.guard import SafetyViolationError

logger = logging.getLogger(__name__)


class K8sChannel(BaseChannel):
    """
    Kubernetes API 操作。
    
    - 支持多集群 (通过 kubeconfig 切换)
    - 安全守卫: 禁止删除 namespace
    """
    
    # 禁止操作
    FORBIDDEN_OPERATIONS = [
        ("delete", "Namespace"),
        ("delete", "CustomResourceDefinition"),
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
    
    def __init__(
        self,
        kubeconfig: str = "~/.kube/config",
        dry_run: bool = False,
        wal: Any = None,
    ):
        super().__init__(dryry_run=dry_run, wal=wal)
        self.kubeconfig = kubeconfig
        self._api_client: Optional[client.ApiClient] = None
        self._core_v1: Optional[client.CoreV1Api] = None
        self._apps_v1: Optional[client.AppsV1Api] = None
    
    def _load_config(self) -> None:
        """加载 kubeconfig"""
        if self._api_client is None:
            config.load_kube_config(config_file=self.kubeconfig)
            self._api_client = client.ApiClient()
            self._core_v1 = client.CoreV1Api(self._api_client)
            self._apps_v1 = client.AppsV1Api(self._api_client)
    
    def _check_safety(self, action: str, params: dict) -> None:
        """检查禁止操作"""
        for forbidden_action, forbidden_resource in self.FORBIDDEN_OPERATIONS:
            if action == forbidden_action and params.get("resource_type") == forbidden_resource:
                raise SafetyViolationError(
                    f"Operation '{action}' on '{forbidden_resource}' is forbidden"
                )
    
    async def _execute_impl(self, action: str, params: dict) -> ChannelResult:
        self._load_config()
        
        if action == "delete_pod":
            return await self._delete_pod(
                params["pod_name"],
                params["namespace"],
            )
        elif action == "scale_deployment":
            return await self._scale_deployment(
                params["name"],
                params["namespace"],
                params["replicas"],
            )
        elif action == "get_pods":
            return await self._get_pods(
                params["namespace"],
                params.get("label_selector"),
            )
        
        return ChannelResult(success=False, error=f"Unknown action: {action}")
    
    async def _delete_pod(self, pod_name: str, namespace: str) -> ChannelResult:
        """删除 Pod"""
        try:
            self._core_v1.delete_namespaced_pod(pod_name, namespace)
            logger.info(f"Deleted pod {pod_name} in {namespace}")
            return ChannelResult(success=True)
        except ApiException as e:
            return ChannelResult(success=False, error=str(e))
    
    async def _scale_deployment(
        self,
        name: str,
        namespace: str,
        replicas: int,
    ) -> ChannelResult:
        """修改 Deployment 副本数"""
        try:
            # 获取当前副本数用于恢复
            current = self._apps_v1.read_namespaced_deployment(name, namespace)
            current_replicas = current.spec.replicas
            
            # 记录恢复操作到 WAL
            if self.wal:
                self.wal.record(
                    fault_id=f"scale_{name}_{namespace}",
                    channel="kubernetes",
                    target=f"{namespace}/{name}",
                    inject_action="scale_deployment",
                    inject_params={"replicas": replicas},
                    recover_action="scale_deployment",
                    recover_params={"replicas": current_replicas},
                )
            
            # 执行缩放
            body = {"spec": {"replicas": replicas}}
            self._apps_v1.patch_namespaced_deployment_scale(name, namespace, body)
            logger.info(f"Scaled {name} to {replicas} replicas")
            return ChannelResult(success=True)
        except ApiException as e:
            return ChannelResult(success=False, error=str(e))
    
    async def _get_pods(
        self,
        namespace: str,
        label_selector: Optional[str] = None,
    ) -> ChannelResult:
        """获取 Pod 列表"""
        try:
            pods = self._core_v1.list_namespaced_pod(
                namespace,
                label_selector=label_selector,
            )
            pod_names = [p.metadata.name for p in pods.items]
            return ChannelResult(success=True, output="\n".join(pod_names))
        except ApiException as e:
            return ChannelResult(success=False, error=str(e))
    
    async def delete_pods_by_label(
        self,
        label_selector: str,
        namespace: str,
    ) -> ChannelResult:
        """删除匹配标签的所有 Pod"""
        self._load_config()
        try:
            self._core_v1.delete_collection_namespaced_pod(
                namespace,
                label_selector=label_selector,
            )
            logger.info(f"Deleted pods with selector {label_selector} in {namespace}")
            return ChannelResult(success=True)
        except ApiException as e:
            return ChannelResult(success=False, error=str(e))
```

---

### Phase 2: 实现编排引擎 (优先级: P0)

#### Step 2.1: Session 状态管理

**文件**: `orchestrator/session.py`

```python
"""
Session — Session 状态管理

管理故障注入会话的状态持久化。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional
import uuid

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    """Session 状态"""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"


class SessionPhase(str, Enum):
    """Session 阶段"""
    INIT = "init"
    BASELINE = "baseline"
    INJECT = "inject"
    OBSERVE = "observe"
    RECOVER = "recover"
    VERIFY = "verify"
    REPORT = "report"


@dataclass
class Session:
    """
    Session 状态模型。
    
    持久化到 session_dir/session.json。
    """
    session_id: str
    config_hash: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    status: SessionStatus = SessionStatus.RUNNING
    phase: SessionPhase = SessionPhase.INIT
    active_faults: list[str] = field(default_factory=list)
    rollback_journal_path: str = ""
    events: list[dict] = field(default_factory=list)
    scenario_results: dict[str, dict] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.session_id:
            self.session_id = uuid.uuid4().hex[:8]
    
    @classmethod
    def create(cls, config_hash: str = "", session_dir: str = "./fault-reports/sessions/") -> "Session":
        """创建新 Session"""
        session = cls(
            session_id=uuid.uuid4().hex[:8],
            config_hash=config_hash,
        )
        session.rollback_journal_path = f"{session_dir}{session.session_id}/rollback.jsonl"
        return session
    
    def save(self, session_dir: str = "./fault-reports/sessions/") -> None:
        """保存 Session 状态"""
        path = Path(session_dir) / self.session_id / "session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        data["status"] = self.status.value
        data["phase"] = self.phase.value
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.debug(f"Session saved: {path}")
    
    @classmethod
    def load(cls, session_id: str, session_dir: str = "./fault-reports/sessions/") -> Optional["Session"]:
        """加载 Session 状态"""
        path = Path(session_dir) / session_id / "session.json"
        if not path.exists():
            return None
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        data["started_at"] = datetime.fromisoformat(data["started_at"])
        data["status"] = SessionStatus(data["status"])
        data["phase"] = SessionPhase(data["phase"])
        
        return cls(**data)
    
    def add_event(self, event: str, details: dict | None = None) -> None:
        """添加事件"""
        self.events.append({
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "details": details or {},
        })
    
    def set_phase(self, phase: SessionPhase) -> None:
        """设置阶段"""
        self.phase = phase
        self.add_event(f"Phase changed to {phase.value}")
```

#### Step 2.2: 编排引擎

**文件**: `orchestrator/engine.py`

```python
"""
FaultOrchestrator — 确定性编排引擎

管理故障注入的完整生命周期。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from fault_injector.config.schema import FaultInjectorConfig, ScenarioConfig
from fault_injector.orchestrator.session import Session, SessionPhase, SessionStatus
from fault_injector.channels.ssh import SSHChannel
from fault_injector.channels.prometheus import PrometheusChannel
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.safety.guard import SafetyGuard
from fault_injector.scenarios.registry import SCENARIO_REGISTRY

logger = logging.getLogger(__name__)


class FaultOrchestrator:
    """
    确定性编排引擎。
    
    执行流程:
    1. 配置解析
    2. 连通性预检
    3. 基线采集
    4. 故障注入
    5. 观测期
    6. 故障恢复
    7. 恢复验证
    8. 报告生成
    """
    
    def __init__(
        self,
        config: FaultInjectorConfig,
        dry_run: bool = False,
        session_dir: str = "./fault-reports/sessions/",
    ):
        self.config = config
        self.dry_run = dry_run
        self.session_dir = session_dir
        
        # 初始化组件
        self.session: Optional[Session] = None
        self.ssh: Optional[SSHChannel] = None
        self.prometheus: Optional[PrometheusChannel] = None
        self.rollback: Optional[RollbackJournal] = None
        self.guard: Optional[SafetyGuard] = None
    
    async def run(self) -> Session:
        """
        执行完整故障注入流程。
        """
        # 1. 初始化
        self.session = Session.create(
            config_hash="",  # TODO: 计算配置哈希
            session_dir=self.session_dir,
        )
        self.session.save(self.session_dir)
        
        # 初始化组件
        self._init_components()
        
        # 启动看门狗
        watchdog_task = asyncio.create_task(self._watchdog())
        
        try:
            # 2. 连通性预检
            self.session.set_phase(SessionPhase.INIT)
            self.session.save(self.session_dir)
            await self._preflight_check()
            
            # 3. 基线采集
            self.session.set_phase(SessionPhase.BASELINE)
            self.session.save(self.session_dir)
            baseline = await self._collect_baseline()
            
            # 4-7. 按场景执行
            scenarios = self._resolve_scenarios()
            for scenario_config in scenarios:
                await self._run_scenario(scenario_config, baseline)
            
            # 8. 报告生成
            self.session.set_phase(SessionPhase.REPORT)
            self.session.save(self.session_dir)
            await self._generate_report()
            
            self.session.status = SessionStatus.COMPLETED
            self.session.save(self.session_dir)
            
        except Exception as e:
            logger.error(f"Orchestration failed: {e}")
            self.session.status = SessionStatus.FAILED
            self.session.add_event("error", {"message": str(e)})
            self.session.save(self.session_dir)
            
            # 恢复所有故障
            if self.rollback:
                await self.rollback.recover_all()
            raise
        
        finally:
            watchdog_task.cancel()
        
        return self.session
    
    def _init_components(self) -> None:
        """初始化组件"""
        session_path = Path(self.session_dir) / self.session.session_id
        session_path.mkdir(parents=True, exist_ok=True)
        
        # 回滚日志
        self.rollback = RollbackJournal(session_path)
        
        # 安全守卫
        self.guard = SafetyGuard()
        
        # SSH Channel
        inventory = self._build_inventory()
        self.ssh = SSHChannel(
            inventory=inventory,
            dry_run=self.dry_run,
            wal=self.rollback,
            guard=self.guard,
        )
        
        # Prometheus Channel
        # TODO: 从配置读取 URL
        self.prometheus = PrometheusChannel(
            base_url="http://prometheus-k8s.monitoring:9090",
            dry_run=self.dry_run,
        )
    
    def _build_inventory(self) -> dict:
        """构建节点清单"""
        inventory = {}
        for group_name, nodes in self.config.inventory.items():
            for node in nodes:
                inventory[node.name] = node
        return inventory
    
    async def _preflight_check(self) -> None:
        """连通性预检"""
        self.session.add_event("preflight_check_start")
        # TODO: 实现 SSH/BMC/K8s 连通性检查
        self.session.add_event("preflight_check_complete")
    
    async def _collect_baseline(self) -> dict:
        """采集基线指标"""
        self.session.add_event("baseline_collection_start")
        # TODO: 实现基线采集
        baseline = {}
        self.session.add_event("baseline_collection_complete")
        return baseline
    
    def _resolve_scenarios(self) -> list[ScenarioConfig]:
        """解析要执行的场景"""
        scenarios = []
        for name, config in self.config.scenarios.items():
            if config.enabled:
                scenarios.append(config)
        return scenarios
    
    async def _run_scenario(
        self,
        config: ScenarioConfig,
        baseline: dict,
    ) -> None:
        """执行单个场景"""
        scenario_class = SCENARIO_REGISTRY.get(config.name)
        if not scenario_class:
            logger.warning(f"Scenario not found: {config.name}")
            return
        
        scenario = scenario_class()
        
        # 构建上下文
        from fault_injector.scenarios.base import FaultContext
        ctx = FaultContext(
            ssh=self.ssh,
            rollback=self.rollback,
            guard=self.guard,
            target_node=config.target_nodes[0] if config.target_nodes else "",
            params=config.params,
            fault_id=scenario.generate_fault_id(FaultContext(
                ssh=self.ssh,
                rollback=self.rollback,
                guard=self.guard,
                target_node="",
                params=config.params,
                fault_id="",
            )),
        )
        
        # 注入
        self.session.set_phase(SessionPhase.INJECT)
        self.session.save(self.session_dir)
        inject_result = await scenario.inject(ctx)
        if not inject_result.success:
            raise RuntimeError(f"Inject failed: {inject_result.error}")
        
        # 观测
        self.session.set_phase(SessionPhase.OBSERVE)
        self.session.save(self.session_dir)
        duration = config.params.get("duration", 60)
        if not self.dry_run:
            await asyncio.sleep(duration)
        else:
            logger.info(f"[DRY-RUN] Skip observation period ({duration}s)")
        
        # 恢复
        self.session.set_phase(SessionPhase.RECOVER)
        self.session.save(self.session_dir)
        recover_result = await scenario.recover(ctx)
        
        # 验证
        self.session.set_phase(SessionPhase.VERIFY)
        self.session.save(self.session_dir)
        verified = await scenario.verify(ctx)
        
        # 记录结果
        self.session.scenario_results[config.name] = {
            "inject_success": inject_result.success,
            "recover_success": recover_result.success,
            "verified": verified,
        }
        self.session.save(self.session_dir)
    
    async def _generate_report(self) -> None:
        """生成报告"""
        self.session.add_event("report_generation_start")
        # TODO: 实现报告生成
        self.session.add_event("report_generation_complete")
    
    async def _watchdog(self) -> None:
        """看门狗：超时自动恢复"""
        timeout = self.config.global_.safety.auto_recover_timeout
        await asyncio.sleep(timeout)
        
        logger.warning(f"Watchdog timeout ({timeout}s), auto-recovering")
        if self.rollback:
            await self.rollback.recover_all()
```

---

### Phase 3: 实现必选场景 (优先级: P0)

#### Step 3.1: vLLM Latency 场景

**文件**: `scenarios/vllm_latency.py`

```python
"""
VLLMLatencyScenarios — RC-1~RC-6 六个 vLLM 延迟场景

必选场景：制造 vLLM 推理延迟不稳定。
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# RC-1: GPU 资源争抢
# ============================================================================

class GPUContentionScenario(BaseScenario):
    """
    RC-1: GPU 资源争抢。
    
    使用 gpu-burn 在同一 GPU 上运行多个计算任务争抢 SM 和显存带宽。
    
    效果:
    - 推理延迟 P50 增加 3-5x
    - P99 尾延迟出现周期性波动
    - TTFT (Time To First Token) 不稳定
    """
    
    @property
    def name(self) -> str:
        return "gpu_contention"
    
    @property
    def description(self) -> str:
        return "GPU 资源争抢 — 使用 gpu-burn 制造 GPU 满载"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        gpu_id = ctx.params.get("gpu_id", 0)
        
        # 构建命令
        inject_cmd = f"gpu-burn -d {duration}"
        recover_cmd = "pkill -f gpu-burn"
        
        logger.info(f"注入 GPU 争抢: node={ctx.target_node}, duration={duration}s")
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="gpu_burn",
            inject_params={"duration": duration, "gpu_id": gpu_id},
            recover_action="kill_gpu_burn",
            recover_params={},
        )
        
        # 执行
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"GPU 争抢注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"GPU 争抢注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复 GPU 争抢: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pkill -f gpu-burn",
        )
        
        # pkill 失败可能是因为进程不存在，这是可接受的
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        # 检查 gpu-burn 是否还在运行
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pgrep -f gpu-burn || echo 'not_running'",
        )
        if "not_running" in result.output:
            logger.info("验证通过: gpu-burn 已停止")
            return True
        logger.warning("验证失败: gpu-burn 仍在运行")
        return False
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "gpu_util": 'DCGM_FI_DEV_GPU_UTIL{{node="{node}"}}',
            "gpu_mem_used": 'DCGM_FI_DEV_FB_USED{{node="{node}"}}',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
        }


# ============================================================================
# RC-3: 存储 I/O 干扰
# ============================================================================

class StorageIOInterferenceScenario(BaseScenario):
    """
    RC-3: 存储 I/O 干扰。
    
    使用 fio 在推理节点的数据盘发起大量随机 I/O。
    
    效果:
    - 模型冷启动时间增加 3-6x
    - KV Cache 落盘时延迟出现间歇性尖峰
    """
    
    @property
    def name(self) -> str:
        return "storage_io_interference"
    
    @property
    def description(self) -> str:
        return "存储 I/O 干扰 — 使用 fio 制造磁盘 I/O 压力"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        filename = ctx.params.get("filename", "/data/testfile")
        
        # 构建命令
        inject_cmd = (
            f"fio --name=disturb --filename={filename} "
            f"--rw=randwrite --bs=4k --iodepth=128 --numjobs=8 "
            f"--size=10G --time_based --runtime={duration} &"
        )
        recover_cmd = "pkill -f 'fio.*disturb'"
        
        logger.info(f"注入存储 I/O 干扰: node={ctx.target_node}, duration={duration}s")
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="fio_stress",
            inject_params={"duration": duration, "filename": filename},
            recover_action="kill_fio",
            recover_params={},
        )
        
        # 执行
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"存储 I/O 干扰注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"存储 I/O 干扰注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复存储 I/O 干扰: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pkill -f 'fio.*disturb'",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "disk_io_util": 'node_disk_io_utilization_seconds{{device="{device}"}}',
            "disk_io_wait": 'node_disk_io_time_weighted_seconds{{device="{device}"}}',
        }


# ============================================================================
# RC-5: OS 资源压力
# ============================================================================

class OSResourcePressureScenario(BaseScenario):
    """
    RC-5: OS 资源压力。
    
    使用 stress-ng 制造内存和 CPU 压力。
    
    效果:
    - 延迟整体升高且波动大
    - 出现间歇性超时
    - OOM Killer 可能杀死推理进程
    """
    
    @property
    def name(self) -> str:
        return "os_resource_pressure"
    
    @property
    def description(self) -> str:
        return "OS 资源压力 — 使用 stress-ng 制造 CPU/内存压力"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        vm_bytes_percent = ctx.params.get("vm_bytes_percent", 80)
        cpu_workers = ctx.params.get("cpu_workers", 64)
        cpu_load = ctx.params.get("cpu_load", 90)
        
        # 构建命令
        inject_cmd = (
            f"stress-ng "
            f"--vm 4 --vm-bytes {vm_bytes_percent}% "
            f"--cpu {cpu_workers} --cpu-load {cpu_load} "
            f"--timeout {duration}s"
        )
        recover_cmd = "pkill -f stress-ng"
        
        logger.info(
            f"注入 OS 资源压力: node={ctx.target_node}, "
            f"vm={vm_bytes_percent}%, cpu_workers={cpu_workers}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="stress_ng",
            inject_params={
                "duration": duration,
                "vm_bytes_percent": vm_bytes_percent,
                "cpu_workers": cpu_workers,
                "cpu_load": cpu_load,
            },
            recover_action="kill_stress_ng",
            recover_params={},
        )
        
        # 执行
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        # stress-ng 在后台运行，前台命令会立即返回
        if result.success or "dispatching hogs" in result.output:
            logger.info(f"OS 资源压力注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"OS 资源压力注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复 OS 资源压力: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pkill -f stress-ng",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "cpu_util": 'node_cpu_utilization_seconds{{mode="idle"}}',
            "memory_util": 'node_memory_utilization_seconds',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
        }


# 注册场景
SCENARIOS = [
    GPUContentionScenario,
    StorageIOInterferenceScenario,
    OSResourcePressureScenario,
]
```

#### Step 3.2: RDMA 异常场景

**文件**: `scenarios/rdma_anomaly.py`

```python
"""
RDMAAnomalyScenarios — F-1~F-6 六个 RDMA 异常场景

必选场景：制造 RDMA 网络异常。
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# F-2: ECN 标记阈值错配
# ============================================================================

class ECNMisconfigurationScenario(BaseScenario):
    """
    F-2: ECN 标记阈值错配。
    
    通过 H3C 交换机 CLI 修改 ECN 阈值设置。
    
    效果:
    - DCQCN 频繁触发速率降低
    - RDMA 吞吐不稳定，出现周期性波动
    """
    
    @property
    def name(self) -> str:
        return "ecn_misconfiguration"
    
    @property
    def description(self) -> str:
        return "ECN 标记阈值错配 — 修改交换机 ECN 配置"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        # TODO: 需要 SwitchChannel 实现
        # 这里是伪代码，展示设计意图
        
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        
        inject_commands = [
            "system-view",
            f"interface {interface}",
            "qos wred apply ecn",
            "qos wred queue 3 ecn",
            "quit",
        ]
        
        recover_commands = [
            "system-view",
            f"interface {interface}",
            "undo qos wred apply ecn",
            "quit",
        ]
        
        logger.info(f"注入 ECN 错配: switch={switch}, interface={interface}")
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="switch",
            target=switch,
            inject_action="ecn_misconfig",
            inject_params={"interface": interface},
            recover_action="ecn_restore",
            recover_params={"interface": interface},
        )
        
        # TODO: 实际执行需要 SwitchChannel
        logger.warning("SwitchChannel not implemented, ECN injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        logger.info(f"恢复 ECN 配置: switch={switch}")
        
        # TODO: 实际执行需要 SwitchChannel
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "rdma_retrans": 'rdma_retransmissions_total',
        }


# ============================================================================
# F-4: RDMA 链路间歇性中断
# ============================================================================

class RDMALinkFlapScenario(BaseScenario):
    """
    F-4: RDMA 链路间歇性中断。
    
    通过交换机 CLI 间歇性 shutdown/undo shutdown 端口。
    
    效果:
    - RDMA 连接周期性断开重建
    - NCCL 通信超时重试
    - 训练任务可能 hang 或 crash
    """
    
    @property
    def name(self) -> str:
        return "rdma_link_flap"
    
    @property
    def description(self) -> str:
        return "RDMA 链路间歇性中断 — 交换机端口 flap"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        flap_interval = ctx.params.get("flap_interval", 30)
        flap_duration = ctx.params.get("flap_duration", 5)
        
        # 构建 flap 脚本
        inject_script = f"""
while true; do
    system-view
    interface {interface}
    shutdown
    quit
    sleep {flap_duration}
    system-view
    interface {interface}
    undo shutdown
    quit
    sleep {flap_interval}
done
"""
        
        logger.info(
            f"注入 RDMA 链路 flap: switch={switch}, interface={interface}, "
            f"interval={flap_interval}s, duration={flap_duration}s"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="switch",
            target=switch,
            inject_action="link_flap_script",
            inject_params={
                "interface": interface,
                "flap_interval": flap_interval,
                "flap_duration": flap_duration,
            },
            recover_action="stop_flap_script",
            recover_params={"interface": interface},
        )
        
        # TODO: 实际执行需要 SwitchChannel
        logger.warning("SwitchChannel not implemented, link flap injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        
        logger.info(f"恢复 RDMA 链路: switch={switch}, interface={interface}")
        
        # 停止 flap 脚本，确保端口 up
        # TODO: 实际执行需要 SwitchChannel
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_link_status": 'rdma_link_status',
            "nccl_allreduce_latency": 'nccl_allreduce_latency_seconds',
        }


# 注册场景
SCENARIOS = [
    ECNMisconfigurationScenario,
    RDMALinkFlapScenario,
]
```

---

### Phase 4: 实现测试 (优先级: P1)

#### Step 4.1: 测试配置

**文件**: `tests/conftest.py`

```python
"""
pytest 配置和 fixtures
"""
import pytest
import asyncio
from pathlib import Path
import tempfile

from fault_injector.config.schema import (
    FaultInjectorConfig,
    GlobalConfig,
    SafetyConfig,
    TargetNodeConfig,
    SSHConfig,
    ScenarioConfig,
)
from fault_injector.channels.ssh import SSHChannel
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.safety.guard import SafetyGuard


@pytest.fixture
def event_loop():
    """创建事件循环"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir():
    """临时目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config() -> FaultInjectorConfig:
    """示例配置"""
    return FaultInjectorConfig(
        global_=GlobalConfig(
            session_dir="./fault-reports/sessions/",
            log_level="DEBUG",
            safety=SafetyConfig(
                require_confirmation=False,
                auto_recover_timeout=60,
                dry_run=True,
            ),
        ),
        inventory={
            "nodes": [
                TargetNodeConfig(
                    name="test-node-1",
                    ssh=SSHConfig(
                        host="192.168.1.100",
                        port=22,
                        user="root",
                        key_file="~/.ssh/id_rsa",
                        use_sudo=True,
                    ),
                    interface="eth0",
                )
            ]
        },
        scenarios={
            "network_jitter": ScenarioConfig(
                name="network_jitter",
                enabled=True,
                target_nodes=["test-node-1"],
                params={
                    "delay_ms": 50,
                    "jitter_ms": 100,
                    "duration": 30,
                },
            )
        },
    )


@pytest.fixture
def dry_run_ssh(sample_config) -> SSHChannel:
    """Dry-run 模式的 SSH Channel"""
    inventory = {}
    for group, nodes in sample_config.inventory.items():
        for node in nodes:
            inventory[node.name] = node
    
    return SSHChannel(
        inventory=inventory,
        dry_run=True,
        wal=None,
        guard=None,
    )


@pytest.fixture
def rollback_journal(temp_dir) -> RollbackJournal:
    """回滚日志"""
    return RollbackJournal(temp_dir)


@pytest.fixture
def safety_guard() -> SafetyGuard:
    """安全守卫"""
    return SafetyGuard()
```

#### Step 4.2: 场景测试

**文件**: `tests/test_scenarios.py`

```python
"""
场景测试
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from fault_injector.scenarios.network_jitter import NetworkJitterScenario
from fault_injector.scenarios.base import FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult


class TestNetworkJitterScenario:
    """NetworkJitterScenario 测试"""
    
    def test_scenario_properties(self):
        """测试场景属性"""
        scenario = NetworkJitterScenario()
        
        assert scenario.name == "network_jitter"
        assert "tc netem" in scenario.description
        assert scenario.layer == "os"
    
    @pytest.mark.asyncio
    async def test_inject_dry_run(self, dry_run_ssh, rollback_journal, safety_guard):
        """测试 dry-run 注入"""
        scenario = NetworkJitterScenario()
        
        ctx = FaultContext(
            ssh=dry_run_ssh,
            rollback=rollback_journal,
            guard=safety_guard,
            target_node="test-node-1",
            params={
                "delay_ms": 50,
                "jitter_ms": 100,
                "interface": "eth0",
            },
            fault_id="test-fault-1",
        )
        
        result = await scenario.inject(ctx)
        
        assert result.success
        assert result.fault_id == "test-fault-1"
    
    @pytest.mark.asyncio
    async def test_recover_dry_run(self, dry_run_ssh, rollback_journal, safety_guard):
        """测试 dry-run 恢复"""
        scenario = NetworkJitterScenario()
        
        ctx = FaultContext(
            ssh=dry_run_ssh,
            rollback=rollback_journal,
            guard=safety_guard,
            target_node="test-node-1",
            params={"interface": "eth0"},
            fault_id="test-fault-1",
        )
        
        result = await scenario.recover(ctx)
        
        assert result.success
    
    @pytest.mark.asyncio
    async def test_verify_dry_run(self, dry_run_ssh, rollback_journal, safety_guard):
        """测试 dry-run 验证"""
        scenario = NetworkJitterScenario()
        
        ctx = FaultContext(
            ssh=dry_run_ssh,
            rollback=rollback_journal,
            guard=safety_guard,
            target_node="test-node-1",
            params={"interface": "eth0"},
            fault_id="test-fault-1",
        )
        
        result = await scenario.verify(ctx)
        
        # dry-run 模式下假设验证通过
        assert result is True
    
    def test_monitor_queries(self):
        """测试监控查询"""
        scenario = NetworkJitterScenario()
        queries = scenario.monitor_queries()
        
        assert "inference_p50" in queries
        assert "inference_p99" in queries
        assert "vllm" in queries["inference_p50"]
```

---

## 4. 实现顺序建议

### Sprint 1 (Week 1): 基础设施

1. ✅ `config/` - 配置层
2. ✅ `safety/` - 安全层
3. ✅ `channels/base.py`, `channels/ssh.py` - 基础 Channel
4. `channels/prometheus.py` - Prometheus Channel
5. `orchestrator/session.py` - Session 管理
6. `orchestrator/engine.py` - 编排引擎骨架

### Sprint 2 (Week 2): 必选场景

1. `scenarios/vllm_latency.py` - RC-1, RC-3, RC-5
2. `scenarios/rdma_anomaly.py` - F-2, F-4
3. `channels/switch.py` - SwitchChannel (用于 RDMA 场景)
4. `tests/test_scenarios.py` - 场景测试

### Sprint 3 (Week 3): 更多 Channel + Agent

1. `channels/redfish.py` - RedfishChannel
2. `channels/kubernetes.py` - K8sChannel
3. `agents/base.py` - Agent 基类
4. `agents/os_fault.py` - OSFaultAgent

### Sprint 4 (Week 4): 报告 + 集成测试

1. `reporting/timeline.py` - 时间线
2. `reporting/html_report.py` - HTML 报告
3. `tests/test_integration.py` - 集成测试
4. 文档完善

---

## 5. 关键设计决策

### 5.1 WAL 回滚日志

**位置**: `safety/rollback.py`

**格式**: JSONL (JSON Lines)

```
{"fault_id":"xxx","injected_at":"...","channel":"ssh","target":"node1","inject_action":"tc_add_delay","recover_action":"tc_del_qdisc","status":"active"}
```

**保证**: 
- 每条记录在注入前写入并 fsync
- 崩溃后可通过 `--resume` 恢复

### 5.2 安全守卫

**位置**: `safety/guard.py`

**硬编码拦截**:
- `rm -rf /`
- `dd if=/dev/zero`
- `:(){ :|:& };:` (fork bomb)
- BMC 网络配置修改
- 删除 K8s namespace

### 5.3 Channel 层复用

**原则**: 遵循 `channel.md` 设计

- BaseChannel 统一 dry_run 和 WAL 集成
- 所有 Channel 继承 BaseChannel
- 共享 Channel 放在独立 `channels/` 包

---

## 6. 测试策略

### 6.1 单元测试

- 配置解析测试
- Channel mock 测试
- 场景逻辑测试 (dry-run 模式)

### 6.2 集成测试

- 完整注入/恢复流程
- WAL 崩溃恢复
- 安全守卫拦截

### 6.3 端到端测试

- 在真实环境中验证
- 需要配置 SSH 连接和 sudo 权限

---

## 7. 配置示例

配置文件应放在项目根目录或 `config/` 目录下（不放在 `fault_injector/` 内部）。

**示例**: `config/fault-injector-config.yaml`

```yaml
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
    - name: "test-node-1"
      ssh:
        host: "192.168.1.100"
        port: 22
        user: "yuyonghao"
        key_file: "~/.ssh/id_rsa"
        use_sudo: true
      interface: "eth0"
      roles: []

scenarios:
  network_jitter:
    name: "network_jitter"
    enabled: true
    target_nodes: ["test-node-1"]
    params:
      delay_ms: 50
      jitter_ms: 100
      distribution: "pareto"
      loss_pct: 0
      duration: 60
      interface: "eth0"
```

---

## 8. 附录：场景完整清单

| ID | 场景名 | 描述 | 层级 | 状态 |
|----|--------|------|------|------|
| RC-1 | gpu_contention | GPU 资源争抢 | hardware | 待实现 |
| RC-2 | network_jitter | 网络延迟抖动 | os | ✅ 已实现 |
| RC-3 | storage_io_interference | 存储 I/O 干扰 | os | 待实现 |
| RC-4 | platform_cascade | 平台组件级联延迟 | platform | 待实现 |
| RC-5 | os_resource_pressure | OS 资源压力 | os | 待实现 |
| RC-6 | thermal_throttling | 热降频 | hardware | 待实现 |
| F-1 | pfc_deadlock | PFC 死锁 | hardware | 待实现 |
| F-2 | ecn_misconfiguration | ECN 标记阈值错配 | hardware | 待实现 |
| F-3 | rdma_load_imbalance | 不均衡 RDMA 负载 | hardware | 待实现 |
| F-4 | rdma_link_flap | RDMA 链路间歇性中断 | hardware | 待实现 |
| F-5 | roce_mtu_mismatch | RoCE 网络 MTU 不一致 | hardware | 待实现 |
| F-6 | rdma_qos_downgrade | RDMA QoS 降级 | hardware | 待实现 |