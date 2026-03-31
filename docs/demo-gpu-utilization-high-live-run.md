# Demo 实录: GPUUtilizationHigh Live Alert + gpu_burn 注入

## 目标

这份文档记录一次真实可复现的 Demo 流程：

- 先用 `fault_injector` 在 `worker-03` 上注入 `gpu_burn`
- 再让 `sre_agent/scripts/gpu_utilization_high_live_demo.py` 从 **live alert** 中选取 `GPUUtilizationHigh`
- Agent 使用 ReAct 做只读诊断
- 最终生成 `kill_process` 修复计划

这份文档对应的最新真实输出文件是：

- [gpu-utilization-high-demo-live-injected.txt](/root/workspace/cube-studio/data/demo/gpu-utilization-high-demo-live-injected.txt)

## 前提

使用的配置文件：

- [gpu-utilization-high-live-demo.yaml](/root/workspace/cube-studio/fault_injector/gpu-utilization-high-live-demo.yaml)

关键环境：

- 节点：`worker-03`
- 注入 GPU：`GPU-0`
- live alert 来源：Prometheus `GPUUtilizationHigh`
- 模型：`MiniMax-M2.5`

## 运行步骤

### 1. 启动 gpu_contention 注入

```bash
cd /root/workspace/cube-studio

bash sre_agent/scripts/run_gpu_contention_live_demo.sh \
  --demo-config fault_injector/gpu-utilization-high-live-demo.yaml \
  --model 'MiniMax-M2.5' \
  --output data/demo/gpu-utilization-high-demo-live-injected.txt \
  --output-format txt
```

这条一键脚本内部会先执行 fault injection，再触发 live demo 诊断。

本次最新实录里，fault injector session 结果是：

```text
session_id: 5476c33b
status: completed
```

### 2. demo 脚本实际调用

```bash
export OPENAI_API_KEY='你的兼容 OpenAI 接口 key'
export SRE_OPENAI_BASE_URL='https://coding.dashscope.aliyuncs.com/v1'

PYTHONPATH=/root/workspace/cube-studio /root/workspace/.cube/bin/python \
  sre_agent/scripts/gpu_utilization_high_live_demo.py \
  --demo-config fault_injector/gpu-utilization-high-live-demo.yaml \
  --model 'MiniMax-M2.5' \
  --output data/demo/gpu-utilization-high-demo-live-injected.txt \
  --output-format txt
```

## 真实告警细节

本次 demo 选中的 live alert 摘要如下：

```text
alert_name: GPUUtilizationHigh
status: firing
severity: warning
Hostname: wj-lab-cpt-03
instance: 10.0.10.239:9400
gpu: 0
device: nvidia0
exported_namespace: service
exported_pod: qwen3-32b-fp8-202602261-7f46f958db-dsblw
exported_container: qwen3-32b-fp8-202602261
```

说明：

- 现在 demo 不再自己构造 alert
- `namespace`、`pod_name`、`service`、`gpu` 都直接来自 live alert labels

## Agent 诊断结果

这次最新真实运行的最终状态：

```text
status: diagnosed
session_id: b788f5129c1b491fabcf76af1fdbabb2
```

Summary：

```text
GPU 0 is saturated at 100% by a contention test process, potentially impacting inference latency for qwen3-32b-fp8-202602261 service
```

诊断结论：

```text
root_cause: GPU contention caused by rogue contention test process
            (fi_gpu_burn_gpu_contention_581aa55f)
root_cause_layer: service
confidence: 0.95
affected_service: qwen3-32b-fp8-202602261
triage_priority: P1
```

## 关键证据

### 1. GPU 指标

Agent 调用了 `gpu.get_metrics`，看到：

```text
GPU 0, util=100%, mem_used=31194 MiB / 32607 MiB
GPU 1, util=98%
GPU 2, util=97%
GPU 3, util=97%
```

其中 `GPU-0` 被异常打满，是重点排查对象。

### 2. GPU 进程

Agent 调用了 `gpu.get_processes`，抓到了真实争用进程：

```text
1906726, fi_gpu_burn_gpu_contention_581aa55f, GPU-f33786b8-cfc3-17f6-98d5-81e16727ea64, 8504
```

同时也看到了同卡上的 vLLM worker：

```text
1735788, VLLM::Worker_TP0, ...
```

这说明：

- `gpu_burn` 注入确实落在了 `worker-03 / GPU-0`
- 它和真实推理进程共享同一张卡，构成 GPU contention

### 3. K8s 上下文

Agent 还调用了 `k8s.list_pods`，确认受影响的业务 pod 为：

```text
qwen3-32b-fp8-202602261-7f46f958db-dsblw
```

命名空间为：

```text
service
```

## 生成的修复计划

这次 agent 已经生成了正确的 `kill_process` 方案，不再是早期版本里不贴场景的 `k8s.delete_pod`：

```json
{
  "plan_id": "proposal-gpu-contention-fix",
  "root_cause": "Rogue GPU contention test process (fi_gpu_burn_gpu_contention_581aa55f) on GPU 0",
  "description": "Kill the rogue GPU burn/contention process to restore normal inference performance",
  "steps": [
    {
      "step_id": 1,
      "description": "Terminate the rogue GPU contention test process fi_gpu_burn_gpu_contention_581aa55f (PID 1906726) on worker-03",
      "tool": "kill_process",
      "params": {
        "node": "worker-03",
        "pid": 1906726
      },
      "verification": {
        "method": "wait",
        "wait_seconds": 30
      },
      "timeout": 60
    }
  ],
  "confidence": 0.9,
  "priority": "P1",
  "safety_level": "medium"
}
```

## 修复说明

这次 demo 的“真实清理”由 fault injector session 自动完成：

- 读取 pid file
- 发送 `SIGTERM`
- 再执行 `pkill -f fi_gpu_burn_gpu_contention_581aa55f`

也就是说：

- Agent 已经正确生成了 `kill_process` remediation plan
- 本次现场故障也已经被 fault injector 自动回收
- 但 demo 没有再额外调用 Agent 的 write-tool 执行这条 plan，避免和 fault injector 的回收动作重叠

## 结论

这次 live demo 已经打通了完整主链路：

1. `gpu_burn` 真实注入到 `worker-03 / GPU-0`
2. Prometheus live alert 触发 `GPUUtilizationHigh`
3. Agent 从 live alert labels 自动提取 `gpu / namespace / pod / service`
4. ReAct 诊断识别出 `fi_gpu_burn_gpu_contention_*`
5. 输出正确的 `kill_process` 修复计划

## 最新实录摘要

```text
Run date: 2026-03-26 UTC
Fault injector session: 5476c33b
Diagnosis session: b788f5129c1b491fabcf76af1fdbabb2
Injected process: fi_gpu_burn_gpu_contention_581aa55f
Detected pid: 1906726
Result: diagnosed
Recommended action: kill_process(node="worker-03", pid=1906726)
```

这条链路已经足够作为 GPU contention 场景的现场演示基线。
