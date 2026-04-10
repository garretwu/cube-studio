---
id: builtin-platform-health
name: Platform Health Check
description: Run a lightweight platform-wide health sweep using pod inventory, baseline metrics, and node GPU signals.
permissions:
  - read:k8s
  - read:metrics
  - read:gpu
tags:
  - platform
  - health
  - baseline
---

## When To Use
- A broad platform triage where we need a quick baseline before specializing.
- Situations where service impact is known but the failing layer is still unclear.

## Required Context
- `namespace`
- `node`
- `promql` for a baseline platform health metric

## Suggested Checks
- List pods in the baseline namespace.
- Query an overall health or availability metric.
- Sample the node GPU health signal if accelerators are involved.
