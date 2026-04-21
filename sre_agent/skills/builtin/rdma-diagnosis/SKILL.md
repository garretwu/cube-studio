---
id: builtin-rdma-diagnosis
name: RDMA 异常诊断
description: >
  用于 RDMA 或 RoCE 异常的发现、诊断与处理。通过采集主机侧 RDMA/NIC 状态、
  交换机接口 QoS 配置与队列策略，并将当前状态与健康基线对比，定位吞吐下降、
  链路抖动、MTU 不一致、PFC/ECN/QoS 误配、接口限速、DSCP 信任关闭、
  WRED drop-level 或 discard-probability 错配等问题。
permissions:
  - read:network
  - read:k8s
  - read:gpu
tags:
  - rdma
  - roce
  - infiniband
  - nic
  - switch
  - qos
  - nccl
  - remediation
---

# RDMA 异常发现、诊断与处理

这个 skill 覆盖四个阶段：

1. 主机侧发现：采集 RDMA、NIC、MTU、qdisc、内核日志。
2. 主机侧处理：对明确的主机侧问题给出恢复方案，必要时执行低风险动作。
3. 交换机侧发现：读取接口 QoS、限速、信任状态、WRED 配置，并识别与健康基线的偏差。
4. 交换机侧恢复：按健康基线或显式期望回退接口上的 QoS、信任与 WRED 配置。

## 脚本清单

这个 skill 当前暴露 4 个脚本：

- `rdma_health_check.sh`
  - 主机侧首选发现脚本。
  - 采集 RDMA、NIC、MTU、qdisc、`dmesg` 证据。
  - 输出 JSON 格式的 `findings`、`recommended_actions`。

- `rdma_remediate.sh`
  - 主机侧处理脚本。
  - 默认只输出处理计划；显式加 `--execute` 才真正执行低风险动作。

- `rdma_switch_health_check.sh`
  - 交换机侧发现脚本。
  - 读取接口当前配置，识别 QoS policy 绑定、`qos trust dscp` 状态、WRED `drop-level` 行、以及全局 `car cir` 等线索。
  - 支持加载健康基线并做差异比对。

- `rdma_switch_restore.sh`
  - 交换机侧恢复脚本。
  - 根据健康基线或显式期望，恢复接口上的 policy 绑定、信任状态与 WRED 行。
  - 默认只输出计划，只有在显式 `--execute` 时才真正下发配置。

调用 `skills.run_skill` 时，不要发明新的脚本名。

## 适用场景

- RDMA / RoCE 吞吐下降。
- NCCL all-reduce 不稳定，需要区分是 GPU 饱和还是网络/交换机问题。
- 链路抖动、NIC reset、间歇性丢包。
- MTU 不匹配、测试遗留 qdisc。
- PFC 死锁、ECN 阈值误配、QoS 降级、接口限速、DSCP 信任关闭、WRED 参数错配等怀疑场景。

## 推荐工作流

1. 先跑主机侧发现：`bash scripts/rdma_health_check.sh`
2. 如果主机侧证据不足、或怀疑交换机误配，再跑交换机侧发现：`bash scripts/rdma_switch_health_check.sh`
3. 如果环境允许，先在健康窗口对关键接口做一次基线采集，并保存 discovery report，后续恢复时优先使用这份基线。
4. 处理前先看计划输出；只有在明确授权、确认影响面后再加 `--execute`。

## 主机侧覆盖范围

`rdma_health_check.sh` 采集：

- `rdma link show`
- `/sys/class/infiniband/<hca>/ports/1/counters`
- `ip -s link show`
- `ethtool` / `ethtool -S`
- `tc qdisc show`
- 接口 MTU 与 operstate
- 与 mlx5 / RDMA / RoCE / link reset 相关的 `dmesg`

典型发现包括：

- RDMA 工具缺失或 HCA 名称不匹配
- NIC link down
- RoCE 侧 MTU 偏小
- NIC error counter 增长
- qdisc 残留
- 内核日志出现 link flap、tx timeout、mlx5 error

