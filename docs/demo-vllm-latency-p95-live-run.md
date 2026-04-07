# Demo 实录: VLLMInterTokenLatencyP95High Live Alert + gpu_burn 注入

## 目标

这份文档对应 `VLLMInterTokenLatencyP95High` 场景的一次真实 live diagnosis 实录：

- 先用 `load_simulator` 给目标 vLLM 服务打持续推理流量
- 再用 `fault_injector` 在目标节点注入 `gpu_burn`
- 等待 `VLLMInterTokenLatencyP95High` live alert 触发
- 让 Agent 用 ReAct 完成 live diagnosis
- 生成 remediation plan
- 记录 LLM / skill / tool 的真实交互链路

## 本次成功实录

对应输出文件：

- [vllm-inter-token-latency-p95-live-demo.txt](/root/workspace/cube-studio/data/demo/vllm-inter-token-latency-p95-live-demo.txt)

这次 skill-enabled run 的核心结果是：

- `alert_name = VLLMInterTokenLatencyP95High`
- `alert_source = live`
- `status = diagnosed`
- `session_id = c2c7651107c142699f296380b07a2a70`
- 告警值：`48.78ms`
- 阈值：`50ms`
- 目标服务：`qwen3-32b-fp8-202602261`
- 目标 Pod：`qwen3-32b-fp8-202602261-5495cc6f4b-kt6kd`
- 目标节点：`worker-03 (10.11.4.12)`
- 选中的 skill：`builtin-vllm-diagnosis`
- 根因：`GPU compute saturation on worker-03`
- remediation proposal：`k8s.scale_deployment(namespace="service", name="qwen3-32b-fp8-202602261", replicas=2)`
- remediation execution：`executed = false`

这轮也验证了运行时拓扑增强已经生效：

- alert 会先自动解析出 `service -> pod -> node`
- 诊断前会补全 runtime topology：
  - `service`
  - `pod`
  - `node`
  - `gpu`
  - `nic/rdma`
  - `switch`
  - `switch_port`
- `Topology Context` 会和告警一起提供给 LLM

## 时间线

