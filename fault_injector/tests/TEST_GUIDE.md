# Fault Injector 测试指南

本文档描述如何测试 AIDC Auto-SRE Fault Injector 的 RC-2 网络延迟场景。

## 1. 测试环境要求

### 1.1 软件要求

- Python 3.10+ （已测试 Python 3.14）
- 必需的 Python 包：
  ```bash
  pip install pydantic pyyaml click rich asyncssh
  ```

### 1.2 测试目标节点

- 一台 Linux 虚拟机或物理机（用于 SSH 连接）
- 需要 root 权限或 sudo 权限（执行 tc 命令）
- 需要安装 `iproute2` 包（提供 tc 命令）

```bash
# 在目标节点上安装 iproute2
apt-get install iproute2 -y   # Debian/Ubuntu
yum install iproute -y        # CentOS/RHEL
```

## 2. 测试前准备

### 2.1 配置 SSH 连接

1. 在目标节点上创建 SSH 密钥认证（推荐）：

```bash
# 在运行 fault_injector 的机器上生成密钥（如果没有）
ssh-keygen -t rsa -b 4096

# 将公钥复制到目标节点
ssh-copy-id yuyonghao@<目标节点IP>
```

2. 或者在配置文件中使用密码认证。

### 2.2 配置 sudo 免密码（重要）

由于 `tc` 命令需要 root 权限，需要在目标节点上配置 sudo 免密码：

```bash
# 在目标节点上执行（以 root 或有 sudo 权限的用户）
sudo visudo -f /etc/sudoers.d/fault-injector
```

添加以下内容：

```
# 允许 yuyonghao 免密码执行所有命令
yuyonghao ALL=(ALL) NOPASSWD: ALL

# 或者只允许执行 tc 命令（更安全）
yuyonghao ALL=(ALL) NOPASSWD: /usr/sbin/tc
```

保存后验证：

```bash
# 切换到 yuyonghao 用户测试
sudo -u yuyonghao sudo tc qdisc show
# 应该不需要密码就能执行
```

### 2.3 创建测试配置文件

复制 `fault-injector-config.yaml` 并修改节点信息：

```yaml
# fault-injector-test.yaml
global:
  session_dir: "./fault-reports/sessions/"
  log_level: "DEBUG"  # 测试时使用 DEBUG 级别
  safety:
    require_confirmation: false  # 测试时跳过确认
    auto_recover_timeout: 300
    dry_run: false
    max_concurrent_faults: 3
    excluded_nodes: []

inventory:
  nodes:
    - name: "test-node-1"
      ssh:
        host: "<目标节点IP>"      # 修改为实际 IP
        port: 22
        user: "yuyonghao"         # 使用普通用户
        key_file: "~/.ssh/id_rsa"  # 或使用 password
        # password: "your-password"
        use_sudo: true             # 启用 sudo（默认已启用）
      interface: "eth0"            # 修改为实际网络接口
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
      duration: 30    # 测试时使用较短的持续时间
      interface: "eth0"
```

**配置说明：**

| 参数 | 说明 |
|------|------|
| `user` | 使用普通用户（如 `yuyonghao`），不是 `root` |
| `use_sudo` | 是否在命令前添加 `sudo`，默认为 `true` |
| `interface` | 网络接口名，使用 `ip link show` 查看 |

## 3. 测试步骤

### 3.1 测试 1：验证配置文件

```bash
cd d:/project/cubestudiofeature/cube-studio-yu

# 验证配置文件语法
python -m fault_injector validate-config fault-injector-test.yaml
```

**预期输出：**
```
✓ 配置校验通过
  Session 目录: ./fault-reports/sessions/
  日志级别: DEBUG
  节点组: ['nodes']
  场景: ['network_jitter']
```

### 3.2 测试 2：列出可用场景

```bash
python -m fault_injector list-scenarios
```

**预期输出：**
```
              可用场景              
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ 名称            ┃ 描述                                    ┃ 层级   ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ network_jitter  │ 网络延迟抖动 — 使用 tc netem 注入不确定延迟 │ os     │
└─────────────────┴────────────────────────────────────────┴────────┘
```

