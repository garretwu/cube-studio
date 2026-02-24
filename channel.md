# Channel 层统一设计

## 1. 概述

Channel 是与外部系统交互的抽象层，定义统一的执行模型。所有 Agent（Load Simulator / Fault Injector / SRE Agent）通过 Channel 发送请求，Channel 负责认证、连接池、重试、dry-run、安全守卫和回滚注册。

Channel 分为三类：
- **共享 Channel**（§2–§3）：跨系统复用，放在独立 `channels/` 包中。
- **Load Simulator 专用 Channel**（§4）：仅 load-simulator 使用。
- **SRE Agent 专用 Channel**（§5）：仅 SRE Agent 使用。

---

## 2. BaseChannel 基类

采用 AIDC-auto-SRE 版本——最完整，包含 `_is_forbidden()` 安全守卫、WAL 集成和 dry_run 支持。

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

**与各文档版本的差异说明：**

| 特性 | load-simulator | fault-injector | AIDC-auto-SRE (本版本) |
|------|---------------|----------------|----------------------|
| `dry_run` | ✅ | ✅ | ✅ |
| `rollback` / WAL | ❌ (无基类) | ✅ (`rollback_journal`) | ✅ (`wal`) |
| `_is_forbidden()` 守卫 | ❌ | ❌ (子类自行实现) | ✅ (基类统一调用) |

> **注意**：load-simulator 的 `CubeStudioChannel` 原版不继承 `BaseChannel`（独立实现 dry_run），统一后应改为继承 `BaseChannel`。

---

## 3. 共享 Channel

### 3.1 CubeStudioChannel

Cube Studio REST API 客户端，合并 load-simulator（全量 API 方法）和 fault-injector（简化 fault 视角）两个版本。

```python
class CubeStudioChannel(BaseChannel):
    """
    Cube Studio REST API 客户端。
    - JWT 或 username 认证 (Authorization header)
    - 自动重试 (指数退避)
    - dry-run 模式: 仅校验请求格式
    - 请求级指标自动采集
    - 目标端点:
        /inferenceservice_modelview/api/ — 推理服务 CRUD
        /pipeline_modelview/api/        — Pipeline CRUD
        /notebook_modelview/api/        — Notebook CRUD
        /k8s/                          — K8s 资源查询
    """
    def __init__(self, base_url: str, auth_config: AuthConfig,
                 dry_run: bool = False, timeout: int = 30,
                 wal: RollbackJournal | None = None):
        super().__init__(dry_run, wal)
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers=self._build_auth_headers(auth_config),
            timeout=timeout,
        )
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

    async def get_service_status(self, service_name: str) -> dict:
        """GET /inferenceservice_modelview/api/?_filters=[name=...]"""
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

    # ---- fault-injector 视角补充 ----
    async def update_inference_service(self, service_name: str,
                                        params: dict) -> ExecResult:
        """POST /inferenceservice_modelview/api/deploy/update/ (按 service_name)"""
        ...
```

### 3.2 PrometheusChannel

Prometheus HTTP API 查询。只读操作，无需回滚。采用 fault-injector 版本（包含 baseline/deviation 扩展，是 load-simulator 版本的超集）。

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

### 3.3 SSHChannel

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

### 3.4 RedfishChannel

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

### 3.5 SwitchChannel

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

### 3.6 K8sChannel

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

---

## 4. Load Simulator 专用 Channel

### 4.1 InferenceChannel

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

### 4.2 NotebookChannel

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

---

## 5. SRE Agent 专用 Channel

### 5.1 LogChannel

```python
class LogChannel:
    """统一日志访问：K8s Pod 日志 + 系统日志 + Cube Studio 审计日志"""

    def __init__(self, k8s: K8sChannel, ssh: SSHChannel,
                 cube_studio: CubeStudioChannel):
        self.k8s = k8s
        self.ssh = ssh
        self.cube_studio = cube_studio

    async def read_pod_logs(self, pod: str, namespace: str,
                            tail: int = 200, since: str | None = None
                            ) -> list[str]:
        """读取 K8s Pod 日志"""
        return await self.k8s.get_pod_logs(pod, namespace,
                                           tail_lines=tail, since_time=since)

    async def search_pod_logs(self, pattern: str, namespace: str,
                              pod_selector: str | None = None) -> list[dict]:
        """跨 Pod 搜索日志（正则匹配）"""
        pods = await self.k8s.get_pods(namespace=namespace,
                                       label_selector=pod_selector)
        results = []
        for pod in pods:
            logs = await self.read_pod_logs(pod["name"], namespace)
            for line in logs:
                if re.search(pattern, line):
                    results.append({"pod": pod["name"], "line": line})
        return results

    async def read_system_log(self, node: str, log_path: str = "/var/log/syslog",
                              tail: int = 100) -> list[str]:
        """通过 SSH 读取节点系统日志

        P0 安全修复：log_path 使用白名单 + shlex.quote() 防命令注入。
        """
        import shlex
        ALLOWED_LOG_PATHS = {
            "/var/log/syslog", "/var/log/messages", "/var/log/kern.log",
            "/var/log/dmesg", "/var/log/auth.log", "/var/log/gpu-manager.log",
        }
        # 路径白名单校验
        if log_path not in ALLOWED_LOG_PATHS:
            raise SafetyViolationError(
                f"log_path {log_path!r} not in allowed list: {ALLOWED_LOG_PATHS}")
        # 即使白名单也 quote，防御纵深
        output = await self.ssh.run_command(
            node, f"tail -n {int(tail)} {shlex.quote(log_path)}")
        return output.strip().split("\n")

    async def read_dmesg(self, node: str, filter_str: str | None = None
                         ) -> list[str]:
        """读取内核日志

        P0 安全修复：filter_str 使用 shlex.quote() 防命令注入。
        """
        import shlex
        cmd = "dmesg --time-format iso"
        if filter_str:
            # 限制 filter_str 长度并 quote
            if len(filter_str) > 100:
                raise SafetyViolationError("filter_str too long (max 100 chars)")
            cmd += f" | grep -i {shlex.quote(filter_str)}"
        output = await self.ssh.run_command(node, cmd)
        return output.strip().split("\n")
```

