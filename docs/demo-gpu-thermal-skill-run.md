# Demo 实录: GPUTemperatureHighWjLabCpt04 Live Alert + Claude-style Skill

## 目标

这份文档对应 `GPUTemperatureHighWjLabCpt04` 场景的一次真实 live diagnosis 实录：

- 直接从 Prometheus / AlertChannel 读取真实 GPU 温度告警
- 让 Agent 在统一 ReAct 主链中自主决定是否使用 skill
- 验证模型会不会先：
  - `skills.list_skills`
  - `skills.load_skill`
  - `skills.run_skill`
- 验证 `gpu-fault-sop` 是否会被命中
- 验证 thermal 场景下是否会优先执行 `gpu_health_check.sh`
- 记录 LLM / skill / tool 的真实交互链路

## 本次成功实录

对应输出文件：

- [gpu-temp-skill-alert-demo.json](/root/workspace/cube-studio/data/demo/gpu-temp-skill-alert-demo.json)
- [gpu-temp-skill-alert-demo.run.log](/root/workspace/cube-studio/data/demo/gpu-temp-skill-alert-demo.run.log)

这次完整 run 的核心结果是：

- `alert_name = GPUTemperatureHighWjLabCpt04`
- `alert_source = live`
- `status = diagnosed`
- `session_id = 3e7c24109d0d4eefac13806828446f34`
- 真实告警值：`53C`
- 节点：`wj-lab-cpt-04`
- GPU：`0`
- 选中的 skill：`gpu-fault-sop`
- 执行的脚本：`gpu_health_check.sh`
- 根因：`thermal management failure on wj-lab-cpt-04`
- remediation proposal：降低全部 GPU power limit 到 `400W`
- remediation execution：`executed = false`

这次最重要的验证点是：

- 读的是**真实告警**，不是 synthetic alert
- Agent 没有走旧的图内专用 skill 分支，而是在统一 ReAct 里通过固定 `skills.*` tools 自主使用 skill
- 模型没有再误选 `gpu_benchmark.sh`
- thermal 场景下正确优先执行了 `gpu_health_check.sh`

## 时间线

```text
[07:17:xx] ┌─ Alert 接收 ─────────────────────────────────────────────┐
           │ 收到 live alert: GPUTemperatureHighWjLabCpt04           │
           │ node = wj-lab-cpt-04                                    │
           │ gpu = 0                                                 │
           │ value = 53C                                             │
           └──────────────────────────────────────────────────────────┘

[07:17:xx] ── Step 1 [Think] ──
           Agent 读取真实 alert payload 后，先判断：
           1. 这是 GPU / thermal / hardware 相关问题
           2. 是否存在强匹配的 reusable skill
           3. 若 skill 足够匹配，优先走 skills.* 链路

[07:17:xx] ── Step 2 [Act] ── tool_call
           → skills.list_skills()

[07:17:xx] ── Step 2 [Observe] ──
           返回 skill catalog。
           模型看到：
           - gpu-fault-sop
           - tags 含 gpu / thermal / fault / xid / pcie
           结论：
           当前告警与 gpu-fault-sop 强匹配

[07:17:xx] ── Step 3 [Act] ── tool_call
           → skills.load_skill(skill_id="gpu-fault-sop")

[07:17:xx] ── Step 3 [Observe] ──
           返回：
           - skill 正文
           - scripts = [gpu_benchmark.sh, gpu_health_check.sh]

           模型从 skill 文本中读到：
           - thermal / health alert 首选 gpu_health_check.sh
           - gpu_benchmark.sh 只用于性能基线和退化对比

[07:17:xx] ── Step 4 [Act] ── tool_call
           → skills.run_skill(
               skill_id="gpu-fault-sop",
               script="gpu_health_check.sh",
               ...
             )

[07:17:xx] ── Step 4 [Observe] ──
           gpu_health_check.sh 真实执行成功。
           收集到了：
           - GPU 温度
           - clocks
           - nvidia-smi 基础状态
           - PCIe / XID / 基础健康信息

           关键观察：
           - GPU 0 当前温度 = 53C
           - GPU 1/2/3 的 SM clock = 180 MHz
           - max clock = 3090 MHz
           - 实际只剩约 5%，说明已严重 throttling

[07:17:xx] ── Step 5 [Think] ──
           Agent 结合：
           - 真实 alert payload
           - gpu-fault-sop 正文
           - gpu_health_check.sh 输出
           开始收敛：
           1. 不是简单单卡轻微升温
           2. 是整机热管理问题
           3. 多张 GPU 已严重降频
           4. inference / training workload 会明显掉速甚至失败

[07:17:xx] ── Step 6 [Conclude] ──
           主因: thermal management failure on wj-lab-cpt-04
           层级: hardware
           影响对象:
             - wj-lab-cpt-04
             - GPU 0 / 1 / 2 / 3

           hypotheses:
           - Inadequate cooling causing thermal throttling → confirmed
           - Ambient environment issue → testing
           - Individual GPU hardware failure → eliminated

[07:17:xx] ── Step 7 [Remediation Plan] ──
           remediation proposal:
           1. 将全部 GPU power limit 降到 400W
           2. 再次检查 fan / temperature / power / clocks

[07:17:xx] ── Step 8 [Execution] ──
           本轮未执行 remediation
           executed = false
           原因: 当前 demo 只生成 proposal，不自动执行写操作
```

