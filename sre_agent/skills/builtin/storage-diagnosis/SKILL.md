---
name: Storage Diagnosis
description: Diagnose storage and IO latency symptoms by correlating metrics, impacted workloads, and transport signals.
---

## Runtime Metadata
```yaml
id: builtin-storage-diagnosis
scope: builtin
permissions:
  - read:metrics
  - read:k8s
  - read:network
tags:
  - storage
  - io
  - latency
```

## Steps
```yaml
- description: Query storage latency/error metric.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
- description: List pods in storage-impacted namespace.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
- description: Inspect RDMA/transport signal on target node.
  tool: network.get_rdma_stats
  params:
    node: "${node}"
```
