# Monitor Channel and Baseline Code Change Plan

## Goal
Make baseline collection deterministic, configurable, and trustworthy for CPU/GPU/vLLM/host-network metrics.

## Why change
- Baseline queries are currently hardcoded in orchestrator (`cpu_util`, `memory_util`) and scenario queries are mixed in without quality gating.
- Prometheus query failures are silently converted to `0.0`, which hides monitoring failures as valid data.
- During-scenario observation currently limits duration to `min(observe_duration, observe_interval)`, often producing only one sample.

## Proposed changes

### 1) Add configurable baseline query profile
- Add a monitor query profile in config (default `inference_resilience`) with explicit query map.
- Load from config first; fall back to current defaults if profile is absent.
- Keep scenario-specific queries for `during` phase only.

Suggested config shape:
```yaml
monitor:
  enabled: true
  prometheus_url: "http://10.11.4.3:31260"
  baseline_duration: 180
  post_recovery_duration: 300
  baseline_queries:
    cpu_util_ratio: "avg(rate(node_cpu_seconds_total{mode!='idle'}[2m]))"
    gpu_util_avg: "avg(DCGM_FI_DEV_GPU_UTIL)"
    e2e_p95: "histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket[2m])) by (le))"
```

### 2) Improve Prometheus channel error visibility
- Extend `collect_baseline` return with per-metric metadata:
  - sample_count
  - error_count
  - zero_ratio
  - last_error
- Keep numeric samples for compatibility, but write metadata in session/report.
- Do not silently coerce all failures to valid baseline; include warning events in session.

### 3) Fix observe sampling window
- Change observe call duration from:
  - `min(observe_duration, observe_interval)`
- To:
  - `observe_duration`
- This ensures multiple samples in longer fault windows.

### 4) Baseline quality gate
- Add baseline validation before writing `baseline_completed`:
  - `sample_count >= 3`
  - `error_ratio <= 0.3`
  - not all-zero for metrics marked `require_non_zero`
- Emit `baseline_quality_warning` event when any metric fails gate.
- Optionally add strict mode to fail session pre-injection if critical metrics are invalid.

### 5) Update scenario query defaults
- Replace weak or missing queries in scenario monitor definitions:
  - `network_jitter:network_latency` -> `vllm:e2e_request_latency_seconds_*` or `istio_agent_outgoing_latency`.
- Keep query names stable in report payload for backward compatibility.

## Test plan
- Unit tests:
  - config loader parses `monitor.baseline_queries`
  - baseline quality gate behavior (pass/warn/fail)
  - observe duration fix yields expected sample count
  - Prometheus error metadata population
- Integration tests:
  - dry-run still returns empty baseline without false quality pass
  - non-dry-run with mocked Prometheus failures emits warning events
- Regression checks:
  - existing report generation and session json shape remain compatible

## Rollout sequence
1. Add config/schema support for baseline query map.
2. Implement observe duration fix (low risk, immediate value).
3. Add metadata and quality gate, default warning-only.
4. Tune scenario monitor query defaults.
5. Enable strict gate in test profile after one validation cycle.