```text
[09:21:xx] ┌─ Alert 接收 ─────────────────────────────────────────────┐
           │ 收到 live alert: VLLMInterTokenLatencyP95High            │
           │ labels: namespace=service, service=qwen3-32b-fp8-202602261 │
           └──────────────────────────────────────────────────────────┘

[09:21:xx] ── Topology Context 构建 ──
           根据 alert payload 自动解析:
           service = qwen3-32b-fp8-202602261
           -> pod = qwen3-32b-fp8-202602261-5495cc6f4b-kt6kd
           -> node_ip = 10.11.4.12
           -> node = worker-03

           诊断前补全 runtime topology:
           service -> pod -> node
           node -> nic/rdma
           switch -> switch_port
           并将 Topology Context 一并提供给 LLM

[09:21:xx] ── Step 1 [Skill Selection] ──
           Agent 首轮不直接查 tool，而是先判断是否应使用 reusable skill
           top skill = builtin-vllm-diagnosis
           match_score = 0.0246
           结论: 先执行 builtin-vllm-diagnosis

[09:21:xx] ── Step 2 [Skill Execution] ──
           builtin-vllm-diagnosis 固定执行:
           1. prometheus.query_instant(latency_promql)
           2. k8s.list_pods(namespace="service")
           3. gpu.get_metrics(node="worker-03")

[09:21:xx] ── Step 2 [Observe] ──
           skill result:
           - P95 inter-token latency = 48.8ms
           - namespace 内目标 pod 正在 Running
           - worker-03 上 GPU utilization = 97-98%
           "skill 已证明 latency 升高且整机 GPU 极度繁忙"

[09:21:xx] ── Step 3 [Think] ──
           Agent 读取 skill 结果后判断：
           1. compute saturation 信号很强
           2. 仍需补充 pod/node 与 RDMA 证据
           3. 继续少量 ReAct 证据收集后再收敛

[09:21:xx] ── Step 4 [Act] ── tool_call
           → k8s.resolve_pod_node_ip(namespace="service", pod_name="qwen3-32b-fp8-202602261-5495cc6f4b-kt6kd")
           → network.get_rdma_stats(node="worker-03")

[09:21:xx] ── Step 4 [Observe] ──
           - pod 所在节点 = 10.11.4.12 / worker-03
           - RDMA:
             mlx5_0/1 ACTIVE
             mlx5_1/1 DOWN
             mlx5_200/1 ACTIVE
           "存在冗余链路 down，但主工作链路仍可用，网络不是最强主因"

[09:21:xx] ── Step 5 [Act] ── tool_call
           → prometheus.query_instant(gpu_token_latency_promql)

[09:21:xx] ── Step 5 [Observe] ──
           结合 skill 结果与补充证据，模型收敛为：
           - GPU 侧证据强
           - pod 正常运行
           - RDMA 仅为次要异常

[09:21:xx] ── Step 6 [Conclude] ──
           主因: GPU compute saturation on worker-03
           层级: hardware
           影响对象:
             - worker-03
             - qwen3-32b-fp8-202602261-5495cc6f4b-kt6kd

           hypotheses:
           - GPU compute saturation → confirmed
           - RDMA network issue → eliminated
           - vLLM configuration/model issue → testing

[09:21:xx] ── Step 7 [Remediation Plan] ──
           remediation proposal:
           k8s.scale_deployment(
             namespace="service",
             name="qwen3-32b-fp8-202602261",
             replicas=2
           )

[09:21:xx] ── Step 8 [Execution] ──
           本轮未执行 remediation
           executed = false
           原因: 直接运行诊断脚本，未开启 execute-remediation
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

该脚本会在诊断前先构造 `Topology Context`，再围绕以下证据链做诊断：

1. alert payload
   提供 `alert_name / namespace / service / description / threshold breach`
2. runtime topology
   自动补全：
   - `service -> pod -> node`
   - `gpu`
   - `nic/rdma`
   - `switch`
   - `switch_port`
3. `prometheus.query_instant`
   使用 `latency_promql` 验证 p95 inter-token latency 是否高于阈值
4. `gpu.get_metrics`
   查看目标节点 GPU 指标
5. `gpu.get_processes`
   查找 `gpu_burn` / `fi_gpu_burn_*` 等争用进程
6. `k8s.list_pods`
   确认受影响的服务 Pod
7. `network.get_rdma_stats`
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

这次完整 run 最终给出的 hypotheses 是：

- `GPU compute saturation causing request queuing and latency increase` → `confirmed`
- `RDMA network issue causing latency` → `eliminated`
- `vLLM configuration or model issue` → `testing`

也就是说，这条 demo 现在不只是“定位一个根因”，而是已经显式展示了多假设排查过程。

## 本次 run 的边界

这次记录的是一次真实的 `skill_selection -> skill_execution -> ReAct` 诊断链路，修复没有执行：

- selected skill: `builtin-vllm-diagnosis`
- remediation tool proposal: `k8s.scale_deployment`
- execution result: `executed = false`
- 原因：这次是直接运行 `vllm_latency_p95_live_demo.py`，未开启 remediation execution

因此这次 run 实际覆盖的是：

1. 告警接收
2. 拓扑扫描
3. skill selection
4. skill execution
5. ReAct 诊断
6. remediation proposal 生成

如果需要完整覆盖“执行修复 + 验证恢复”，应使用带 `--execute-remediation` 的完整闭环运行。

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
  是 Agent 实际发给大模型的 system prompt 和 human prompt，其中已经包含 `Topology Context`
- `response_message`
  是大模型直接返回的消息对象，包含 tool calls
- `raw_response_text`
  是大模型原始文本回答

这样可以直接看到：

1. Agent 给了模型什么上下文
2. 模型先要求调用哪些工具
3. 模型拿到证据后如何形成最终 diagnosis 和 remediation plan

## 当前实现边界

这条 demo 现在已经补上了高层语义诊断 tool，但和概念文档仍有一点差异：

- 已支持：
  - `get_inference_latency`
  - `gpu.get_metrics`
  - `gpu.get_processes`
  - `get_thermal_status`
  - `k8s.list_pods`
  - `network.get_rdma_stats`
  - `check_nic_errors`
  - `kill_process`
- 仍然存在的差异：
  - `get_inference_latency` 当前默认基于 `vllm:inter_token_latency_seconds_bucket` 封装，而不是 request duration
  - `get_thermal_status` 当前能稳定覆盖 GPU 温度和主机 `sensors` 输出，但不是完整硬件管理面采集
  - `check_nic_errors` 当前基于 `ip -s link` + `ethtool -S`，更偏 Linux 主机网卡视角

所以当前 demo 已经能用“正式 tool”表达概念文档里的大部分步骤，只是底层数据源和理想化版本还有一点实现层差异。

## 预期结果

当压测流量足够、`gpu_burn` 争用明显、并且告警真正触发时，预期会得到：

- `alert_name = VLLMInterTokenLatencyP95High`
- `status = diagnosed`
- 先进行 `skill_selection`
- 命中 `builtin-vllm-diagnosis` 或继续直接 tool-driven ReAct
- 根因收敛到 GPU / 网络 / 配置三类候选中的一个主因
- remediation plan：生成 proposal-only，或在开启执行开关时进入 remediation execution

如果 live alert 没有触发，最常见原因是：

- 压测负载不够
- 注入强度不够
- 告警规则窗口尚未满足
- 当前服务瓶颈并不体现在 p95 inter-token latency