### 5.2 AlertChannel

```python
class AlertChannel:
    """告警源集成：Prometheus Alertmanager + Webhook 接收"""

    def __init__(self, alertmanager_url: str):
        self.client = httpx.AsyncClient(base_url=alertmanager_url)

    async def get_active_alerts(self, filter_labels: dict | None = None
                                ) -> list[Alert]:
        """获取当前活跃告警"""
        resp = await self.client.get("/api/v2/alerts",
                                     params={"filter": self._build_filter(filter_labels)})
        return [Alert.model_validate(a) for a in resp.json()]

    async def get_alert_history(self, alert_name: str,
                                lookback: str = "24h") -> list[Alert]:
        """获取告警历史"""
        # 通过 Prometheus query 获取告警触发历史
        pass

    async def silence_alert(self, alert_id: str, duration: str,
                            comment: str) -> str:
        """静默告警（修复期间避免告警风暴）"""
        resp = await self.client.post("/api/v2/silences", json={
            "matchers": [{"name": "alertname", "value": alert_id}],
            "startsAt": datetime.now().isoformat(),
            "endsAt": (datetime.now() + parse_duration(duration)).isoformat(),
            "comment": comment,
            "createdBy": "sre-agent",
        })
        return resp.json()["silenceID"]

class Alert(BaseModel):
    """告警数据结构

    P1 修复：统一为 Pydantic BaseModel（原 @dataclass 与 model_validate() 和
    IncidentRecord(BaseModel) 中的 alert: Alert 字段不兼容）。
    """
    alert_name: str
    severity: Literal["critical", "warning", "info"]
    labels: dict[str, str]                      # {instance, job, namespace, ...}
    annotations: dict[str, str]                 # {summary, description, ...}
    starts_at: datetime
    ends_at: datetime | None = None
    fingerprint: str
    status: Literal["firing", "resolved"]
```

### 5.3 OntologyChannel

```python
class OntologyChannel:
    """数字孪生 CRUD 操作"""

    def __init__(self, ontology: OntologyGraph):
        self.ontology = ontology

    async def query(self, entity_type: str,
                    filters: dict | None = None) -> list[dict]:
        return self.ontology.find_entities(entity_type, filters)

    async def get_blast_radius(self, entity_id: str) -> dict:
        return self.ontology.get_blast_radius(entity_id)

    async def get_path(self, from_id: str, to_id: str) -> list[str] | None:
        return self.ontology.get_path(from_id, to_id)

    async def refresh_entity(self, entity_id: str) -> dict:
        """实时刷新实体状态（重新从源系统采集）"""
        # 根据实体类型调用对应 discovery 方法
        pass
```

### 5.4 KnowledgeBaseChannel

```python
class KnowledgeBaseChannel:
    """知识库 RAG 检索接口"""

    def __init__(self, store: KnowledgeStore):
        self.store = store

    async def search(self, query: str, category: str | None = None,
                     top_k: int = 5) -> list[KnowledgeChunk]:
        return await self.store.search(query, category=category, top_k=top_k)

    async def search_runbook(self, symptom: str) -> list[Runbook]:
        return await self.store.search_runbooks(symptom)
```

---

## 6. Channel 使用矩阵

| Channel | Load Simulator | Fault Injector | SRE Agent | 详见 |
|---------|:-:|:-:|:-:|------|
| **BaseChannel** (基类) | ✅ | ✅ | ✅ | §2 |
| **CubeStudioChannel** | ✅ | ✅ | ✅ | §3.1 |
| **PrometheusChannel** | ✅ | ✅ | ✅ | §3.2 |
| **SSHChannel** | — | ✅ | ✅ | §3.3 |
| **RedfishChannel** | — | ✅ | ✅ | §3.4 |
| **SwitchChannel** | — | ✅ | ✅ | §3.5 |
| **K8sChannel** | — | ✅ | ✅ | §3.6 |
| **InferenceChannel** | ✅ | — | — | §4.1 |
| **NotebookChannel** | ✅ | — | — | §4.2 |
| **LogChannel** | — | — | ✅ | §5.1 |
| **AlertChannel** | — | — | ✅ | §5.2 |
| **OntologyChannel** | — | — | ✅ | §5.3 |
| **KnowledgeBaseChannel** | — | — | ✅ | §5.4 |
