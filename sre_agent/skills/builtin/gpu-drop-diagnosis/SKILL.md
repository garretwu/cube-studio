---
id: builtin-gpu-drop-diagnosis
name: gpu-drop-diagnosis
description: >
  Diagnose GPU fallen-off-bus and missing-GPU incidents, including the partial
  recovery case where a PCIe device is still present but NVIDIA has not brought
  it back into nvidia-smi inventory. Use the bundled helper script to diagnose
  and, when possible, recover the card in one run.
tags:
  - gpu
  - pcie
  - fallen-off-bus
  - xid79
  - drop
  - recover
  - rescan
  - bdf
  - missing
  - inventory
  - gpucardmissing
---

# GPU 掉卡诊断与恢复

这个 skill 用于处理 GPU 掉卡、`nvidia-smi` 少卡、XID 79、PCIe 设备消失等场景。

默认只需要运行一次脚本：

```bash
bash scripts/gpu_drop_recover.sh
```

脚本会自动进入 `doctor` 模式，完成：

1. 采集 `nvidia-smi`、PCIe、sysfs 三份 GPU inventory
2. 判断是：
   - 真正少卡
   - 还是 “PCIe 设备还在，但 NVIDIA 没把它重新纳入 `nvidia-smi`”
3. 在允许时自动尝试：
   - PCIe rescan
   - 暂停 GPU 监控客户端
   - `nvidia-smi --gpu-reset`
4. 输出结构化 JSON 诊断结果和恢复结果

## When To Use

- `nvidia-smi -L` 看到 GPU 数量少于预期
- `dmesg` 出现 `XID 79` 或 "fallen off the bus"
- `lspci` 中 GPU PCIe 设备消失
- 需要受控模拟 GPU 掉卡 / PCIe rescan 恢复流程

## Recommended Flow

1. 优先直接运行 `bash scripts/gpu_drop_recover.sh`，让脚本自己判断当前状态。
2. 在 live alert 场景下，优先让这个 skill 先完成一次 `doctor`，不要先手动拆成很多底层 tool 调查。
3. 重点区分三类状态：
   - `pcie_missing`
   - `pcie_present_nvidia_missing`
   - `healthy`
4. 如果结果显示 `pcie_present_nvidia_missing`，说明 PCIe 设备还在，但 NVIDIA inventory 里已经少卡。
5. 如果结果显示 `pcie_missing`，说明这轮是真掉卡，PCIe 侧和 `nvidia-smi` 都少卡。
6. 如果结果显示 `healthy`，说明 inventory 已恢复一致；这时要把告警解释成“已自恢复”或“经脚本恢复成功”，而不是继续假设仍在故障中。
7. 如果需要受控注入掉卡，运行 `bash scripts/gpu_drop_recover.sh drop --gpu-index <N>` 或 `--bdf <BDF>`。
8. 如果需要单独恢复，运行 `bash scripts/gpu_drop_recover.sh recover`。
9. 若脚本自动恢复后仍未健康，按脚本输出建议继续做 driver reload 或 reboot。

## Runtime Context

在 live demo 或 server 里，优先通过 `SRE_` 环境变量给脚本传上下文：

- `SRE_GPU_DROP_NODE`
- `SRE_GPU_DROP_HOST`
- `SRE_GPU_DROP_SSH_USER`
- `SRE_GPU_DROP_SSH_PASSWORD`
- `SRE_GPU_DROP_SSH_PORT`
- `SRE_GPU_DROP_EXPECTED_GPU_COUNT`

这样 `skills.run_skill(skill_id="builtin-gpu-drop-diagnosis", script="gpu_drop_recover.sh")`
即使不带额外参数，也能直接完成诊断与恢复尝试。

## Live Alert Guidance

- 处理 `GPUCardMissing`、`GPUCardCountDropped` 这类 live alert 时，优先先用这个 skill。
- 如果 runtime 已经提供了 SSH 上下文和 `SRE_GPU_DROP_*` 环境变量，默认直接运行：

```bash
bash scripts/gpu_drop_recover.sh
```

- 不需要先自己拼很多调查步骤；先看脚本给出的 `initial_diagnosis` / `final_diagnosis` / `actions` / `remediation`。
- 最终总结时，明确回答：
  - 当前是 `pcie_missing`、`pcie_present_nvidia_missing`，还是 `healthy`
  - 是否已经恢复成功
  - 如果没恢复，下一步是 `driver reload` 还是 `reboot`
- 如果脚本已经把 inventory 恢复成健康状态，应把 skill 的恢复动作视为本轮 remediation 的核心证据。

## Safety Notes

- 执行 `drop` 前，优先确认目标 GPU 上没有关键训练/推理负载。
- `recover` 通过 PCIe `rescan` 尝试恢复，不保证所有驱动/平台都能无损恢复。
- 若 `rescan` 后设备仍未回来，可能需要重载驱动或重启节点。
- `doctor` 模式里的 `gpu-reset` 需要暂时停掉 `dcgm-exporter`、`nvidia-device-plugin` 等 GPU 客户端。
- 这类动作会直接影响硬件设备可见性，应只在明确授权的故障演练或修复窗口中使用。
