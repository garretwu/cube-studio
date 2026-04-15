# Runbook: 使用 `load_simulator` + `fault_injector` 模拟 vLLM P95 Latency 过高告警

## 目标

这份手册用于复现 `VLLMLatencyP95High` 告警，并为后续的诊断 / 灰度修复 Demo 提供稳定输入。

演示目标是：

- 先用 `load_simulator` 给 vLLM 服务施加持续推理压力。
- 再用 `fault_injector` 注入 `gpu_contention` 故障。
- 让 Prometheus 侧出现 `vLLMLatencyP95High` 告警。
- 为 `sre_agent` 的诊断和灰度发布演示提供可重复的故障现场。

## 参考资料

- [Demo 实录：VLLMInterTokenLatencyP95High Live Alert + gpu_burn 注入](/home/yuyonghao/dev/cube-studio/Demobranch/cube-studio-track/docs/demo-vllm-latency-p95-live-run.md)
- [vLLM 灰度发布 Demo 设计](/home/yuyonghao/dev/cube-studio/Demobranch/cube-studio-track/sre_agent/docs/demo_vllm_p95_canary_demo.md)
- `sre_agent/scripts/run_vllm_latency_p95_live_demo.sh`
- `sre_agent/scripts/trigger_vllm_inter_token_latency_alert.py`
- `fault_injector/vllm-latency-p95-live-demo.yaml`

## 场景说明

推荐使用的默认场景如下：

- 目标服务：`qwen3-32b-fp8-202602261`
- 目标节点：`worker-03`
- 故障类型：`gpu_contention`
- 告警名：`VLLMLatencyP95High`
- Prometheus 查询：
  - `histogram_quantile(0.95, sum by(le) (rate(vllm:inter_token_latency_seconds_bucket{namespace="service",service="qwen3-32b-fp8-202602261"}[1m])))`

默认配置已经在 `fault_injector/vllm-latency-p95-live-demo.yaml` 中准备好：

- `load_simulator_config`: `load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml`
- `load_warmup_seconds`: `30`
- `alert_wait_seconds`: `360`
- `remediation_wait_seconds`: `60`

## 前置条件

运行前确认：

- 后端 Prometheus 可访问。
- `load_simulator` 的目标推理服务可访问。
- `fault_injector` 的目标节点 SSH 可访问。
- `sre_agent` 的 LLM / API key 已配置。
- 本地终端已进入仓库根目录。

建议先做一次只读检查：

```bash
cd /home/yuyonghao/dev/cube-studio/Demobranch/cube-studio-track
python -m load_simulator list-scenarios
python -m load_simulator validate-config load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml
```

## 运行方式总览

推荐按下面顺序执行：

1. 启动 `load_simulator`
2. 等待 warmup
3. 启动 `fault_injector`
4. 等待告警触发
5. 用 `sre_agent` 诊断或查看告警捕获结果

如果只想做告警复现，不跑完整诊断，可以先用告警捕获脚本确认告警已经出现。

## 方式 A: 一键联动运行

如果目标是完整演示链路，优先用一键脚本：

```bash
cd /home/yuyonghao/dev/cube-studio/Demobranch/cube-studio-track

export OPENAI_API_KEY='你的兼容 OpenAI 接口 key'
export SRE_OPENAI_BASE_URL='https://coding.dashscope.aliyuncs.com/v1'

bash sre_agent/scripts/run_vllm_latency_p95_live_demo.sh \
  --demo-config fault_injector/vllm-latency-p95-live-demo.yaml \
  --scenario gpu_contention \
  --model 'MiniMax-M2.5' \
  --output data/demo/vllm-latency-p95-live-demo.txt \
  --output-format txt
```

这个脚本内部会按顺序完成：

- 启动 `load_simulator run`
- 等待 `load_warmup_seconds`
- 启动 `fault_injector.cli run`
- 等待 `alert_wait_seconds`
- 启动 live diagnosis
- 可选执行 remediation

## 方式 B: 手动分步运行

如果你只想单独复现告警，建议分步执行。

### Step 1: 启动 load_simulator

使用配置文件中的 `load_simulator_config`：

```bash
cd /home/yuyonghao/dev/cube-studio/Demobranch/cube-studio-track

python -m load_simulator run \
  --config load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml \
  --only inference \
  --output-format json
```

说明：

- `--only inference` 用于只启动推理压测。
- `--output-format json` 便于上层脚本或人眼查看结果。
- 运行后保持该进程持续运行，不要过早停止。

### Step 2: 等待 warmup

建议至少等待 30 秒：

