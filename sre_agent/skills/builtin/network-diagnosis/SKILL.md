---
id: builtin-network-diagnosis
name: Network Diagnosis
description: Diagnose network-related latency and transport symptoms through metrics, RDMA stats, and pod state.
permissions:
  - read:network
  - read:k8s
  - read:metrics
tags:
  - network
  - rdma
  - latency
---

## When To Use
- Transport or network alerts where latency, packet loss, or RDMA degradation may be involved.
- Pod traffic problems that could be caused by node or fabric-level instability.

## Required Context
- `node`
- `namespace`
- `promql` for the affected network or latency signal

## Suggested Checks
- Confirm the transport symptom from metrics.
- Read RDMA or link statistics on the target node.
- Inspect impacted pods in the target namespace.
