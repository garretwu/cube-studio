---
name: Network Diagnosis
description: Diagnose network-related latency and transport symptoms through metrics, RDMA stats, and pod state.
---

## Runtime Metadata
```yaml
id: builtin-network-diagnosis
scope: builtin
permissions:
  - read:network
  - read:k8s
  - read:metrics
tags:
  - network
  - rdma
  - latency
```

## Steps
```yaml
- description: Verify RTT/packet-loss signal via Prometheus.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
- description: Inspect tc qdisc/netem rules on target interface.
  tool: network.get_tc_qdisc
  params:
    node: "${node}"
- description: Check NIC link state and negotiated speed/duplex.
  tool: network.get_nic_link_state
  params:
    node: "${node}"
- description: Check NIC error/drop counters for queue issues.
  tool: network.get_nic_counters
  params:
    node: "${node}"
- description: Check RDMA statistics on target node.
  tool: network.get_rdma_stats
  params:
    node: "${node}"
- description: Inspect impacted namespace pod list.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
```