## 本次真实 alert

本轮抓到的告警 payload 关键信息如下：

- `alert_name = GPUTemperatureHighWjLabCpt04`
- `status = firing`
- `severity = warning`
- `Hostname = wj-lab-cpt-04`
- `gpu = 0`
- `instance = 10.0.12.137:9400`
- `pod = dcgm-exporter-t8hdj`
- `service = dcgm-exporter`
- `description = GPU 0 on wj-lab-cpt-04 temperature is above 30C. instance=10.0.12.137:9400, value=53C`

注意：

- `30C` 是告警阈值
- `53C` 才是这次 run 的真实观测值

## 实际 tool 调用顺序

这次 run 的真实 `tool_runs` 顺序是：

1. `skills.list_skills`
2. `skills.load_skill`
3. `skills.run_skill`

这说明当前 GPU thermal 场景已经符合新的 skill 设计目标：

- skill 不是图里的专用子分支
- Agent 在统一 ReAct 中自己发现并使用 skill
- skill 通过固定 4 个 `skills.*` tools 被调用

## 为什么这次选对了脚本

此前 thermal 场景里出现过一个问题：

- 模型先选中了 `gpu-fault-sop`
- 但错误地执行了 `gpu_benchmark.sh`

这次已经修正，原因有两层：

1. prompt 明确要求：
   - 先 `load_skill`
   - 再从 `scripts` 列表里选择具体脚本
   - 禁止不带 `script` 调 `run_skill`
2. `gpu-fault-sop/SKILL.md` 明确新增了 `Script Selection Guidance`
   - thermal / health / XID / PCIe / memory / 掉卡类告警优先 `gpu_health_check.sh`
   - `gpu_benchmark.sh` 只用于 baseline / compare

## 运行脚本

- [gpu_thermal_skill_demo.py](/root/workspace/cube-studio/sre_agent/scripts/gpu_thermal_skill_demo.py)

默认行为：

- 从 [config.yaml](/root/workspace/cube-studio/sre_agent/conf/config.yaml) 读取 `global.prometheus_url`
- 抓真实 `GPUTemperatureHighWjLabCpt04`
- 直接用真实 live alert 进入 diagnosis

## 一键运行

```bash
cd /root/workspace/cube-studio

export OPENAI_API_KEY='你的兼容 OpenAI 接口 key'
export SRE_OPENAI_BASE_URL='https://coding.dashscope.aliyuncs.com/v1'
export SRE_LLM_MODEL='MiniMax-M2.5'

PYTHONPATH=/root/workspace/cube-studio /root/workspace/.cube/bin/python \
  sre_agent/scripts/gpu_thermal_skill_demo.py \
  --config sre_agent/conf/config.yaml \
  --alert-name GPUTemperatureHighWjLabCpt04 \
  --output data/demo/gpu-temp-skill-alert-demo.json \
  --output-format json
```

## 本次 run 的边界

- 这轮 diagnosis 是真实 alert + 真实 skill + 真实脚本执行
- 但 remediation 仍然只是 proposal，不会自动落地
- `gpu_health_check.sh` 读取的是现场真实 `nvidia-smi` / dmesg / PCIe 信息
- 这条 demo 主要验证的是：
  - live alert ingestion
  - Claude-style skill selection
  - script selection
  - script execution
  - diagnosis synthesis
