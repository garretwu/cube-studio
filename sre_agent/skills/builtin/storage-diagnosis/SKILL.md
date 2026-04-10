---
id: builtin-storage-diagnosis
name: Storage Diagnosis
description: Diagnose storage and IO latency symptoms by correlating metrics, impacted workloads, and transport signals.
permissions:
  - read:metrics
  - read:k8s
  - read:network
tags:
  - storage
  - io
  - latency
---

## When To Use
- Storage latency or IO degradation incidents.
- Cases where workload symptoms may be caused by storage or transport bottlenecks.

## Required Context
- `namespace`
- `node`
- `promql` for storage latency or error signal

## Suggested Checks
- Query the storage latency or error metric.
- List workloads in the impacted namespace.
- Inspect transport signals on the target node.
