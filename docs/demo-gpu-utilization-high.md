# Demo: GPUUtilizationHigh 告警诊断与修复计划生成

## 场景设定

```text
前提：在 gpu-1-1 节点注入 GPU 资源争用
      GPU-0 上运行 gpu-burn 进程
      推理服务 vllm-deepseek 使用 GPU-1

告警：Prometheus alert "GPUUtilizationHigh"
      labels: {service="vllm-deepseek", namespace="service", node="gpu-1-1"}
      条件：GPU-0 util_pct > 90%
      持续：10 分钟
```

## Agent ReAct Trace

```text
[15:00:00] ┌─ Alert 接收 ─────────────────────────────────────────────┐
           │ alert: GPUUtilizationHigh                                │
           │ severity: critical                                       │
           │ labels: {service="vllm-deepseek", namespace="service",   │
           │          node="gpu-1-1"}                                 │
           │ value: GPU-0 util = 97.2% (阈值: 90%)                    │
           └──────────────────────────────────────────────────────────┘

[15:00:01] ── 构建初始上下文 ──
           · 从 Ontology 获取服务拓扑:
             InferenceService(vllm-deepseek) → Pod(vllm-xxx-abc123)
               → Node(gpu-1-1) → GPU-0, GPU-1
           · 从记忆检索: 无相似历史事件
           · 从知识库检索: GPU contention / vLLM tuning 文档 (2 chunks)

[15:00:02] ── Step 1 [Think] ──
           "收到 GPUUtilizationHigh 告警。需要确认这是正常推理负载升高，
            还是异常的 GPU 资源争用。
            当前假设：
            假设 A: GPU 资源争用（优先级最高）
            假设 B: GPU 热节流
            假设 C: 推理业务自身负载正常升高
            先查看节点上各 GPU 指标。"

[15:00:02] ── Step 2 [Act] ── tool_call
           → get_gpu_metrics(node="gpu-1-1")

[15:00:03] ── Step 2 [Observe] ──
           {per_gpu: [
             {index: 0, util_pct: 97.2, mem_used_mb: 74200, mem_total_mb: 81920,
              temp_c: 77, power_w: 282},
             {index: 1, util_pct: 67.1, mem_used_mb: 45100, mem_total_mb: 81920,
              temp_c: 71, power_w: 219}
           ]}
           "GPU-0 利用率异常高，但 GPU-1 处于正常推理范围，说明问题不是整机
            两块 GPU 同时繁忙，更像是 GPU-0 上有额外进程。"

[15:00:04] ── Step 3 [Think] ──
           "异常集中在 GPU-0，需要确认是否有非推理进程占用它。
            如果 GPU-0 上存在 gpu-burn 或训练类进程，则优先确认资源争用。"

[15:00:04] ── Step 4 [Act] ── tool_call
           → get_gpu_processes(node="gpu-1-1")

[15:00:05] ── Step 4 [Observe] ──
           {processes: [
             {pid: 12847, name: "gpu-burn", gpu_uuid: "GPU-0", memory_mb: 73800},
             {pid: 15923, name: "python3", gpu_uuid: "GPU-1", memory_mb: 44800}
           ]}
           "确认 gpu-burn 进程占用 GPU-0，且显存占用接近满载。
            假设 A 基本成立。"

[15:00:06] ── Step 5 [Act] ── tool_call
           → get_thermal_status(node="gpu-1-1")

[15:00:07] ── Step 5 [Observe] ──
           {temperatures: [
             {name: "GPU-0", reading: 77, threshold: 90},
             {name: "GPU-1", reading: 71, threshold: 90}
           ],
            fans: [
             {name: "Fan-1", speed_rpm: 8400, status: "OK"},
             {name: "Fan-2", speed_rpm: 8150, status: "OK"}
           ]}
           "温度低于阈值，风扇正常，未见热节流迹象。
            假设 B 排除。"

[15:00:08] ── Step 6 [Conclude] ──
           根因: GPU 资源争用
           层级: hardware
           置信度: 0.94
           详细: gpu-burn 进程占用 GPU-0 全部算力和大部分显存，
                 导致节点 GPU 资源异常紧张。推理服务本身运行在 GPU-1，
                 但共享主机资源与总线带宽，告警反映的是同节点异常负载。
           假设验证:
             A: GPU 争用 ✓ confirmed
             B: 热节流 ✗ eliminated
             C: 正常业务升高 ✗ unlikely

[15:00:08] ── Step 7 [Remediate] ──
           输出 proposal-only remediation plan
```

## 预期诊断结果与修复计划

```text
诊断结果:
  根因: GPU 资源争用（gpu-burn 占用 GPU-0）
  层级: hardware
  置信度: 0.94
  影响范围: gpu-1-1 节点上的推理业务存在被干扰风险

修复计划（proposal-only，未执行）:
  Step 1: kill_process(node="gpu-1-1", pid_or_name="gpu-burn")
          验证: get_gpu_metrics(node="gpu-1-1") → GPU-0 util < 10%
  Step 2: 等待 30-60s 观察节点资源恢复
  Step 3: get_inference_latency(service="vllm-deepseek")
          验证: 推理延迟恢复到基线范围

说明:
  - 该计划仅为 Agent 生成的修复建议，不代表已执行
  - 实际执行前仍需经过审批门控
```
