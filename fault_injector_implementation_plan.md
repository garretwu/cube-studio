# Fault Injector Implementation Plan

## Overview

实现 Cube Studio 平台的故障注入系统，第一阶段专注于 **RC-2 网络延迟场景**，实现注入和回滚功能，在虚拟环境中进行验证。

## Scope

- **目标场景**: RC-2 网络延迟 (Network Jitter) - 使用 `tc netem` 注入网络延迟
- **运行模式**: 独立运行，不与 load_simulator 联动
- **LLM 诊断**: 暂不实现，专注确定性注入/回滚
- **环境**: 虚拟环境（Linux 虚拟机）

## Types

### 配置相关类型

```python
# config/schema.py

class SSHConfig(BaseModel):
    """SSH 连接配置"""
    host: str
    port: int = 22
    user: str = "root"
    key_file: str | None = None
    password: str | None = None
    timeout: int = 30

class TargetNodeConfig(BaseModel):
    """目标节点配置"""
    name: str
    ssh: SSHConfig
    interface: str = "eth0"  # 网络接口

class SafetyConfig(BaseModel):
    """安全配置"""
    require_confirmation: bool = True
    auto_recover_timeout: int = 600
    dry_run: bool = False
    max_concurrent_faults: int = 3
    excluded_nodes: list[str] = []

class GlobalConfig(BaseModel):
    """全局配置"""
    session_dir: str = "./fault-reports/sessions/"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    safety: SafetyConfig = SafetyConfig()

class NetworkJitterParams(BaseModel):
    """网络延迟场景参数"""
    delay_ms: int = 50              # 基础延迟 (毫秒)
    jitter_ms: int = 100            # 抖动范围 (毫秒)
    distribution: Literal["normal", "pareto", "paretonormal"] = "pareto"
    loss_pct: float = 0             # 丢包率 (%)
    duration: int = 300             # 持续时间 (秒)
    interface: str = "eth0"         # 网络接口

class ScenarioConfig(BaseModel):
    """场景配置"""
    name: str
    enabled: bool = True
    target_nodes: list[str]
    params: dict = {}

class FaultInjectorConfig(BaseModel):
    """完整配置"""
    global_: GlobalConfig = Field(alias="global", default=GlobalConfig())
    inventory: dict[str, list[TargetNodeConfig]] = {}
    scenarios: dict[str, ScenarioConfig] = {}
```

### 执行相关类型

```python
# orchestrator/session.py

class SessionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"

class SessionPhase(str, Enum):
    INIT = "init"
    INJECT = "inject"
    OBSERVE = "observe"
    RECOVER = "recover"
    VERIFY = "verify"

class Session(BaseModel):
    session_id: str
    config_hash: str
    started_at: datetime
    status: SessionStatus = SessionStatus.RUNNING
    phase: SessionPhase = SessionPhase.INIT
    active_faults: list[str] = []
    rollback_journal_path: str = ""
    events: list[dict] = []

# safety/rollback.py

class RollbackEntryStatus(str, Enum):
    ACTIVE = "active"
    RECOVERED = "recovered"
    FAILED = "failed"

class RollbackEntry(BaseModel):
    fault_id: str
    injected_at: datetime
    channel: str
    target: str
    inject_action: str
    recover_action: str
    recover_params: dict
    status: RollbackEntryStatus = RollbackEntryStatus.ACTIVE

# channels/base.py

class ChannelResult(BaseModel):
    success: bool
    output: str = ""
    error: str = ""
    dry_run: bool = False
    duration_ms: int = 0

# scenarios/base.py

class ScenarioResult(BaseModel):
    scenario_name: str
    success: bool
    inject_time: datetime | None = None
    recover_time: datetime | None = None
    verification_passed: bool | None = None
    error: str | None = None
```

## Files

### 新建文件列表