### 3.3 测试 3：Dry-Run 模式

Dry-run 模式仅打印操作，不实际执行 SSH 命令。用于验证配置和流程。

```bash
python -m fault_injector run --dry-run --config fault-injector-test.yaml
```

**预期输出：**
```
加载配置: fault-injector-test.yaml
-------------------- Fault Injector — 场景: network_jitter --------------------
  场景: network_jitter
  描述: 网络延迟抖动 — 使用 tc netem 注入不确定延迟
  层级: os
  Dry-run: 是
  目标节点: test-node-1 (<IP>)

  Session ID: <session_id>
  Session 目录: fault-reports\sessions\<session_id>

>>> 注入故障...
[DRY-RUN] SSH test-node-1: sudo tc qdisc add dev eth0 root netem delay 50ms 100ms distribution pareto
✓ 故障注入成功

>>> 观测期 (30s)...
  [DRY-RUN] 跳过观测期

>>> 恢复故障...
[DRY-RUN] SSH test-node-1: sudo tc qdisc del dev eth0 root
✓ 故障恢复成功

>>> 验证恢复...
[DRY-RUN] SSH test-node-1: sudo tc qdisc show dev eth0
✓ 验证通过

>>> 执行完成
  回滚日志: fault-reports\sessions\<session_id>\rollback.jsonl
```

**注意**：在 dry-run 模式下，可以看到命令前自动添加了 `sudo`。

### 3.4 测试 4：实际故障注入（需要真实 SSH 连接）

**⚠️ 警告：此测试会在目标节点上实际执行 tc 命令，可能会影响网络连接！**

```bash
python -m fault_injector run --config fault-injector-test.yaml
```

**在另一个终端验证网络延迟（在目标节点上执行）：**

```bash
# 测试前（基线）
ping -c 5 8.8.8.8
# 记录平均延迟

# 故障注入后
ping -c 5 8.8.8.8
# 应该看到延迟增加约 50ms ± 100ms

# 查看 tc 配置（需要 sudo）
sudo tc qdisc show dev eth0
# 应该看到 netem 规则

# 恢复后
sudo tc qdisc show dev eth0
# netem 规则应该消失
```

### 3.5 测试 5：WAL 回滚日志验证

每次执行都会生成回滚日志：

```bash
# 查看最新的回滚日志
type fault-reports\sessions\<session_id>\rollback.jsonl
```

**预期内容：**
```json
{
  "fault_id": "network_jitter_test-node-1_20260224_XXXXXX",
  "injected_at": "2026-02-24T18:10:39.697322",
  "channel": "ssh",
  "target": "test-node-1",
  "inject_action": "tc_add_delay",
  "inject_params": {
    "node": "test-node-1",
    "interface": "eth0",
    "delay_ms": 50,
    "jitter_ms": 100,
    "distribution": "pareto",
    "loss_pct": 0.0
  },
  "recover_action": "tc_del_qdisc",
  "recover_params": {
    "node": "test-node-1",
    "interface": "eth0"
  },
  "status": "recovered"
}
```

### 3.6 测试 6：手动恢复测试

如果故障注入过程中断（如 Ctrl+C），可以使用 recover 命令恢复：

```bash
# 1. 执行故障注入（不等待完成，按 Ctrl+C 中断）
python -m fault_injector run --config fault-injector-test.yaml

# 2. 记录 session_id

# 3. 手动恢复
python -m fault_injector recover --session <session_id>
```

## 4. 验证检查清单

### 4.1 功能验证

| 检查项 | 命令 | 预期结果 |
|--------|------|----------|
| CLI 入口 | `python -m fault_injector --help` | 显示帮助信息 |
| 配置验证 | `python -m fault_injector validate-config <config>` | 配置校验通过 |
| 场景列表 | `python -m fault_injector list-scenarios` | 显示 network_jitter |
| Dry-run | `python -m fault_injector run --dry-run --config <config>` | 仅打印操作不执行 |
| 实际注入 | `python -m fault_injector run --config <config>` | SSH 执行 sudo tc 命令 |
| 回滚日志 | `type fault-reports\sessions\*\rollback.jsonl` | JSON 格式日志 |

