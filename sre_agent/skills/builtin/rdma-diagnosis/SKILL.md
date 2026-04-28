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
2. 再做主机侧发现：`network.get_nic_link_state(node="${node_ip}", iface="${iface}")`
3. 再看 NIC 计数器：`network.get_nic_counters(node="${node_ip}", iface="${iface}")`
   - 主机侧工具里的 `node` 默认使用告警上下文里的原始可达节点标识，优先传 IP（例如 `10.11.4.10`），不要优先传 ontology node 名称。
   - 不要把 `ontology.query(node)` 返回的节点实体名直接替换成主机侧工具参数；如果节点名调用失败而 IP 已知，应立即改回 IP 并继续。
4. 再查交换机端口：`network.get_switch_port_counters(switch="${switch_id}", interface="${interface}")`
   - `switch` 必须使用 inventory/拓扑里的交换机 ID 或名称，不要使用管理 IP。
   - 如果还没有 `switch_id` / `interface`，优先调用 `ontology.query(entity_type="node", filters={"ip": "${node}"})` 或按节点名查询对应 node。
   - 缺少 `switch_id` / `interface` 时，必须实际调用 `ontology.query` 补齐映射；不要只在 thought 或结论里描述“下一步需要查询 ontology”。
   - `switch_id` 可以直接使用 node 的 `switch` 属性。
   - `interface` 可以直接来自 node 的 `port`，或从 `switch_ports` 里选择与当前故障 `iface=${iface}` 对应的那一条 `port_id`。
   - 如果 `port` / `port_id` 是 `sw-200g:200GE1/0/1` 这种 entity id，调用交换机工具时必须取冒号后的真实接口名 `200GE1/0/1`，不要把完整 entity id 直接传给 `interface`。
   - `ontology.query(node)` 现在可以作为补充交换机拓扑映射的直接结果使用。
5. 再读取交换机侧配置：`network.get_switch_qos_config(switch="${switch_id}", interface="${interface}")`
   - 重点看 QoS policy、DSCP trust。
   - 一旦 `network.get_switch_port_counters` 已成功，且返回了明确端口状态（例如 `admin=up, oper=up`），下一步必须立刻调用 `network.get_switch_qos_config`。
   - `network.get_switch_port_counters` 在 RDMA 工作流里只是交换机侧取证入口，不是终点；拿到端口状态后不能直接总结，也不能回头重复调用同一个 `network.get_switch_port_counters`，除非 `switch` 或 `interface` 已变化。
   - 在 `network.get_switch_port_counters` 和 `network.get_switch_qos_config` 至少各成功一次之前，不要输出最终诊断；这两步是 RDMA 诊断的关键证据。
6. 如果怀疑 qdisc 或限速残留，再补：`network.get_tc_qdisc(node="${node}", iface="${iface}")`

## 运行上下文

主机侧工具调用依赖：

- `SRE_RDMA_NODE`（优先为可达 IP）
- `SRE_RDMA_IFACE`

交换机侧工具调用依赖：

- `SRE_RDMA_SWITCH_ID`
- `SRE_RDMA_SWITCH_INTERFACE`

## 使用示例

主机侧发现：

先调用 `ssh.run_command(node="10.11.4.10", command="rdma link show")`。如果输出包含 `link mlx5_200/1 ... netdev roce200`，后续调用 `network.get_nic_link_state(node="10.11.4.10", iface="roce200")` 与 `network.get_nic_counters(node="10.11.4.10", iface="roce200")`。

交换机侧发现：

优先调用 `network.get_switch_port_counters(switch="sw-200g", interface="200GE1/0/1")`。
只要 `network.get_switch_port_counters` 成功返回端口状态，下一步就应紧接着调用 `network.get_switch_qos_config(switch="sw-200g", interface="200GE1/0/1")`；不要重复调用 `network.get_switch_port_counters`。
如果需要先补拓扑映射，先调用 `ontology.query` 查 node，并直接使用返回结果里的 `switch` / `port`；如果存在 `switch_ports`，选择与故障 `iface` 对应的那一条。
如果 `port` / `port_id` 带有 `switch_id:` 前缀，调用 `network.get_switch_port_counters` 和 `network.get_switch_qos_config` 时只传真实接口名，例如 `200GE1/0/1`。

## 安全说明

- 诊断阶段只使用只读工具。
- 不直接执行交换机恢复或变更。