| 文件路径 | 用途 | 优先级 |
|---------|------|--------|
| `fault_injector/__init__.py` | 包初始化 | P0 |
| `fault_injector/__main__.py` | python -m 入口 | P0 |
| `fault_injector/cli.py` | Click CLI 命令 | P0 |
| `fault_injector/config/__init__.py` | 配置子包 | P0 |
| `fault_injector/config/schema.py` | Pydantic 配置模型 | P0 |
| `fault_injector/config/defaults.py` | 默认配置 | P0 |
| `fault_injector/config/loader.py` | YAML 配置加载 | P0 |
| `fault_injector/safety/__init__.py` | 安全子包 | P0 |
| `fault_injector/safety/rollback.py` | WAL 回滚日志 | P0 |
| `fault_injector/safety/guard.py` | SafetyGuard | P0 |
| `fault_injector/channels/__init__.py` | Channel 子包 | P0 |
| `fault_injector/channels/base.py` | BaseChannel | P0 |
| `fault_injector/channels/ssh.py` | SSHChannel | P0 |
| `fault_injector/orchestrator/__init__.py` | 编排子包 | P0 |
| `fault_injector/orchestrator/session.py` | Session 管理 | P0 |
| `fault_injector/orchestrator/watchdog.py` | 超时回滚看门狗 | P0 |
| `fault_injector/orchestrator/engine.py` | 主执行引擎 | P0 |
| `fault_injector/agents/__init__.py` | Agent 子包 | P0 |
| `fault_injector/agents/base.py` | BaseAgent | P0 |
| `fault_injector/agents/os_fault.py` | OS 层故障 Agent | P0 |
| `fault_injector/scenarios/__init__.py` | 场景子包 | P0 |
| `fault_injector/scenarios/base.py` | BaseScenario | P0 |
| `fault_injector/scenarios/network_jitter.py` | RC-2 网络延迟 | P0 |
| `fault_injector/scenarios/registry.py` | 场景注册表 | P0 |
| `fault_injector/tests/__init__.py` | 测试包 | P1 |
| `fault_injector/tests/test_network_jitter.py` | 场景测试 | P1 |
| `fault-injector-config.yaml` | 示例配置文件 | P0 |

### 配置文件更新

| 文件路径 | 修改内容 |
|---------|---------|
| `pyproject.toml` 或 `setup.py` | 添加 fault_injector 包依赖 |

## Functions

### CLI 函数

| 函数名 | 文件 | 签名 | 用途 |
|--------|------|------|------|
| `main` | `cli.py` | `() -> None` | Click group 入口 |
| `run_cmd` | `cli.py` | `(config_path, dry_run, scenario, timeout) -> None` | 执行故障注入 |
| `recover_cmd` | `cli.py` | `(session_id) -> None` | 手动恢复故障 |
| `list_scenarios_cmd` | `cli.py` | `() -> None` | 列出可用场景 |
| `validate_config_cmd` | `cli.py` | `(config_path) -> None` | 验证配置文件 |

### 配置函数

| 函数名 | 文件 | 签名 | 用途 |
|--------|------|------|------|
| `load_config` | `config/loader.py` | `(path: str) -> FaultInjectorConfig` | 加载 YAML 配置 |
| `get_default_config` | `config/defaults.py` | `() -> FaultInjectorConfig` | 获取默认配置 |

### 回滚函数

| 函数名 | 文件 | 签名 | 用途 |
|--------|------|------|------|
| `record` | `safety/rollback.py` | `(fault_id, recover_action, recover_params) -> None` | 记录回滚条目 |
| `recover_all` | `safety/rollback.py` | `() -> list[RecoveryResult]` | 恢复所有活跃故障 |
| `recover_fault` | `safety/rollback.py` | `(fault_id: str) -> RecoveryResult` | 恢复单个故障 |

### Channel 函数

| 函数名 | 文件 | 签名 | 用途 |
|--------|------|------|------|
| `execute` | `channels/base.py` | `(action, params, recovery_action, recovery_params) -> ChannelResult` | 执行操作 |
| `_execute_impl` | `channels/base.py` | `(action, params) -> ChannelResult` | 实际执行（抽象） |
| `run_command` | `channels/ssh.py` | `(node, command, recovery_command) -> ChannelResult` | SSH 执行命令 |

### 编排函数

| 函数名 | 文件 | 签名 | 用途 |
|--------|------|------|------|
| `run` | `orchestrator/engine.py` | `(config: FaultInjectorConfig) -> SessionResult` | 执行故障注入会话 |
| `_run_scenario` | `orchestrator/engine.py` | `(scenario: BaseScenario, ctx: FaultContext) -> ScenarioResult` | 执行单个场景 |
| `create` | `orchestrator/session.py` | `(config: FaultInjectorConfig) -> Session` | 创建新会话 |
| `save` | `orchestrator/session.py` | `() -> None` | 保存会话状态 |
| `load` | `orchestrator/session.py` | `(session_id: str) -> Session` | 加载会话 |

### 场景函数

| 函数名 | 文件 | 签名 | 用途 |
|--------|------|------|------|
| `inject` | `scenarios/base.py` | `(ctx: FaultContext) -> InjectResult` | 注入故障（抽象） |
| `recover` | `scenarios/base.py` | `(ctx: FaultContext) -> RecoverResult` | 恢复故障（抽象） |
| `verify` | `scenarios/base.py` | `(ctx: FaultContext) -> bool` | 验证恢复（抽象） |
| `inject` | `scenarios/network_jitter.py` | `(ctx: FaultContext) -> InjectResult` | 注入网络延迟 |
| `recover` | `scenarios/network_jitter.py` | `(ctx: FaultContext) -> RecoverResult` | 恢复网络延迟 |

## Classes

### BaseChannel

