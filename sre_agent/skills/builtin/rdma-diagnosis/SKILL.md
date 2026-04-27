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
  - network
  - packet
---

# RDMA 异常发现、诊断与处理

## 推荐工作流

1. 先解析 RDMA 设备到 Linux 网卡的映射：`ssh.run_command(node="${node}", command="rdma link show")`
   - 必须从输出里的 `netdev <iface>` 得到真实 `iface`。
   - 如果看到 `mlx5_200/1 ... netdev roce200`，后续必须使用 `iface="roce200"`。
2. 再做主机侧发现：`network.get_nic_link_state(node="${node}", iface="${iface}")`
3. 再看 NIC 计数器：`network.get_nic_counters(node="${node}", iface="${iface}")`
4. 再查交换机端口：`network.get_switch_port_counters(switch="${switch_id}", interface="${interface}")`
   - `switch` 必须使用 inventory/拓扑里的交换机 ID 或名称，不要使用管理 IP。
   - 如果还没有 `switch_id` / `interface`，通过 ontology 补齐，但目标必须是“与故障节点 `node=${node}` 和故障网卡 `iface=${iface}` 直连的交换机和端口”。
   - 优先查 `switch` 实体；只有完全没有交换机候选值时，才允许先从 `node` / `iface` 的拓扑关系反查。
   - `ontology.query` 的 `filters` 必须是 dict/object。推荐模板：
     - `ontology.query(entity_type="switch", filters={"name": "${switch_id}"})`
     - `ontology.query(entity_type="node", filters={"ip": "${node}"})`
   - 不要传字符串化 JSON，不要传列表，不要把 `node` 查询当成最终结果。
   - 如果 ontology 仍然拿不到 `switch_id` / `interface`，应明确总结“缺少交换机拓扑映射”，不要回头重复已成功的主机侧工具。
5. 继续读取交换机侧配置：`network.get_switch_qos_config(switch="${switch_id}", interface="${interface}")`
   - 重点看 QoS policy、`car cir`、`qos gts`、WRED、PFC、DSCP trust。
6. 如果怀疑 qdisc 或限速残留，再补：`network.get_tc_qdisc(node="${node}", iface="${iface}")`

## 运行上下文

主机侧工具调用依赖：

- `SRE_RDMA_NODE`
- `SRE_RDMA_IFACE`

交换机侧工具调用依赖：

- `SRE_RDMA_SWITCH_ID`
- `SRE_RDMA_SWITCH_INTERFACE`

## 使用示例

主机侧发现：

先调用 `ssh.run_command(node="worker-01", command="rdma link show")`。如果输出包含 `link mlx5_200/1 ... netdev roce200`，后续调用 `network.get_nic_link_state(node="worker-01", iface="roce200")` 与 `network.get_nic_counters(node="worker-01", iface="roce200")`。

交换机侧发现：

优先调用 `network.get_switch_port_counters(switch="sw-200g", interface="200GE1/0/1")`。
如果怀疑 QoS 或限速，再调用 `network.get_switch_qos_config(switch="sw-200g", interface="200GE1/0/1")`。
如果需要先补拓扑映射，优先查询交换机相关实体；如果必须先从主机侧拓扑反查，也只把它当成找到“直连 switch + 端口”的中间步骤。

## 安全说明

- 诊断阶段只使用只读工具。
- 不直接执行交换机恢复或变更。
