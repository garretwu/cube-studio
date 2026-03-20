---
name: GPU Health Check
description: Run a GPU health check by inspecting node GPU metrics, scheduler visibility, and utilization signal.
---

## Runtime Metadata
```yaml
id: builtin-gpu-health
scope: builtin
permissions:
  - read:gpu
  - read:k8s
  - read:metrics
tags:
  - gpu
  - health
  - node
```

## Steps
```yaml
- description: Collect GPU metrics on target node.
  tool: gpu.get_metrics
  params:
    node: "${node}"
- description: Verify pods scheduled in namespace.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
- description: Query GPU utilization aggregate metric.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
```
