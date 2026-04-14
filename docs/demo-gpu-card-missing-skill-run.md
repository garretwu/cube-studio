# GPU 掉卡 Skill Demo

这条 demo 演示一条完整的 live 告警闭环：

1. 先对 `worker-04` 上的 `0000:01:00.0` 执行真实掉卡注入
2. 等待真实 `GPUCardMissing` 告警
3. Agent 命中 `builtin-gpu-drop-diagnosis`
4. 通过 `gpu_drop_recover.sh` 自动完成诊断与恢复
5. 输出最终 diagnosis / remediation summary

结果文件：

- [gpu-card-missing-live-demo.json](/root/workspace/cube-studio/data/demo/gpu-card-missing-live-demo.json)

## 本次真实场景

- 目标节点：`worker-04`
- SSH 管理地址：`10.11.4.13`
- 掉卡目标：`GPU 0`
- 目标 BDF：`0000:01:00.0`
- 告警名称：`GPUCardMissing`

## 注入

先执行真实掉卡：

```bash
bash sre_agent/skills/builtin/gpu-drop-diagnosis/scripts/gpu_drop_recover.sh \
  drop --bdf 0000:01:00.0 --skip-verify
```

这一步会把 `0000:01:00.0` 从节点当前 GPU inventory 中移除。

## 实际诊断流程

### Step 1. 接收 live alert

demo 从 Prometheus / AlertChannel 读取到真实告警：

- `alert_name = GPUCardMissing`
- `gpu_index = 0`
- `gpu_uuid = GPU-1f575885-8de3-fd54-29a2-ddea916acc9c`
- `instance = 10.11.4.13:9100`

### Step 2. 发现并加载 skill

模型先调用：

1. `skills.list_skills`
2. `skills.load_skill(skill_id="builtin-gpu-drop-diagnosis")`

命中的就是：

- `builtin-gpu-drop-diagnosis`

### Step 3. 调用 skill 脚本

模型最终成功调用：

```text
skills.run_skill(
  skill_id="builtin-gpu-drop-diagnosis",
  script="gpu_drop_recover.sh",
  args=["doctor", "--node", "10.11.4.13", "--expected-gpu-count", "4", "--auto-recover"]
)
```

脚本在这一轮里做了两件关键事：

1. 先诊断当前状态
2. 发现少卡后自动执行 PCIe `rescan`

### Step 4. skill 脚本的真实诊断结果

脚本输出的 `before` 状态是：

- `nvidia-smi`：只有 `3` 张卡
- `pcie`：只有 `3` 个 GPU BDF
- 初始判断：
  - `state = pcie_missing`

也就是说，这一轮不是“PCIe 还在但 NVIDIA inventory 少卡”，而是：

- **GPU 0 对应的 PCIe 设备也已经不在当前 PCIe GPU inventory 里**

### Step 5. skill 脚本自动恢复

脚本自动执行：

- `rescan`

恢复后的 `after` 状态变成：

- `nvidia-smi`：`4` 张卡
- `pcie`：`4` 个 GPU BDF
- 最终判断：
  - `state = healthy`
- `remediation.recovered = true`

也就是说，这轮恢复动作已经在 skill 脚本里完成了，不需要额外再调别的修复脚本。

### Step 6. Agent 收敛最终 diagnosis

模型最后总结为：

- 根因：
  - `0000:01:00.0` 对应 GPU 曾从 PCIe bus 掉出
- 证据：
  - 初始只有 3 卡
  - `dmesg` 里有
    - `Attempting to remove device 0000:01:00.0 with non-zero usage count`
- 恢复结果：
  - 通过 PCIe `rescan` 已恢复
  - 当前 4 卡健康

## 这条 demo 验证到了什么

这次真实 run 验证了：

1. `GPUCardMissing` live alert 能被真实抓到
2. 模型会命中 `builtin-gpu-drop-diagnosis`
3. `gpu_drop_recover.sh` 能直接完成：
   - 少卡诊断
   - 恢复尝试
   - 结构化结果输出
4. Agent 能把 skill 输出收敛成最终 diagnosis

## 当前脚本能力边界

`gpu_drop_recover.sh` 现在能区分三类状态：

1. `pcie_missing`
   - PCIe 与 `nvidia-smi` 都少卡
2. `pcie_present_nvidia_missing`
   - PCIe 设备还在，但 NVIDIA 没把卡接回 `nvidia-smi`
3. `healthy`
   - inventory 已经恢复一致

这使得同一个 skill 既能处理：

- 真掉卡
- 半恢复态
- 已自恢复告警

## 一句话总结

这条 GPU drop demo 现在已经是完整闭环：

- `接收真实 GPUCardMissing 告警`
- `命中 gpu-drop-diagnosis`
- `skill 脚本完成诊断与恢复`
- `Agent 输出最终 diagnosis`
