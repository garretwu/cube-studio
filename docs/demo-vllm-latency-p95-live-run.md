# Demo 实录模板: VLLMInterTokenLatencyP95High Live Alert + gpu_burn 注入

## 目标

这份文档对应 `VLLMInterTokenLatencyP95High` 场景的可运行 demo 骨架：

- 先用 `load_simulator` 给目标 vLLM 服务打持续推理流量
- 再用 `fault_injector` 在目标节点注入 `gpu_burn`
- 等待 `VLLMInterTokenLatencyP95High` live alert 触发
- 让 Agent 用 ReAct 完成只读诊断
- 最终生成 `kill_process` 修复计划

## 本次成功实录

对应输出文件：

- [vllm-inter-token-latency-p95-live-demo.txt](/root/workspace/cube-studio/data/demo/vllm-inter-token-latency-p95-live-demo.txt)

这次成功 run 的核心结果是：

- `alert_name = VLLMInterTokenLatencyP95High`
- `status = diagnosed`
- `p95 inter-token latency = 73.8ms`
- 阈值：`50ms`
- 根因：`fi_gpu_burn_gpu_contention_31304fd7`
- 修复计划：`kill_process(node="worker-03", pid=4095464)`

## 时间线

```text
[06:11:27] ┌─ Demo 启动 ──────────────────────────────────────────────┐
           │ 启动 load_simulator，对 qwen3-32b-fp8-202602261 打持续流量 │
           └──────────────────────────────────────────────────────────┘

[06:11:57] ── 故障注入 ──
           fault_injector 在 worker-03 上启动 gpu_burn
           场景: gpu_contention
           GPU: 0
           显存占用: 90%
           持续时间: 600s

[06:17:xx] ── 告警成立 ──
           Prometheus 观测到:
           VLLMInterTokenLatencyP95High
           value = 0.073836s = 73.8ms
           threshold = 50ms

[06:18:xx] ── Step 1 [Think] ──
           Agent 接收 live alert，决定先确认：
           1. latency 是否真的超阈
           2. GPU 是否存在资源争用
           3. 是否可能是网络/RDMA 或正常高负载

[06:18:xx] ── Step 2 [Act] ── tool_call
           → prometheus.query_instant(
               histogram_quantile(0.95, sum by(le) (
                 rate(vllm:inter_token_latency_seconds_bucket{namespace="service",service="qwen3-32b-fp8-202602261"}[1m])
               ))
             )

[06:18:xx] ── Step 2 [Observe] ──
           73.8ms
           "已高于 50ms 阈值，说明 inter-token latency 回归真实存在"

[06:18:xx] ── Step 3 [Act] ── tool_call
           → gpu.get_metrics(node="worker-03")

[06:18:xx] ── Step 3 [Observe] ──
           GPU-0 util=100%, mem=31194/32607 MB
           GPU-1 util=98%,  mem=22685/32607 MB
           GPU-2 util=97%,  mem=30347/32607 MB
           GPU-3 util=97%,  mem=30347/32607 MB
           "整机 GPU 已接近饱和，GPU contention 成为高优先级嫌疑"

[06:18:xx] ── Step 4 [Act] ── tool_call
           → gpu.get_processes(node="worker-03")

[06:18:xx] ── Step 4 [Observe] ──
           VLLM::Worker_TP0 / TP1
           fi_gpu_burn_gpu_contention_31304fd7 (PID 4095464, 8504MB)
           "确认存在 rogue gpu_burn 进程，并与 VLLM worker 竞争 GPU-0 资源"

[06:18:xx] ── Step 5 [Act] ── tool_call
           → k8s.list_pods(namespace="service", node="worker-03")

[06:18:xx] ── Step 5 [Observe] ──
           qwen3-32b-fp8-202602261-5495cc6f4b-kt6kd 处于 Running
           "业务 Pod 正常在跑，不像是 Pod 自身崩溃导致的延迟问题"

[06:18:xx] ── Step 6 [Act] ── tool_call
           → network.get_rdma_stats(node="worker-03")

[06:18:xx] ── Step 6 [Observe] ──
           mlx5_0/1 ACTIVE
           mlx5_200/1 ACTIVE
           "没有直接证据支持 RDMA 链路异常是主因"

[06:18:xx] ── Step 7 [Conclude] ──
           主因: GPU contention from rogue fi_gpu_burn process
           备选1: Network/RDMA issues → eliminated
           备选2: Normal load → eliminated
           置信度: 0.95

[06:18:xx] ── Step 8 [Remediate] ──
           proposal-only remediation plan:
           kill_process(node="worker-03", pid=4095464)

[06:21:57] ── 故障清理 ──
           fault_injector watchdog 到时自动回收
           pkill fi_gpu_burn_gpu_contention_31304fd7
```

## 配置文件

