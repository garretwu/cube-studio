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
- description: Query packet loss or latency metric.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
- description: Check RDMA statistics on target node.
  tool: network.get_rdma_stats
  params:
    node: "${node}"
- description: Inspect impacted namespace pod list.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
```
