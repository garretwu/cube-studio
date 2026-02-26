# Fault Injector Tests Guide

本文档说明 `fault_injector/tests` 的新结构、运行方式和扩展规则。

## 1. 目录结构

```text
fault_injector/tests/
├── __init__.py                     # 保持不变
├── config/                         # 保持不变（设备测试配置）
│   └── switch_config.yaml
├── conftest.py                     # 全局 pytest fixtures
├── channel/                        # Channel 相关测试
│   ├── __init__.py
│   ├── test_ssh_channel.py         # SSH Channel 单元测试（mock）
│   ├── test_netconf_connection.py  # Switch NETCONF 连接测试（实设备）
│   └── test_switch_operations.py   # Switch 端口操作测试（实设备/可 dry-run）
├── scenario/                       # Scenario 相关测试
│   ├── __init__.py
│   └── test_scenarios.py
└── common/                         # 公共测试基类与工具
    ├── __init__.py
    └── test_base.py
```

设计原则：
- `channel/` 与 `scenario/` 分层清晰，方便定位与扩展。
- `conftest.py` 放在 `tests/` 根目录，供两类测试共享 fixture。
- 测试目录调整不影响 `fault_injector` 业务代码。

## 2. Channel 测试

### 2.1 单元测试（mock）

```bash
pytest fault_injector/tests/channel/test_ssh_channel.py -v
```

### 2.2 设备连接测试（NETCONF）

先确认 `fault_injector/tests/config/switch_config.yaml` 已配置。

```bash
python -m fault_injector.tests.channel.test_netconf_connection
```

### 2.3 设备操作测试（Switch 端口）

默认是安全模式（dry-run）：

```bash
python -m fault_injector.tests.channel.test_switch_operations
```

显式 dry-run：

```bash
python -m fault_injector.tests.channel.test_switch_operations --dry-run
```

真实执行（会修改端口状态）：

```bash
python -m fault_injector.tests.channel.test_switch_operations --real
```

## 3. Scenario 测试

运行全部场景单元测试：

```bash
pytest fault_injector/tests/scenario/test_scenarios.py -v
```

运行单个场景测试类：

```bash
pytest fault_injector/tests/scenario/test_scenarios.py::TestNetworkJitterScenario -v
pytest fault_injector/tests/scenario/test_scenarios.py::TestRoCEMTUMismatchScenario -v
```

## 4. 全量测试建议

仅 mock 单元测试（推荐在 CI 默认执行）：

```bash
pytest fault_injector/tests/channel/test_ssh_channel.py fault_injector/tests/scenario/test_scenarios.py -v
```

包含覆盖率：

```bash
pytest fault_injector/tests/channel/test_ssh_channel.py fault_injector/tests/scenario/test_scenarios.py -v --cov=fault_injector
```

## 5. 扩展规范

### 5.1 新增 Channel 测试

1. 在 `fault_injector/tests/channel/` 新建 `test_<channel_name>.py`。
2. 复用 `fault_injector/tests/conftest.py` 的 fixture。
3. 若有通用断言逻辑，沉淀到 `fault_injector/tests/common/test_base.py`。
4. 若该 channel 需要实设备测试，命名为 `test_<channel_name>_connection.py` 或 `test_<channel_name>_operations.py` 并在文档注明风险。

### 5.2 新增 Scenario 测试

1. 在 `fault_injector/tests/scenario/` 新建 `test_<scenario_name>.py`，或在 `test_scenarios.py` 添加新测试类。
2. 保持三类测试最小闭环：
   - 属性测试（`name/description/layer`）
   - 注入与恢复测试（`inject/recover`）
   - 验证测试（`verify`）
3. 参数化场景使用 `pytest.mark.parametrize`，避免复制测试代码。

### 5.3 新增通用 fixture

1. 优先放在 `fault_injector/tests/conftest.py`。
2. fixture 名称用业务语义命名，如 `mock_<channel>_channel`、`<scenario>_context`。
3. 仅当 fixture 只服务一个文件时，才放在该测试文件内部。

## 6. 常见问题

`ModuleNotFoundError: No module named 'fault_injector'`

```bash
cd d:/dev/cube-studio/cube-studio
pytest fault_injector/tests -v
```

`pytest` 没有发现异步测试

检查依赖：

```bash
pip install pytest pytest-asyncio
```

NETCONF/交换机测试失败

- 先跑连接测试：`python -m fault_injector.tests.channel.test_netconf_connection`
- 再跑 dry-run 操作测试：`python -m fault_injector.tests.channel.test_switch_operations --dry-run`
- 最后再考虑 `--real` 模式。
