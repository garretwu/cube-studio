---
name: vLLM Latency Diagnosis
description: Diagnose vLLM latency incidents by checking latency metrics, namespace pod state, and node GPU utilization.
---

## Runtime Metadata
```yaml
id: builtin-vllm-diagnosis
scope: builtin
permissions:
  - read:metrics
  - read:k8s
  - read:gpu
tags:
  - vllm
  - latency
  - gpu
```

## Steps
```yaml
- description: Query vLLM latency indicator.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
- description: List service pods in target namespace.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
- description: Check GPU utilization on target node.
  tool: gpu.get_metrics
  params:
    node: "${node}"
```