```python
class BaseChannel(ABC):
    """Channel 基类 — 统一 dry_run 和 WAL 集成"""
    
    def __init__(self, dry_run: bool = False, wal: RollbackJournal | None = None)
    
    async def execute(self, action: str, params: dict,
                      recovery_action: str | None = None,
                      recovery_params: dict | None = None) -> ChannelResult
    
    @abstractmethod
    async def _execute_impl(self, action: str, params: dict) -> ChannelResult
    
    def _is_forbidden(self, action: str, params: dict) -> bool
```

### RollbackJournal

```python
class RollbackJournal:
    """WAL 回滚日志 — 核心安全机制"""
    
    def __init__(self, journal_path: Path)
    
    def record(self, fault_id: str, recover_action: str, 
               recover_params: dict) -> None
    """写前记录：在故障注入之前调用，立即 fsync"""
    
    async def recover_all(self) -> list[RecoveryResult]
    """按注入逆序恢复所有活跃故障"""
    
    async def recover_fault(self, fault_id: str) -> RecoveryResult
    """恢复单个故障"""
    
    def get_active_faults(self) -> list[RollbackEntry]
    """获取所有活跃故障"""
```

### SSHChannel

```python
class SSHChannel(BaseChannel):
    """SSH 命令执行 Channel"""
    
    def __init__(self, inventory: dict[str, SSHConfig], 
                 dry_run: bool = False, 
                 wal: RollbackJournal | None = None,
                 pool_size: int = 5,
                 command_timeout: int = 60)
    
    async def run_command(self, node: str, command: str,
                          recovery_command: str | None = None,
                          timeout: int | None = None) -> ChannelResult
    """在目标节点执行命令"""
    
    async def _execute_impl(self, action: str, params: dict) -> ChannelResult
```

### OrchestrationEngine

```python
class OrchestrationEngine:
    """确定性编排引擎"""
    
    def __init__(self, config: FaultInjectorConfig)
    
    async def run(self) -> SessionResult
    """执行故障注入会话"""
    
    async def _run_scenario(self, scenario: BaseScenario, 
                            ctx: FaultContext) -> ScenarioResult
    """执行单个场景"""
    
    async def _init_channels(self) -> dict
    """初始化 Channel 连接"""
    
    async def _preflight_check(self) -> None
    """预检：验证节点连通性"""
```

### BaseScenario

```python
class BaseScenario(ABC):
    """故障场景基类"""
    
    @property
    @abstractmethod
    def name(self) -> str
    
    @abstractmethod
    async def inject(self, ctx: FaultContext) -> InjectResult
    
    @abstractmethod
    async def recover(self, ctx: FaultContext) -> RecoverResult
    
    @abstractmethod
    async def verify(self, ctx: FaultContext) -> bool
```

### NetworkJitterScenario

```python
class NetworkJitterScenario(BaseScenario):
    """RC-2: 网络延迟场景"""
    
    name = "network_jitter"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        """
        使用 tc netem 注入网络延迟
        命令: tc qdisc add dev eth0 root netem delay 50ms 100ms distribution pareto
        """
        params = NetworkJitterParams(**ctx.params)
        cmd = self._build_tc_command(params)
        recovery_cmd = f"tc qdisc del dev {params.interface} root"
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=cmd,
            recovery_command=recovery_cmd
        )
        return InjectResult(success=result.success, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        """恢复：由 WAL 自动处理"""
        return RecoverResult(success=True)
    
    async def verify(self, ctx: FaultContext) -> bool:
        """验证：检查 tc qdisc 是否已删除"""
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="tc qdisc show dev " + ctx.params.get("interface", "eth0")
        )
        return "netem" not in result.output
    
    def _build_tc_command(self, params: NetworkJitterParams) -> str:
        cmd = f"tc qdisc add dev {params.interface} root netem delay {params.delay_ms}ms {params.jitter_ms}ms"
        if params.distribution != "normal":
            cmd += f" distribution {params.distribution}"
        if params.loss_pct > 0:
            cmd += f" loss {params.loss_pct}%"
        return cmd
```

## Dependencies

### 新增依赖

```toml
# pyproject.toml 或 requirements.txt

[project.dependencies]
# 现有依赖 (load_simulator 已有)
click = ">=8.1"
pydantic = ">=2.5"
pyyaml = ">=6.0"
rich = ">=13.0"

# 新增依赖
asyncssh = ">=2.14"      # 异步 SSH 客户端
```

### 依赖说明

| 依赖 | 版本 | 用途 |
|------|------|------|
| `click` | >=8.1 | CLI 框架 |
| `pydantic` | >=2.5 | 配置校验 |
| `pyyaml` | >=6.0 | YAML 解析 |
| `rich` | >=13.0 | 终端美化输出 |
| `asyncssh` | >=2.14 | 异步 SSH 连接 |

## Testing

