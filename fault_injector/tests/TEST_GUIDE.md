# Fault Injector 测试指南

本文档描述如何测试 AIDC Auto-SRE Fault Injector 的场景，包括：
- **RC-2**: NetworkJitterScenario (网络延迟抖动)
- **F-5**: RoCEMTUMismatchScenario (RoCE 网络 MTU 不一致)

## 1. 测试环境要求

### 1.1 软件要求

- Python 3.10+ （已测试 Python 3.14）
- 必需的 Python 包：
  ```bash
  pip install pydantic pyyaml click rich asyncssh
  ```
- 运行单元测试还需要：
  ```bash
  pip install pytest pytest-asyncio
  ```

### 1.2 测试目标节点（端到端测试）

- 一台 Linux 虚拟机或物理机（用于 SSH 连接）
- 需要 root 权限或 sudo 权限（执行 tc/ip 命令）
- 需要安装 `iproute2` 包（提供 tc/ip 命令）

```bash
# 在目标节点上安装 iproute2
apt-get install iproute2 -y   # Debian/Ubuntu
yum install iproute -y        # CentOS/RHEL
```

## 2. 测试方法概述

| 测试类型 | 说明 | 位置 |
|----------|------|------|
| 单元测试 | 使用 mock，不需要真实节点 | `tests/mocktest/` |
| 端到端测试 | CLI 完整流程，需要真实 SSH 连接 | 手动执行 |

---

## 3. 单元测试（Mock Tests）

### 3.1 测试文件结构

```
fault_injector/tests/mocktest/
├── __init__.py            # 测试包初始化
├── conftest.py            # pytest fixtures（可扩展）
├── test_base.py           # 测试基类
├── test_ssh_channel.py    # SSH Channel 单元测试
└── test_scenarios.py      # 场景测试（NetworkJitter + RoCEMTU）
```

### 3.2 运行单元测试

```bash
cd d:/project/cubestudiofeature/cube-studio-yu

# 运行所有 mock 测试
pytest fault_injector/tests/mocktest/ -v

# 运行 SSH Channel 测试
pytest fault_injector/tests/mocktest/test_ssh_channel.py -v

# 运行场景测试
pytest fault_injector/tests/mocktest/test_scenarios.py -v

# 运行特定测试类
pytest fault_injector/tests/mocktest/test_scenarios.py::TestNetworkJitterScenario -v
pytest fault_injector/tests/mocktest/test_scenarios.py::TestRoCEMTUMismatchScenario -v

# 带覆盖率
pytest fault_injector/tests/mocktest/ -v --cov=fault_injector
```

### 3.3 预期输出

```
================ test session start ================
collected 50+ items

test_ssh_channel.py::TestSSHChannelDryRun::test_dry_run_property PASSED
test_ssh_channel.py::TestSSHChannelDryRun::test_run_command_dry_run_returns_success PASSED
...
test_scenarios.py::TestNetworkJitterScenario::test_scenario_name PASSED
test_scenarios.py::TestNetworkJitterScenario::test_inject_dry_run PASSED
test_scenarios.py::TestRoCEMTUMismatchScenario::test_scenario_name PASSED
...
================= passed in 5.00s =================
```

### 3.4 添加新测试

使用提供的模板类添加新场景测试：

```python
# 在 test_scenarios.py 中添加
class TestMyNewScenario(ScenarioTestTemplate):
    @pytest.fixture
    def scenario(self):
        return MyNewScenario()
    
    @pytest.fixture
    def context(self, base_context):
        base_context.params = {"my_param": "value"}
        return base_context
```

---

## 4. 端到端测试（CLI）

### 4.1 测试前准备

#### 配置 SSH 连接

```bash
# 在运行 fault_injector 的机器上生成密钥（如果没有）
ssh-keygen -t rsa -b 4096

# 将公钥复制到目标节点
ssh-copy-id yuyonghao@<目标节点IP>
```

#### 配置 sudo 免密码（重要）

```bash
# 在目标节点上执行
sudo visudo -f /etc/sudoers.d/fault-injector
```

添加以下内容：
```
yuyonghao ALL=(ALL) NOPASSWD: ALL
```

#### 验证 sudo 配置

```bash
sudo -u yuyonghao sudo tc qdisc show
# 应该不需要密码就能执行
```

### 4.2 创建测试配置文件

创建 `fault-injector-test.yaml`：