- 让推理流量稳定下来。
- 让 P95 曲线先形成可观察基线。
- 避免故障注入后误把冷启动波动当成异常。

### Step 3: 启动 fault_injector

使用 `gpu_contention` 场景：

```bash
python -m fault_injector.cli run \
  --config fault_injector/vllm-latency-p95-live-demo.yaml \
  --scenario gpu_contention \
  --no-monitor \
  --yes
```

说明：

- `--scenario gpu_contention` 是本 demo 的核心故障。
- `--no-monitor` 表示不额外启用 fault-injector 自己的监控循环。
- `--yes` 表示跳过交互确认，适合演示和自动化运行。

### Step 4: 等待告警出现

建议保留足够的观察时间：

- 默认可等待 3 到 6 分钟。
- 文档配置中默认 `alert_wait_seconds=360`。
- 如果现场环境较慢，可把等待时间适当拉长。

### Step 5: 触发告警捕获

可以用告警捕获脚本验证当前是否已经出现目标告警：

```bash
python sre_agent/scripts/trigger_vllm_inter_token_latency_alert.py \
  --demo-config fault_injector/vllm-latency-p95-live-demo.yaml \
  --scenario gpu_contention \
  --output data/demo/vllm-inter-token-latency-alert-capture.json \
  --output-format json
```

如果只想保留故障环境再做后续诊断，可以加 `--hold-seconds`：

```bash
python sre_agent/scripts/trigger_vllm_inter_token_latency_alert.py \
  --demo-config fault_injector/vllm-latency-p95-live-demo.yaml \
  --scenario gpu_contention \
  --hold-seconds 120 \
  --output data/demo/vllm-inter-token-latency-alert-capture.json \
  --output-format json
```

## 默认配置文件说明

### load_simulator 配置

`load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml`

关键字段：

- `inference.targets[0].endpoint`
- `inference.concurrency`
- `inference.duration_seconds`
- `inference.stream`

这份配置的作用是持续向 vLLM 推理端点施压，尽量保持稳定、可重复的延迟基线。

### fault_injector 配置

`fault_injector/vllm-latency-p95-live-demo.yaml`

关键字段：

- `monitor.prometheus_url`
- `demo.vllm_inter_token_latency_p95_alert_remediation.latency_promql`
- `demo.vllm_inter_token_latency_p95_alert_remediation.latency_threshold_ms`
- `demo.vllm_inter_token_latency_p95_alert_remediation.load_simulator_config`
- `scenarios.gpu_contention.target_nodes`
- `scenarios.gpu_contention.params.gpu_ids`

这份配置决定了：

- 告警由谁来监控
- 故障注入注入到哪台机器
- 故障的强度和持续时间

## 观测点

建议在演示期间观察下面几个信号：

- Prometheus 中 `VLLMLatencyP95High` 是否触发。
- vLLM 的 P95 是否从正常区间升高。
- 目标节点上 GPU utilization 是否明显升高。
- `gpu_contention` 进程或类似负载是否存在。
- 前端告警页是否出现对应事件。

## 常见问题

### 1. 没有触发告警

先检查：

- `load_simulator` 是否真的启动成功。
- `fault_injector` 是否已经开始注入。
- Prometheus URL 是否正确。
- 告警阈值是否过高。
- 目标服务是否与配置里的 service 名称一致。

### 2. 告警出现得太慢

可以尝试：

- 增加 `load_warmup_seconds`。
- 增加 `alert_wait_seconds`。
- 适当提高故障注入强度。

### 3. 告警出现但不稳定

可以检查：

- 目标服务是否本身有其他噪声。
- `load_simulator` 是否并发太低。
- `gpu_contention` 是否只造成短暂抖动。

### 4. 故障注入后不影响 P95

可以检查：

- `gpu_contention` 是否真的打到目标 GPU。
- 目标节点 SSH 和权限是否正常。
- demo 配置中的 `gpu_ids` 是否正确。

## 清理方式

如果你是手动跑的分步流程，最后建议按以下顺序清理：

1. 停止 `fault_injector`
2. 停止 `load_simulator`
3. 确认目标节点恢复正常
4. 复查告警是否解除

如果是用一键脚本，正常情况下脚本会在结束时自动清理子进程。

## 与灰度发布 Demo 的衔接

这份手册的定位是“把故障现场准备好”。

后续如果要进入灰度发布演示，接下来就是：

1. 让告警触发。
2. 进入 `sre_agent` 诊断。
3. 生成带 `canary` 的修复计划。
4. 展示先小范围验证，再扩展到全量。

也就是说，这份手册负责故障制造，灰度 Demo 负责修复展示。