### 单元测试

```python
# tests/test_network_jitter.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from fault_injector.scenarios.network_jitter import NetworkJitterScenario
from fault_injector.orchestrator.session import FaultContext

class TestNetworkJitterScenario:
    """RC-2 网络延迟场景测试"""
    
    @pytest.fixture
    def scenario(self):
        return NetworkJitterScenario()
    
    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock(spec=FaultContext)
        ctx.ssh = AsyncMock()
        ctx.target_node = "test-node"
        ctx.params = {
            "delay_ms": 50,
            "jitter_ms": 100,
            "distribution": "pareto",
            "interface": "eth0"
        }
        return ctx
    
    @pytest.mark.asyncio
    async def test_inject_success(self, scenario, mock_context):
        """测试成功注入网络延迟"""
        mock_context.ssh.run_command.return_value = MagicMock(
            success=True, output="", error=""
        )
        
        result = await scenario.inject(mock_context)
        
        assert result.success
        mock_context.ssh.run_command.assert_called_once()
        call_args = mock_context.ssh.run_command.call_args
        assert "tc qdisc add" in call_args.kwargs["command"]
        assert "netem delay 50ms 100ms" in call_args.kwargs["command"]
    
    @pytest.mark.asyncio
    async def test_inject_with_recovery(self, scenario, mock_context):
        """测试注入时注册恢复命令"""
        mock_context.ssh.run_command.return_value = MagicMock(
            success=True, output="", error=""
        )
        
        await scenario.inject(mock_context)
        
        call_args = mock_context.ssh.run_command.call_args
        assert call_args.kwargs["recovery_command"] == "tc qdisc del dev eth0 root"
    
    @pytest.mark.asyncio
    async def test_verify_recovered(self, scenario, mock_context):
        """测试恢复验证"""
        mock_context.ssh.run_command.return_value = MagicMock(
            success=True, output="qdisc mq 0: root", error=""
        )
        
        result = await scenario.verify(mock_context)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_verify_still_active(self, scenario, mock_context):
        """测试故障仍然存在"""
        mock_context.ssh.run_command.return_value = MagicMock(
            success=True, output="qdisc netem 1: root delay 50ms", error=""
        )
        
        result = await scenario.verify(mock_context)
        assert result is False
```

### 集成测试清单

- [ ] 干运行模式验证 (`--dry-run`)
- [ ] SSH 连接测试
- [ ] 网络延迟注入/恢复测试
- [ ] WAL 崩溃恢复测试
- [ ] 超时自动回滚测试

## Implementation Order

### Phase 1: 骨架与配置 (Day 1-2)

1. 创建包结构 (`__init__.py` 文件)
2. 实现 CLI 入口 (`cli.py`, `__main__.py`)
3. 实现 Pydantic 配置 (`config/schema.py`, `config/defaults.py`)
4. 实现配置加载 (`config/loader.py`)

### Phase 2: 安全核心 (Day 2-3)

5. 实现 WAL 回滚日志 (`safety/rollback.py`)
6. 实现 SafetyGuard (`safety/guard.py`)

### Phase 3: Channel 层 (Day 3-4)

7. 实现 BaseChannel (`channels/base.py`)
8. 实现 SSHChannel (`channels/ssh.py`)

### Phase 4: 编排引擎 (Day 4-5)

9. 实现 Session 管理 (`orchestrator/session.py`)
10. 实现 Watchdog 超时回滚 (`orchestrator/watchdog.py`)
11. 实现主引擎 (`orchestrator/engine.py`)

### Phase 5: 第一个场景 (Day 5-6)

12. 实现 BaseAgent (`agents/base.py`)
13. 实现 OSFaultAgent (`agents/os_fault.py`)
14. 实现 BaseScenario (`scenarios/base.py`)
15. 实现 NetworkJitter 场景 (`scenarios/network_jitter.py`)
16. 实现场景注册表 (`scenarios/registry.py`)

### Phase 6: 测试验证 (Day 6-7)

17. 编写单元测试
18. 创建示例配置文件
19. 虚拟环境端到端验证

---

## 示例配置文件

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
      interface: "eth0"

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

## 验证步骤

1. **干运行验证**
   ```bash
   python -m fault_injector --config fault-injector-config.yaml --dry-run
   ```

2. **实际注入**
   ```bash
   python -m fault_injector --config fault-injector-config.yaml --scenario network_jitter
   ```

3. **手动验证**
   ```bash
   # 在目标虚拟机上检查延迟
   ping -c 10 8.8.8.8
   
   # 查看 tc 配置
   tc qdisc show dev eth0
   ```

4. **恢复验证**
   ```bash
   # 等待自动恢复或手动恢复
   python -m fault_injector --recover --session <session_id>
   
   # 验证 tc 配置已删除
   tc qdisc show dev eth0