```yaml
# fault-injector-test.yaml
global:
  session_dir: "./fault-reports/sessions/"
  log_level: "DEBUG"
  safety:
    require_confirmation: false
    auto_recover_timeout: 300
    dry_run: false
    max_concurrent_faults: 3
    excluded_nodes: []

inventory:
  nodes:
    - name: "test-node-1"
      ssh:
        host: "<目标节点IP>"
        port: 22
        user: "yuyonghao"
        key_file: "~/.ssh/id_rsa"
        use_sudo: true
      interface: "eth0"
      roles: []
```

---

## 5. RC-2: NetworkJitterScenario 测试

### 5.1 场景配置

```yaml
scenarios:
  network_jitter:
    name: "network_jitter"
    enabled: true
    target_nodes: ["test-node-1"]
    params:
      interface: "eth0"
      delay_ms: 50
      jitter_ms: 100
      distribution: "pareto"
      loss_pct: 0
      duration: 30
```

### 5.2 测试步骤

#### 测试 1: 验证配置

```bash
python -m fault_injector validate-config fault-injector-test.yaml
```

#### 测试 2: Dry-Run 模式

```bash
python -m fault_injector run --dry-run --scenario network_jitter --config fault-injector-test.yaml
```

**预期输出：**
```
>>> 注入故障...
[DRY-RUN] SSH test-node-1: sudo tc qdisc add dev eth0 root netem delay 50ms 100ms distribution pareto
✓ 故障注入成功

>>> 观测期 (30s)...
  [DRY-RUN] 跳过观测期

>>> 恢复故障...
[DRY-RUN] SSH test-nsudo tc qdisc del dev eth0 root
✓ 故障恢复成功
```

#### 测试 3: 实际故障注入

```bash
python -m fault_injector run --scenario network_jitter --config fault-injector-test.yaml
```

**在目标节点上验证：**

```bash
# 查看 tc 配置
sudo tc qdisc show dev eth0
# 预期: qdisc netem 1: root refcnt 2 limit 1000 delay 50.0ms  100ms

# 测试延迟
ping -c 5 8.8.8.8
# 应该看到延迟增加约 50ms ± 100ms
```

### 5.3 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `interface` | 网络接口名 | `eth0` |
| `delay_ms` | 基础延迟（毫秒） | `50` |
| `jitter_ms` | 延迟抖动范围（毫秒） | `100` |
| `distribution` | 抖动分布: `normal`, `pareto`, `paretonormal` | `pareto` |
| `loss_pct` | 丢包率（百分比） | `0` |
| `duration` | 持续时间（秒） | `60` |

---

## 6. F-5: RoCEMTUMismatchScenario 测试

### 6.1 场景配置

```yaml
scenarios:
  roce_mtu_mismatch:
    name: "roce_mtu_mismatch"
    enabled: true
    target_nodes: ["test-node-1"]
    params:
      interface: "eth0"
      mtu: 1500        # 降级后的 MTU
      original_mtu: 9000  # 恢复时使用的原始 MTU
```

### 6.2 测试步骤

#### 测试 1: Dry-Run 模式

```bash
python -m fault_injector run --dry-run --scenario roce_mtu_mismatch --config fault-injector-test.yaml
```

**预期输出：**
```
>>> 注入故障...
[DRY-RUN] SSH test-node-1: sudo ip link set dev eth0 mtu 1500
✓ 故障注入成功

>>> 恢复故障...
[DRY-RUN] SSH test-node-1: sudo ip link set dev eth0 mtu 9000
✓ 故障恢复成功
```

#### 测试 2: 实际故障注入

```bash
python -m fault_injector run --scenario roce_mtu_mismatch --config fault-injector-test.yaml
```

**在目标节点上验证：**

```bash
# 查看 MTU
ip link show eth0
# 预期: ... mtu 1500 ...

# 或使用
cat /sys/class/net/eth0/mtu
# 预期: 1500
```

### 6.3 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `interface` | 网络接口名 | `eth0` |
| `mtu` | 故障注入后的 MTU 值 | `1500` |
| `original_mtu` | 恢复时使用的原始 MTU | `9000` |

### 6.4 测试场景效果

- RDMA 大包传输性能下降
- PFC 暂停帧频发
- RoCEv2 连接可能出现分段重传

---

## 7. 验证检查清单

### 7.1 单元测试验证

| 检查项 | 命令 | 预期结果 |
|--------|------|----------|
| SSH Channel 测试 | `pytest tests/mocktest/test_ssh_channel.py -v` | 全部 PASSED |
| NetworkJitter 测试 | `pytest tests/mocktest/test_scenarios.py::TestNetworkJitterScenario -v` | 全部 PASSED |
| RoCEMTU 测试 | `pytest tests/mocktest/test_scenarios.py::TestRoCEMTUMismatchScenario -v` | 全部 PASSED |

