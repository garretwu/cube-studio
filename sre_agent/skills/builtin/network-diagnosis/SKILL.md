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

## SOP: Network Jitter / tc Netem Injection
- For latency alerts (for example `NetworkLatencyHigh100ms`), first confirm the symptom with `prometheus.query_instant`.
- Then inspect qdisc/netem state with `network.get_tc_qdisc` on the affected node/interface.
- If qdisc output shows injected delay/loss/corruption rules, treat tc/netem residue as the primary root-cause candidate.

## Remediation Guidance (Write Tools)
- Preferred action for tc/netem residue: use `network.clear_tc_qdisc` with structured params in `params`:
  - required: `node`, `iface`
  - optional: `parent`, `handle`
- Do not rely on free-text description as the only source for required params.
- Only use `kill_process` when there is explicit evidence of a still-running fault-injector process.
- If neither qdisc residue nor injector process evidence exists, avoid forcing a write plan and return no executable remediation plan.
