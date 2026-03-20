---
name: Platform Health Check
description: Run a lightweight platform-wide health sweep using pod inventory, baseline metrics, and node GPU signals.
---

## Runtime Metadata
```yaml
id: builtin-platform-health
scope: builtin
permissions:
  - read:k8s
  - read:metrics
  - read:gpu
tags:
  - platform
  - health
  - baseline
```

## Steps
```yaml
- description: List baseline namespace pods.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
- description: Query platform up/health metric.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
- description: Sample GPU node health signal.
  tool: gpu.get_metrics
  params:
    node: "${node}"
```