### 7.2 CLI 端到端验证

| 检查项 | 命令 | 预期结果 |
|--------|------|----------|
| CLI 入口 | `python -m fault_injector --help` | 显示帮助信息 |
| 配置验证 | `python -m fault_injector validate-config <config>` | 配置校验通过 |
| 场景列表 | `python -m fault_injector list-scenarios` | 显示所有场景 |
| Dry-run (NetworkJitter) | `python -m fault_injector run --dry-run --scenario network_jitter ...` | 仅打印操作 |
| Dry-run (RoCEMTU) | `python -m fault_injector run --dry-run --scenario roce_mtu_mismatch ...` | 仅打印操作 |

---

## 8. 常见问题排查

### 8.1 SSH 连接失败

```
错误: SSH 连接超时
```

**解决方案：**
1. 检查目标节点 IP 是否正确
2. 检查 SSH 服务是否运行：`systemctl status sshd`
3. 检查防火墙是否允许 SSH：`ufw allow 22`

### 8.2 sudo 密码问题

```
错误: sudo: a terminal is required to read the password
```

**解决方案：**
```bash
echo "yuyonghao ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/yuyonghao
sudo chmod 440 /etc/sudoers.d/yuyonghao
```

### 8.3 网络接口名称错误

```
错误: Cannot find device "eth0"
```

**解决方案：**
- 检查正确的网络接口名：`ip link show`
- 修改配置文件中的 `interface` 参数

### 8.4 pytest 找不到模块

```
错误: ModuleNotFoundError: No module named 'fault_injector'
```

**解决方案：**
```bash
# 确保在项目根目录执行
cd d:/project/cubestudiofeature/cube-studio-yu

# 或设置 PYTHONPATH
set PYTHONPATH=.
pytest fault_injector/tests/mocktest/ -v
```

---

## 9. 测试脚本

### 9.1 单元测试脚本

```bash
#!/bin/bash
# test_unit.sh

echo "=== Fault Injector 单元测试 ==="

echo "[1/3] SSH Channel 测试..."
pytest fault_injector/tests/mocktest/test_ssh_channel.py -v

echo "[2/3] 场景测试..."
pytest fault_injector/tests/mocktest/test_scenarios.py -v

echo "[3/3] 全部测试..."
pytest fault_injector/tests/mocktest/ -v --cov=fault_injector --cov-report=html

echo "=== 测试完成 ==="
echo "覆盖率报告: htmlcov/index.html"
```

### 9.2 端到端测试脚本

```bash
#!/bin/bash
# test_e2e.sh

set -e

echo "=== Fault Injector 端到端测试 ==="

# 1. 验证配置
echo "[1/4] 验证配置文件..."
python -m fault_injector validate-config fault-injector-test.yaml

# 2. 列出场景
echo "[2/4] 列出可用场景..."
python -m fault_injector list-scenarios

# 3. NetworkJitter Dry-run
echo "[3/4] NetworkJitter Dry-run..."
python -m fault_injector run --dry-run --scenario network_jitter --config fault-injector-test.yaml

# 4. RoCEMTU Dry-run
echo "[4/4] RoCEMTU Dry-run..."
python -m fault_injector run --dry-run --scenario roce_mtu_mismatch --config fault-injector-test.yaml

echo "=== 测试完成 ==="
```

---

## 10. 测试报告模板

完成测试后，填写以下报告：

```markdown
## Fault Injector 测试报告

**测试日期**: YYYY-MM-DD
**测试人员**: 
**测试环境**: 
- 操作系统: 
- Python 版本: 
- 目标节点: 
- SSH 用户: 

### 单元测试结果

| 测试项 | 状态 | 备注 |
|--------|------|------|
| SSH Channel 测试 | □ 通过 / □ 失败 | |
| NetworkJitter 测试 | □ 通过 / □ 失败 | |
| RoCEMTU 测试 | □ 通过 / □ 失败 | |

### 端到端测试结果

| 测试项 | 状态 | 备注 |
|--------|------|------|
| 配置验证 | □ 通过 / □ 失败 | |
| NetworkJitter Dry-run | □ 通过 / □ 失败 | |
| NetworkJitter 实际注入 | □ 通过 / □ 失败 | |
| RoCEMTU Dry-run | □ 通过 / □ 失败 | |
| RoCEMTU 实际注入 | □ 通过 / □ 失败 | |

### 问题记录

1. 
2. 

### 建议

1. 
2.