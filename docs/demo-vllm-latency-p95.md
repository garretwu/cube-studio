# Demo 1: vLLM Latency P95 不达标

## 场景设定

```text
前提：使用 fault-injector 在推理节点注入 GPU 争用
      GPU-0 上运行 gpu-burn 进程，推理服务使用 GPU-1

告警：Prometheus alert "VLLMLatencyP95High"
      vllm_request_duration_seconds{quantile="0.95"} > 0.5
      持续 5 分钟
```

## Agent 完整诊断 Trace

```text
[14:00:00] ┌─ Alert 接收 ─────────────────────────────────────────────┐
           │ alert: VLLMLatencyP95High                                │
           │ severity: critical                                       │
           │ labels: {service="vllm-deepseek", namespace="service"}   │
           │ value: 0.68s (阈值: 0.5s)                               │
           └──────────────────────────────────────────────────────────┘

[14:00:01] ── 构建初始上下文 ──
           · 从 Ontology 获取 vllm-deepseek 服务拓扑:
             InferenceService(vllm-deepseek) → Pod(vllm-xxx-abc123)
               → Node(gpu-1-1) → GPU-0, GPU-1
               → NIC(enp65s0f0) → SwitchPort(sw-200g:HGE1/0/1)
           · 从记忆检索: 无相似历史事件
           · 从知识库检索: vLLM 性能调优文档 (3 chunks)

[14:00:02] ── Step 1 [Think] ──
           "收到 vLLM P95 延迟告警 (680ms > 500ms)。需要确认问题范围并
            列举可能根因。常见原因：
            假设 A: GPU 资源争用（优先级高，最常见）
            假设 B: 网络问题（RDMA 异常、丢包）
            假设 C: KV Cache 内存不足
            假设 D: GPU 热节流
            先查看延迟分布和 GPU 指标。"

[14:00:02] ── Step 2 [Act] ── tool_call
           → get_inference_latency(service="vllm-deepseek")

[14:00:03] ── Step 2 [Observe] ──
           {p50_ms: 145, p95_ms: 680, p99_ms: 1250, qps: 12.3, error_rate: 0.02}
           "P50 正常 (145ms)，P95/P99 明显偏高 → 尾延迟问题，非全面性能下降"

[14:00:03] ── Step 3 [Act] ── tool_call
           → get_gpu_metrics(node="gpu-1-1")

[14:00:04] ── Step 3 [Observe] ──
           {per_gpu: [
             {index: 0, util_pct: 97.2, mem_used_mb: 75800, mem_total_mb: 81920,
              temp_c: 78, power_w: 285},
             {index: 1, util_pct: 68.5, mem_used_mb: 45200, mem_total_mb: 81920,
              temp_c: 72, power_w: 220}
           ]}

[14:00:04] ── Step 4 [Think] ──
           "GPU-0 利用率 97.2%（异常高），GPU-1 利用率 68.5%（正常推理范围）。
            推理服务应该在 GPU-1 上，但 GPU-0 被什么进程占用了？
            GPU-0 高占用可能通过 PCIe 总线争用影响 GPU-1。
            → 假设 A 升级为主要嫌疑
            → 需要查看 GPU-0 上的进程"

[14:00:05] ── Step 5 [Act] ── tool_call
           → get_gpu_processes(node="gpu-1-1")
           (P0 修复：使用专用 get_gpu_processes 工具，替代原错误的 read_system_log)

[14:00:05] ── Step 5 [Observe] ──
           {processes: [
             {pid: 12847, name: "gpu-burn", gpu_uuid: "GPU-0", memory_mb: 74500},
             {pid: 15923, name: "python3", gpu_uuid: "GPU-1", memory_mb: 44800}
           ]}
           "确认: gpu-burn 进程 (PID 12847) 占用 GPU-0 全部显存和算力"

[14:00:06] ── Step 6 [Think] ──
           "根因基本确认：gpu-burn 进程占用 GPU-0，导致 PCIe 总线争用。
            但需要排除其他可能的贡献因素。检查温度（排除假设 D）。"

[14:00:06] ── Step 7 [Act] ── tool_call
           → get_thermal_status(node="gpu-1-1")

[14:00:07] ── Step 7 [Observe] ──
           {temperatures: [{name: "GPU-0", reading: 78, threshold: 90},
                           {name: "GPU-1", reading: 72, threshold: 90},
                           {name: "CPU", reading: 62, threshold: 95}],
            fans: [{name: "Fan-1", speed_rpm: 8500, status: "OK"},
                   {name: "Fan-2", speed_rpm: 8200, status: "OK"}]}
           "温度正常，未触发热节流 → 假设 D 排除"

[14:00:07] ── Step 8 [Act] ── tool_call
           → check_nic_errors(node="gpu-1-1", interface="enp65s0f0")

[14:00:08] ── Step 8 [Observe] ──
           {rx_errors: 0, tx_errors: 0, rx_dropped: 2, tx_dropped: 0, crc_errors: 0}
           "网络无异常 → 假设 B 排除"

[14:00:08] ── Step 9 [Conclude] ──
           根因: GPU 资源争用
           层级: hardware
           置信度: 0.94
           详细: gpu-burn 进程 (PID 12847) 占用 GPU-0 (97.2% util, 74.5GB/80GB VRAM),
                 导致 PCIe 总线带宽争用，影响相邻 GPU-1 上的 vLLM 推理服务。
                 表现为 P95/P99 尾延迟升高，P50 基本不受影响。
           影响范围: vllm-deepseek 推理服务所有请求
           假设验证:
             A: GPU 争用 ✓ confirmed (gpu-burn on GPU-0)
             B: 网络问题 ✗ eliminated (NIC errors = 0)
             C: KV Cache ✗ not tested (GPU-1 mem 45.2/80GB 充足)
             D: 热节流 ✗ eliminated (GPU temp 78°C < 90°C threshold)

[14:00:08] ── Step 10 [Remediate] ──
           修复计划:
             Step 1: kill_process(node="gpu-1-1", pid_or_name="gpu-burn")
                     验证: GPU-0 util < 10% (5s 后)
             Step 2: 等待 60s 让 PCIe 带宽恢复正常
             Step 3: 验证 get_inference_latency → P95 < 500ms
           灰度: 不适用（单节点修复）
           置信度: 0.94

[14:00:09] ── 审批门控 ──
           "kill_process" 需要 human_confirm
           → 推送到 GUI 等待工程师确认

[14:00:15] ── 工程师确认 ── ✓ Approved by admin

[14:00:15] ── 修复执行 ──
           Step 1: kill_process(node="gpu-1-1", pid_or_name="gpu-burn")
           WAL: recorded (无需回滚)
           执行: SSH → kill -9 12847 ✓
           验证 (5s后): GPU-0 util = 3.2% ✓

           Step 2: 等待 60s...

           Step 3: 验证 P95
           get_inference_latency(service="vllm-deepseek")
           → {p50_ms: 125, p95_ms: 310, p99_ms: 480}
           → P95 = 310ms < 500ms ✓

[14:01:20] ── 修复完成 ──
           结果: 成功
           P95: 680ms → 310ms (降低 54%)
           诊断耗时: 8s
           修复耗时: 65s (含等待)
           总耗时: 80s

[14:01:20] ── 记忆记录 ──
           事件写入 memory/aidc-001.db
           模式更新: (vLLM P95 高 + GPU-0 util 异常) → GPU 争用
```