- [vllm-latency-p95-live-demo.yaml](/root/workspace/cube-studio/fault_injector/vllm-latency-p95-live-demo.yaml)

## 一键运行

```bash
cd /root/workspace/cube-studio

export OPENAI_API_KEY='你的兼容 OpenAI 接口 key'
export SRE_OPENAI_BASE_URL='https://coding.dashscope.aliyuncs.com/v1'

bash sre_agent/scripts/run_vllm_latency_p95_live_demo.sh \
  --demo-config fault_injector/vllm-latency-p95-live-demo.yaml \
  --model 'MiniMax-M2.5' \
  --output data/demo/vllm-latency-p95-live-demo.txt \
  --output-format txt
```

## Demo 结构

### 1. load_simulator 压测

脚本会先启动配置里的 `load_simulator_config`，持续对 vLLM endpoint 施加推理负载。

默认配置位于：

- [inference-qwen3-32b-fp8-10.11.4.3-20060.yaml](/root/workspace/cube-studio/load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml)

### 2. fault_injector 注入 gpu_burn

随后脚本会执行：

```bash
python -m fault_injector.cli run \
  --config fault_injector/vllm-latency-p95-live-demo.yaml \
  --scenario gpu_contention \
  --no-monitor \
  --yes
```

### 3. live alert 诊断

最后脚本会调用：

```bash
python sre_agent/scripts/vllm_latency_p95_live_demo.py \
  --demo-config fault_injector/vllm-latency-p95-live-demo.yaml
```

该脚本会围绕以下证据链做诊断：

1. `prometheus.query_instant`
  使用 `latency_promql` 验证 p95 inter-token latency 是否高于阈值
2. `gpu.get_metrics`
   查看目标节点 GPU 指标
3. `gpu.get_processes`
   查找 `gpu-burn` / `fi_gpu_burn_*` 等争用进程
4. `k8s.list_pods`
   确认受影响的服务 Pod
5. `network.get_rdma_stats`
   在需要时补充排查网络/RDMA 信号

## Agent 如何通过 ReAct 排查多个原因

这条 demo 不是“看见高延迟就直接下结论”，而是按照 ReAct 的方式一轮一轮收证据：

1. `Think`
   模型先根据 alert 和 prompt 形成初步假设：
   - GPU contention
   - Network/RDMA 问题
   - Normal high load
2. `Act`
   模型发出 tool call，请 Agent 执行只读工具
3. `Observe`
   Agent 把工具结果再喂回模型
4. 再次 `Think`
   模型根据新证据保留、削弱或排除候选原因
5. 证据足够后输出结构化 JSON

这次成功 run 最终给出的 hypotheses 是：

- `GPU contention from rogue fi_gpu_burn process` → `confirmed`
- `Network/RDMA issues causing latency` → `eliminated`
- `Normal load causing latency regression` → `eliminated`

也就是说，这条 demo 现在不只是“定位一个根因”，而是已经显式展示了多假设排查过程。

## 大模型和 Agent 的交互记录

这次我们把原始交互也一起记录到了输出文件里，位置在：

- [vllm-inter-token-latency-p95-live-demo.txt](/root/workspace/cube-studio/data/demo/vllm-inter-token-latency-p95-live-demo.txt)

可重点查看：

- `LLM Interactions`
- `prompt_messages`
- `response_message`
- `raw_response_text`

其中：

- `prompt_messages`
  是 Agent 实际发给大模型的 system prompt 和 human prompt
- `response_message`
  是大模型直接返回的消息对象，包含 tool calls
- `raw_response_text`
  是大模型原始文本回答

这样可以直接看到：

1. Agent 给了模型什么上下文
2. 模型先要求调用哪些工具
3. 模型拿到证据后如何形成最终 diagnosis 和 remediation plan

## 当前实现边界

这条 demo 是“当前工具能力下的最小可运行版本”，因此和概念文档存在一点差异：

- 已支持：
  - `prometheus.query_instant`
  - `gpu.get_metrics`
  - `gpu.get_processes`
  - `k8s.list_pods`
  - `network.get_rdma_stats`
  - `kill_process`
- 还没有独立 Agent tool 的步骤：
  - `get_inference_latency`
  - `get_thermal_status`
  - `check_nic_errors`

所以可运行 demo 会用：

- PromQL 查询 p95 latency
- RDMA 只读统计

来替代概念 Trace 里的专用热/网工具。

## 预期结果

当压测流量足够、`gpu_burn` 争用明显、并且告警真正触发时，预期会得到：

- `alert_name = VLLMInterTokenLatencyP95High`
- `status = diagnosed`
- 根因：GPU contention / rogue gpu_burn process
- remediation plan：`kill_process(node=..., pid=...)`

如果 live alert 没有触发，最常见原因是：

- 压测负载不够
- 注入强度不够
- 告警规则窗口尚未满足
- 当前服务瓶颈并不体现在 p95 inter-token latency
