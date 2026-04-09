# Demo 实录: GPUTemperatureHighWjLabCpt04 Live Alert + Fault Injection + Claude-style Skill

## 目标

这份文档对应 `GPUTemperatureHighWjLabCpt04` 场景的一次真实完整 run，验证的是整条闭环：

- 注入故障：
  - BMC 风扇切到 `Manual`
  - 全部风扇固定 `PWM 80`
  - 启动 4 卡 `gpu_burn`
- 等待真实 GPU 温度告警进入 `firing`
- 让 Agent 在统一 ReAct 主链里自主使用 Claude-style skill
- 继续补普通 GPU / BMC 工具证据
- 生成保守 remediation proposal
- 自动清理注入现场：
  - 停掉 `gpu_burn`
  - 风扇恢复 `Auto`

## 本次成功实录

对应输出文件：

- [gpu-temp-skill-alert-demo.json](/root/workspace/cube-studio/data/demo/gpu-temp-skill-alert-demo.json)
- [gpu-temp-skill-alert-demo.inject.trace.log](/root/workspace/cube-studio/data/demo/gpu-temp-skill-alert-demo.inject.trace.log)

这次完整 run 的核心结果是：

- `alert_name = GPUTemperatureHighWjLabCpt04`
- `alert_source = live`
- `status = diagnosed`
- `current_alert_count = 0`
- `history_alert_count = 1`
- 节点：`wj-lab-cpt-04`
- 注入前风扇：
  - `fanMode = 0`
  - `fanPWM = 35`
- 注入后风扇：
  - `fanMode = 1`
  - 全部风扇 `fanPWM = 80`
- 启动了 4 卡 `gpu_burn`
- 选中的 skill：`gpu-fault-sop`
- 这次没有执行 skill 脚本
- 而是先用普通 GPU / BMC 工具采证，再通过 `skills.load_skill` 读取 SOP 知识来收敛结论
- 本轮关键工具：
  - `gpu.get_metrics`
  - `gpu.get_processes`
  - `bmc.get_fan_status`
  - `skills.load_skill`
- 最终根因：
  - `Fan control mode is locked in Manual (fanMode=1) at fixed PWM 80, preventing dynamic fan speed adjustment in response to GPU temperature spikes from heavy workload (gpu_burn stress test)`
- remediation proposal：
  - `bmc.set_fan_control(node="wj-lab-cpt-04", mode="auto")`
- remediation execution：
  - `executed = false`
- cleanup 已成功：
  - `gpu_burn` 已停止
  - 风扇已恢复 `Auto`

这次最重要的验证点是：

- 读的是**真实 live alert**
- 注入链路、诊断链路、清理链路都跑通了
- Agent 在统一 ReAct 主链中通过固定 `skills.*` tools 自主使用 skill
- thermal 场景下，模型已经不依赖 demo 强提示，也能主动去查 `bmc.get_fan_status`
- 这次是“先采证，再 load skill 读取 SOP” 的路径，不是先跑 skill 脚本
- 最终结论不是“单纯高温”，而是“风扇被锁死在 Manual 80，遇到 4 卡 `gpu_burn` 压力时失去动态调速能力”
- `bmc.get_fan_status` 的语义化摘要字段
  - `mode_name = Manual`
  - `is_manual = true`
  - `is_fixed_pwm = true`
  明显降低了模型误读 `fanMode=1` 的概率

## 时间线