### 4.2 网络延迟验证

在目标节点上执行：

```bash
# 查看当前 tc 配置
sudo tc qdisc show dev eth0

# 预期输出（故障注入时）：
# qdisc netem 1: root refcnt 2 limit 1000 delay 50.0ms  100ms

# 预期输出（恢复后）：
# qdisc mq 0: root
# 或没有 netem 规则
```

### 4.3 sudo 配置验证

```bash
# 在目标节点上验证 sudo 免密码配置
sudo -l
# 应该显示 NOPASSWD

# 测试 tc 命令
sudo tc qdisc show
# 应该不需要密码
```

## 5. 常见问题排查

### 5.1 SSH 连接失败

```
错误: SSH 连接超时
```

**解决方案：**
1. 检查目标节点 IP 是否正确
2. 检查 SSH 服务是否运行：`systemctl status sshd`
3. 检查防火墙是否允许 SSH：`ufw allow 22`
4. 检查 SSH 密钥/密码是否正确

### 5.2 sudo 密码问题

```
错误: sudo: a terminal is required to read the password
```

**解决方案：**
配置 sudo 免密码（见 2.2 节）：
```bash
echo "yuyonghao ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/yuyonghao
sudo chmod 440 /etc/sudoers.d/yuyonghao
```

### 5.3 tc 命令权限不足

```
错误: RTNETLINK answers: Operation not permitted
```

**解决方案：**
- 确保 `use_sudo: true` 在配置中设置
- 确保 sudo 免密码配置正确

### 5.4 网络接口名称错误

```
错误: Cannot find device "eth0"
```

**解决方案：**
- 检查正确的网络接口名：`ip link show`
- 修改配置文件中的 `interface` 参数

### 5.5 asyncssh 模块未找到

```
错误: ModuleNotFoundError: No module named 'asyncssh'
```

**解决方案：**
```bash
pip install asyncssh
```

## 6. 测试脚本

创建一个自动化测试脚本：

```bash
#!/bin/bash
# test_fault_injector.sh

set -e

echo "=== Fault Injector 自动化测试 ==="

# 1. 验证配置
echo "[1/5] 验证配置文件..."
python -m fault_injector validate-config fault-injector-test.yaml

# 2. 列出场景
echo "[2/5] 列出可用场景..."
python -m fault_injector list-scenarios

# 3. Dry-run 测试
echo "[3/5] Dry-run 测试..."
python -m fault_injector run --dry-run --config fault-injector-test.yaml

# 4. 检查回滚日志
echo "[4/5] 检查回滚日志..."
LATEST_SESSION=$(ls -td fault-reports/sessions/*/ | head -1)
if [ -f "${LATEST_SESSION}rollback.jsonl" ]; then
    echo "✓ 回滚日志存在: ${LATEST_SESSION}rollback.jsonl"
else
    echo "✗ 回滚日志不存在"
    exit 1
fi

# 5. 实际注入测试（可选）
echo "[5/5] 实际注入测试（按 Enter 继续，Ctrl+C 跳过）..."
read
python -m fault_injector run --config fault-injector-test.yaml

echo "=== 测试完成 ==="
```

## 7. 测试报告模板

完成测试后，填写以下报告：

```
## Fault Injector 测试报告

**测试日期**: YYYY-MM-DD
**测试人员**: 
**测试环境**: 
- 操作系统: 
- Python 版本: 
- 目标节点: 
- SSH 用户: yuyonghao (sudo)

### 测试结果

| 测试项 | 状态 | 备注 |
|--------|------|------|
| 配置验证 | □ 通过 / □ 失败 | |
| Dry-run 模式 | □ 通过 / □ 失败 | |
| 实际 SSH 连接 | □ 通过 / □ 失败 | |
| sudo tc 命令执行 | □ 通过 / □ 失败 | |
| 故障恢复 | □ 通过 / □ 失败 | |
| WAL 日志 | □ 通过 / □ 失败 | |

### 问题记录

1. 
2. 

### 建议

1. 
2.