## 交换机侧覆盖范围

`rdma_switch_health_check.sh` 重点关注：

- 接口是否绑定了 QoS policy
- `qos trust dscp` 是否被关闭
- `qos wred queue ... drop-level ... low-limit ... high-limit ... discard-probability ...` 是否异常
- 当前接口状态与健康基线的差异
- 全局配置里是否出现 `car cir` 等限速线索

它不会把故障限定成某几个固定名字，而是从“接口状态是否偏离健康基线”来判断：

- policy 绑定漂移
- 接口信任状态漂移
- WRED 行漂移
- 存在可能引发限速的 CAR 线索

## 交换机侧恢复原则

`rdma_switch_restore.sh` 是按状态恢复，不按故障名恢复。

它支持两种恢复方式：

1. 基线恢复
   - 使用 `rdma_switch_health_check.sh` 在健康状态下保存的 report
   - 恢复当前接口到这份 report 里的 policy / trust / WRED 状态

2. 显式期望恢复
   - 明确指定期望的 trust 状态
   - 明确指定期望的 policy 绑定
   - 明确指定期望的 WRED 行

推荐优先使用基线恢复，因为这样最通用、也最不容易误删生产配置。

## 运行上下文

主机侧脚本支持：

- `SRE_RDMA_NODE`
- `SRE_RDMA_HOST`
- `SRE_RDMA_SSH_USER`
- `SRE_RDMA_SSH_PORT`
- `SRE_RDMA_IFACE`
- `SRE_RDMA_HCA`
- `SRE_RDMA_EXPECTED_MTU`

交换机侧脚本支持：

- `SRE_RDMA_SWITCH_HOST`
- `SRE_RDMA_SWITCH_USER`
- `SRE_RDMA_SWITCH_PASSWORD`
- `SRE_RDMA_SWITCH_PORT`
- `SRE_RDMA_SWITCH_INTERFACE`

## 使用示例

主机侧发现：

```bash
bash scripts/rdma_health_check.sh --node worker-01 --host 10.0.0.11 --iface roce0 --hca mlx5_0
```

主机侧处理计划：

```bash
bash scripts/rdma_remediate.sh --symptom mtu-mismatch --iface roce0 --mtu 4200
```

交换机侧发现：

```bash
bash scripts/rdma_switch_health_check.sh \
  --switch-host 10.11.8.52 \
  --switch-user admin \
  --interface TwoHundredGigE1/0/1
```

交换机侧基线恢复计划：

```bash
bash scripts/rdma_switch_restore.sh \
  --switch-host 10.11.8.52 \
  --switch-user admin \
  --interface TwoHundredGigE1/0/1 \
  --baseline-report /tmp/healthy_switch_report.json
```

交换机侧显式恢复执行：

```bash
bash scripts/rdma_switch_restore.sh \
  --switch-host 10.11.8.52 \
  --switch-user admin \
  --interface TwoHundredGigE1/0/1 \
  --expected-trust enabled \
  --remove-extra-policies \
  --execute
```

## 告警与发现映射

- `link_down`、`link_flap_log`
  - 优先检查链路状态与交换机端口计数。
- `mtu_too_small`
  - 恢复前先确认对端与交换机 MTU。
- `qdisc_present`
  - 只在确认是测试残留时清除。
- `switch_policy_drift`
  - 说明接口 policy 绑定偏离了基线。
- `switch_trust_drift`
  - 说明 DSCP 信任状态偏离了基线或期望。
- `switch_wred_drift`
  - 说明队列 drop-level 或 discard-probability 偏离了基线。
- `switch_car_hint`
  - 说明全局 QoS 中存在可能的限速线索，需要进一步确认是否命中当前接口策略。

## 安全说明

- 发现脚本默认可以执行。
- 恢复脚本默认只输出计划。
- `bounce-link` 会中断当前 RDMA 会话。
- 交换机恢复会直接影响线上队列与流量分类，必须在明确授权后执行。
- 不要在不知道健康基线的情况下盲目删除所有 policy 或 WRED 行。
