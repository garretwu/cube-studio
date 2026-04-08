---
id: builtin-gpu-health
name: GPU Health Check
description: Run a GPU health check by inspecting node GPU metrics, scheduler visibility, and utilization signal.
permissions:
  - read:gpu
  - read:k8s
  - read:metrics
tags:
  - gpu
  - health
  - node
---

## When To Use
- Suspected node-level GPU health problems.
- Periodic GPU checks before or after incident handling.
- Situations where we need a quick signal on utilization and scheduler visibility.

## Required Context
- `node`
- `namespace`
- `promql` or another GPU-related metric query

## Suggested Checks
- Sample GPU metrics on the node.
- Confirm the namespace workload is visible to the scheduler.
- Check the aggregate utilization or health signal from metrics.
