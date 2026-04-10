---
id: builtin-rdma-diagnosis
name: RDMA Anomaly Diagnosis
description: Diagnose RDMA and RoCE anomalies by correlating node RDMA stats, workload placement, and GPU pressure.
permissions:
  - read:network
  - read:k8s
  - read:gpu
tags:
  - rdma
  - roce
  - network
---

## When To Use
- RDMA or RoCE anomalies such as link flaps, degraded throughput, or collective-communication instability.
- Symptoms where we need to separate transport issues from pure GPU saturation.

## Required Context
- `node`
- `namespace`

## Suggested Checks
- Read RDMA statistics from the affected node.
- Confirm pod placement in the impacted namespace.
- Compare the RDMA symptom with GPU load on the same node.
