# Fault Injector 测试执行手册（Runbook）

> 推荐每次发布前至少执行一次完整 Runbook。

## Step 0：准备

1. 记录当前 commit：
   - `git rev-parse --short HEAD`
2. 备份配置文件
3. 确认目标机器为测试环境

## Step 1：执行单元测试

```bash
python -m unittest fault_injector.tests.test_roce_mtu_mismatch
```

- 若失败：停止后续测试，先修复代码。

## Step 2：执行 simulate 注入

```bash
python -m fault_injector --config fault_injector/conf/fault_injector.conf.json --session-id runbook-sim inject-roce-mtu-mismatch
```

记录输出 JSON。

## Step 3：执行 simulate 回滚

```bash
python -m fault_injector --config fault_injector/conf/fault_injector.conf.json --session-id runbook-sim rollback
```

确认全部 success。

## Step 4：切换远端 ssh 配置

1. 复制 `Test/remote_config_template.conf.json`
2. 填写 host/user/port/interface/original_mtu/fault_mtu
3. 将 `mode` 改为 `ssh`

## Step 5：远端注入

```bash
python -m fault_injector --config <your-remote-conf>.json --session-id runbook-real inject-roce-mtu-mismatch
```

逐机核验：

```bash
ssh <user>@<host> "ip link show <iface>"
```

## Step 6：远端回滚

```bash
python -m fault_injector --config <your-remote-conf>.json --session-id runbook-real rollback
```

再次逐机核验 MTU 是否恢复。

## Step 7：异常演练（建议）

重复 Step 5 后，间隔 1~2 分钟再执行 Step 6，观察是否稳定恢复。

## Step 8：输出报告

建议记录格式：

- commit
- 配置文件版本
- 每一步执行时间
- 命令与输出
- 失败项与处理动作
- 是否满足上线条件