```text
[注入阶段] ┌─ Fault Injection ───────────────────────────────────────┐
           │ 风扇从 Auto 35 切到 Manual 80                          │
           │ 启动 4 卡 gpu_burn                                     │
           │ 等待 GPUTemperatureHighWjLabCpt04 进入 firing         │
           └─────────────────────────────────────────────────────────┘

[告警阶段] ── Alert 接收 ──
           当前 current alerts 为空，但 history 中存在 firing alert
           wait_for_current_alert() 选中了这条 live firing alert

[Step 1] ── Act ──
           → skills.list_skills()

[Step 1] ── Observe ──
           返回 skill catalog。
           模型识别出：
           - gpu-fault-sop
           - 与 GPU / thermal / fault 场景强相关

[Step 2] ── Act ──
           → gpu.get_metrics(node="wj-lab-cpt-04")

[Step 2] ── Observe ──
           返回真实 GPU 指标：
           - 4 张卡全部 100% util
           - GPU 温度约 42C~46C
           - 每张卡显存占用约 31809 MB
           结论：
           - 现场正在承受 4 卡高压负载

[Step 3] ── Act ──
           → gpu.get_processes(node="wj-lab-cpt-04")

[Step 3] ── Observe ──
           返回 4 个 `./gpu_burn`
           结论：
           - 高温不是空载异常，而是 gpu_burn stress test 下触发

[Step 4] ── Act ──
           → bmc.get_fan_status(node="wj-lab-cpt-04")

[Step 4] ── Observe ──
           返回：
           - `mode_name = Manual`
           - `is_manual = true`
           - `is_fixed_pwm = true`
           - `fixed_pwm = 80`
           结论：
           - 风扇控制不是自动调速，而是被锁在固定 Manual 80

[Step 5] ── Act ──
           → skills.load_skill(skill_id="gpu-fault-sop")

[Step 5] ── Observe ──
           返回 SOP 正文。
           模型从 skill 中读到：
           - `2E: 温度异常 -> Step 2`
           - `fanMode = 1` 表示 `Manual`
           - 若风扇被锁到 `Manual` 且 PWM 固定，则把“风扇控制策略异常”视为强根因候选

[Step 6] ── Conclude ──
           主因不是单独硬件故障，而是：
           - 风扇被锁在 Manual 80
           - 同时 4 卡 gpu_burn 压力很高
           - 风扇失去动态调速能力
           - 在持续重载下触发 GPU 温度告警

[Step 7] ── Remediation Plan ──
           生成保守 proposal：
           - bmc.set_fan_control(node="wj-lab-cpt-04", mode="auto")

[收尾阶段] ── Cleanup ──
           自动停止 gpu_burn
           自动把风扇恢复成 Auto
           输出结果文件
```

## 本次真实 alert

本轮抓到的真实告警 payload 关键信息如下：

- `alert_name = GPUTemperatureHighWjLabCpt04`
- `status = firing`
- `severity = warning`
- `Hostname = wj-lab-cpt-04`
- `gpu = 0`
- `instance = 10.0.12.137:9400`
- `pod = dcgm-exporter-t8hdj`
- `service = dcgm-exporter`

这轮 demo 在诊断时既参考了当前 alert，也接受了 history 中仍在 `firing` 的同名告警。

## 实际 tool 调用顺序

这次 run 的真实 `tool_runs` 顺序是：

1. `skills.list_skills`
2. `gpu.get_metrics`
3. `gpu.get_processes`
4. `bmc.get_fan_status`
5. `skills.load_skill`

这说明当前 GPU thermal 场景已经符合新的 skill 设计目标：

- skill 不是图里的专用子分支
- Agent 在统一 ReAct 中自己发现并使用 skill
- 这次不是“先跑 skill 脚本”，而是“先采证，再读取 skill 里的 SOP 知识做收敛”

## 每一步关键结果摘录

### Step 1: `skills.list_skills`

输入：

- 无参数

输出摘录：

- `gpu-fault-sop`
- `scripts = [gpu_benchmark.sh, gpu_health_check.sh]`

### Step 2: `gpu.get_metrics`

输入：

- `node = "wj-lab-cpt-04"`

输出摘录：
输出摘录：

```text
0, NVIDIA GeForce RTX 5090, 100, 31809, 32607, 46
1, NVIDIA GeForce RTX 5090, 100, 31809, 32607, 44
2, NVIDIA GeForce RTX 5090, 100, 31809, 32607, 43
3, NVIDIA GeForce RTX 5090, 100, 31809, 32607, 42
```

结论：

- 4 张卡都在重载
- 这不是单卡孤立问题，而是整机高压负载场景

### Step 3: `gpu.get_processes`

输入：

- `node = "wj-lab-cpt-04"`

输出摘录：

```text
2448035, ./gpu_burn, GPU-..., 31800+
2448054, ./gpu_burn, GPU-..., 31800+
2448055, ./gpu_burn, GPU-..., 31800+
2448057, ./gpu_burn, GPU-..., 31800+
```

结论：

- 当前现场确实有 4 个 `gpu_burn`
- 高温是 stress test 直接造成的

### Step 4: `bmc.get_fan_status`

输入：

- `node = "wj-lab-cpt-04"`

输出摘录：

- `mode_name = "Manual"`
- `is_manual = true`
- `is_fixed_pwm = true`
- `fixed_pwm = 80`
- `fan_count = 11`

