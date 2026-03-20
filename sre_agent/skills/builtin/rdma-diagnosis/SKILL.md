---
name: RDMA Anomaly Diagnosis
description: Diagnose RDMA and RoCE anomalies by correlating node RDMA stats, workload placement, and GPU pressure.
---

## Runtime Metadata
```yaml
id: builtin-rdma-diagnosis
scope: builtin
permissions:
  - read:network
  - read:k8s
  - read:gpu
tags:
  - rdma
  - roce
  - network
```

## Steps
```yaml
- description: Read RDMA stats from affected node.
  tool: network.get_rdma_stats
  params:
    node: "${node}"
- description: Confirm pod placement around issue namespace.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
- description: Check whether GPU overload correlates with RDMA anomaly.
  tool: gpu.get_metrics
  params:
    node: "${node}"
```
