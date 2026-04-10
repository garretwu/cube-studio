---
id: builtin-gpu-thermal-diagnosis
name: GPU Thermal Diagnosis
description: Diagnose GPU overheat, thermal throttling, fan-control anomalies, and cooling-path issues after the standard GPU fault SOP has collected the basic snapshot.
tags:
  - gpu
  - thermal
  - temperature
  - fan
  - cooling
  - throttle
  - bmc
---

# GPU Thermal Diagnosis

## When To Use

- `GPUTemperatureHigh`、thermal throttling、持续高温、频繁降频。
- 已经确认是温度/散热方向，需要从标准 `gpu-fault-sop` 继续下钻。
- 明显涉及风扇模式、固定 PWM、机柜风道、热耦合、环境温度等热管理问题。

## Relationship To SOP

- `gpu-fault-sop` 是标准流程，负责统一的告警接收、基础采样和总入口判断。
- 本 skill 是温度异常专项诊断，适合在基础快照拿到之后继续收敛热相关根因。

## Recommended Evidence

- `gpu.get_metrics`
- `gpu.get_processes`
- `bmc.get_fan_status`
- `skills.load_skill(skill_id="gpu-fault-sop")`，用于补充标准流程上下文

## Thermal Triage Heuristics

### 单卡高温，其他卡正常

- 先看 `fan.speed`、`power.draw`、`clocks.current.sm` 是否同步异常。
- 如果可用 `bmc.get_fan_status`，检查风扇控制模式和 PWM 是否异常固定。
- 若风扇被人为锁到 `Manual` 且 PWM 固定，应把“风扇控制策略异常”视为强根因候选。
- 若风扇转速正常但温度持续偏高，则继续怀疑散热器贴合、导热材料老化、局部风道阻塞。
- 同时排查是否存在异常 GPU 进程或长期满载压力。

### 全部 GPU 都高温

- 优先怀疑环境温度、机柜进风、风道堵塞或整机负载过高。
- 对比相邻节点是否同步升温，避免把机房级问题误判为单机故障。
- 检查是否存在统一的功耗上升、频率下降和热节流迹象。

### 相邻两张或同一侧 GPU 高温

- 把物理布局、热耦合和气流死角作为重点候选。
- 结合机箱结构判断是否需要临时迁移负载或调整插槽布局。

## Fast Mitigation

- 先降低压力，再做根因确认，避免 benchmark 或额外压测放大热问题。
- 必要时临时下调功耗上限或限制时钟。
- 对推理业务优先做负载迁移或摘除异常 GPU。

## Output Expectations

- 明确区分“负载导致的正常升温”与“散热控制策略异常”。
- 输出单卡、整机、环境三级候选根因。
- 如涉及 BMC 风扇策略，单独标注为运维配置问题，而不是直接归因到 GPU 硬件损坏。