结论：

- 现在这个 tool 已经不只返回原始 `fanMode = 1`
- 而是直接返回语义化摘要，模型可以明确知道：
  - 当前风扇处于 `Manual`
  - 并且所有风扇都被固定在 `80%`

### Step 5: `skills.load_skill`

输入：

- `skill_id = "gpu-fault-sop"`

输出摘录：

- skill 文本中 `2E: 温度异常 -> Step 2` 明确写了：
  - `fanMode = 1` 表示 `Manual`
  - 若风扇被锁到 `Manual` 且 PWM 固定，则把“风扇控制策略异常”视为强根因候选

结论：

- 这次模型不需要靠 demo query 强提示
- 而是通过 `skills.load_skill` 读取 SOP 本身，完成了对 fan 证据的正确解释

## Fault Injection 与 Cleanup

这轮完整注入与清理过程都记录在：

- [gpu-temp-skill-alert-demo.inject.trace.log](/root/workspace/cube-studio/data/demo/gpu-temp-skill-alert-demo.inject.trace.log)

关键 trace 事件包括：

- `inject_faults_fan_applied`
- `inject_faults_gpu_burn_started`
- `wait_for_alert_done`
- `diagnosis_done`
- `cleanup_stop_gpu_burn_done`
- `cleanup_restore_fan_auto_done`
- `output_written`

这说明本轮已经不是“部分成功”的诊断演示，而是：

- fault injection 成功
- 真实告警被捕获
- diagnosis 成功
- cleanup 成功

## 为什么这次结论和上一轮不一样

这次最新 run 和前一版相比，多了一个关键变化：

- `bmc.get_fan_status` 不再只返回底层字段 `fanMode = 1`
- 现在会额外返回：
  - `mode_name = "Manual"`
  - `is_manual = true`
  - `is_fixed_pwm = true`
  - `fixed_pwm = 80`

再加上 `gpu-fault-sop` 在 `2E: 温度异常 -> Step 2` 里已经明确写了：

- `fanMode = 1` 表示 `Manual`
- `Manual + 固定 PWM` 是强根因候选

所以现在这条结论已经主要由：

- skill 知识
- 语义化 tool 输出

共同驱动，而不是依赖 demo 自己去硬推。

## 运行脚本

- [gpu_thermal_skill_demo.py](/root/workspace/cube-studio/sre_agent/scripts/gpu_thermal_skill_demo.py)

默认行为：

- 从 [config.yaml](/root/workspace/cube-studio/sre_agent/conf/config.yaml) 读取 `global.prometheus_url`
- 抓真实 `GPUTemperatureHighWjLabCpt04`
- 在需要时可开启 fault injection

## 一键运行

```bash
cd /root/workspace/cube-studio

export OPENAI_API_KEY='你的兼容 OpenAI 接口 key'
export SRE_OPENAI_BASE_URL='https://coding.dashscope.aliyuncs.com/v1'
export SRE_LLM_MODEL='MiniMax-M2.5'
export SRE_REDFISH_USERNAME='admin'
export SRE_REDFISH_PASSWORD='你的 BMC 密码'

PYTHONPATH=/root/workspace/cube-studio /root/workspace/.cube/bin/python \
  sre_agent/scripts/gpu_thermal_skill_demo.py \
  --config sre_agent/conf/config.yaml \
  --alert-name GPUTemperatureHighWjLabCpt04 \
  --inject-faults \
  --inject-fan-pwm 80 \
  --output data/demo/gpu-temp-skill-alert-demo.json \
  --output-format json
```

## 本次 run 的边界

- 这轮 diagnosis 是真实 alert + 真实 fault injection + 真实 skill + 真实 SSH GPU / BMC tools
- remediation 仍然只是 proposal，没有自动执行 `bmc.set_fan_control(mode="auto")`
- 但 cleanup 阶段已经自动把风扇恢复回 `Auto`
- 风扇刚切回 `Auto` 时，立即回读有可能暂时还看到较高 PWM；几秒后会稳定回到自动值
- 当前这条 demo 主要验证的是：
  - fault injection
  - alert ingestion
  - Claude-style skill selection
  - 先采证，再读取 skill SOP 知识
  - skill 后继续补普通 GPU / BMC tools
  - diagnosis synthesis
  - cleanup 自动收尾
