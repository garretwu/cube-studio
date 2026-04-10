---
id: builtin-vllm-diagnosis
name: vLLM Latency Diagnosis
description: Diagnose vLLM latency incidents by checking latency metrics, namespace pod state, and node GPU utilization.
permissions:
  - read:metrics
  - read:k8s
  - read:gpu
tags:
  - vllm
  - latency
  - gpu
  - inference
  - p95
  - inter-token
---

## When To Use
- vLLM latency alerts such as inter-token latency or request latency regressions.
- Symptoms that may involve GPU contention, overloaded pods, or node-level resource pressure.
- First-pass triage before deciding whether to scale, restart, or isolate a node.

## Required Context
- `namespace`
- `service`
- `node`
- `promql` or an equivalent latency query

## Suggested Checks
- Confirm the latency signal is genuinely above threshold.
- Inspect the service pods running in the target namespace.
- Inspect GPU utilization on the target node.
- If available, continue with GPU process inspection and RDMA link checks.

## Out Of Scope
- Model correctness or semantic output quality issues.
- Automated remediation execution.
