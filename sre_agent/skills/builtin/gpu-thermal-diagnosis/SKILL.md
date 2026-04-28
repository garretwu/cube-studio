---
name: Gpu Thermal Diagnosis
description: >
  GPU温度异常专项诊断技能。当用户报告GPU温度过高、thermal throttling、风扇异常、
  散热系统故障、频繁降频、推理性能退化与温度关联等问题时触发。
  适用场景：GPUTemperatureHigh告警、thermal throttle、GPU降频、风扇转速异常、
  机房环境温度问题、机柜风道问题、BMC风扇控制策略异常。
tags:
  - gpu
  - thermal
  - temperature
  - fan
  - cooling
  - throttle
  - bmc
  - gputemperaturehigh
---

# GPU Thermal Diagnosis

## 环境配置

### wj-lab-cpt-04 环境详情

- **K8s节点名**: wj-lab-cpt-04
- **物理ip**: 10.11.4.13
- **SSH访问**: `ssh yuyonghao@10.11.4.13` (需sudo权限)
- **BMC访问**: `https://10.11.8.13` (用户: admin, 密码: Admin@9000, TLS验证: 关闭)
- **Prometheus**: `http://10.11.4.3:31260`

### wj-lab-cpt-02 环境详情

- **K8s节点名**: wj-lab-cpt-02
- **物理ip**: 10.11.4.11
- **SSH访问**: `ssh yuyonghao@10.11.4.11` (需sudo权限)
- **BMC访问**: `https://10.11.8.13` (用户: admin, 密码: Admin@9000, TLS验证: 关闭)
- **Prometheus**: `http://10.11.4.3:31260`

## 前置约束

本 skill 仅允许使用以下工具，禁止调用其他读工具如gpu.get_metrics，gpu.get_processes：

| 工具名称 | 用途说明 |
|----------|----------|
| `skills.list_skills` | 查询可用 skill 列表 |
| `skills.load_skill` | 加载 skill 详细内容 |
| `skills.read_skill_ref` | 读取 skill 参考文档（如 inventory.md） |
| `skills.run_skill` | 执行 skill 内置脚本 |
| `ssh.run_command` | 通过 SSH 执行远程命令。**必填参数**: `node`（节点名/IP）、`command`（shell命令）。示例: `{"node": "10.11.4.13", "command": "nvidia-smi --query-gpu=index,temperature.gpu,fan.speed --format=csv"}` |
| `bmc.get_fan_status` | 查询 BMC 风扇状态（Fan Mode、PWM、RPM）。**必填参数**: `node`（节点名）。示例: `{"node": "10.11.4.13"}` |

## 快速诊断决策树

```
温度异常分析
├─ 单卡高温 (> 10°C差异)
│     ├─ 风扇转速=0%或低速 → BMC风扇策略异常
│     ├─ 风扇正常 → 散热器/热耦合/物理位置
│     └─ 功耗异常 → 负载问题
├─ 全部GPU高温
│     ├─ 进风温度 > 27°C → 机房/空调问题
│     ├─ 风道堵塞 → 机柜盲板/障碍物
│     └─ 负载峰值 → 调度问题
└─ Thermal Throttling (温度 > 83°C)
      └─ 立即降载 → 根因下钻
```

## Phase 1: 快速信息采集

```bash
# 全部GPU状态对比
nvidia-smi --query-gpu=index,temperature.gpu,fan.speed,power.draw,power.limit,clocks.current.sm,pstate --format=csv

# 性能与节流原因
nvidia-smi -q -d PERFORMANCE

# BMC风扇状态（如果可用）
bmc.get_fan_status

```

## Phase 2: 根因诊断

### 2A: BMC风扇控制异常（重点）

风扇转速=0%、固定低速、或与温度不匹配时，优先检查BMC策略。

**BMC风扇控制速查表**:

| Fan Mode | 特征 | 风险 |
|----------|------|------|
| Full Speed | 100%转速 | 噪音大，应急用 |
| Optimal | 温度反馈调速 | 正常运维模式 |
| Manual PWM | 固定占空比 | **生产禁用！** |
| Disabled | 风扇停止 | **危险！立即恢复** |

**诊断流程**:
```
BMC风扇状态异常
  ├─ Step 1: 检查BMC风扇状态
  │     # 使用: bmc.get_fan_status
  │     # 关注: Fan Mode、PWM值、转速(RPM)
  │
  ├─ Step 2: 判断控制模式
  │     ├─ Mode = "Manual" 或 PWM固定值
  │     │     └─ 运维配置问题，非GPU硬件故障
  │     ├─ Mode = "Disabled"
  │     │     └─ 危险状态，立即恢复
  │     └─ Mode = "Optimal" 但转速异常
  │           └─ 可能是风扇硬件问题
  │
  ├─ Step 3: 恢复自动控制
  │     # 通过IPMI恢复
  │     ipmitool raw 0x30 0x30 0x01 0x01
  │     # 或通过BMC Web界面调整
  │
  ├─ Step 4: 验证风扇响应
  │     nvidia-smi dmon -s f -c 20 -d 1
  │     # 正常：温度上升时风扇转速同步上升
  │
  └─ Step 5: 风扇硬件故障确认
        ├─ 轴承异响/振动
        ├─ 转速低于预期（如标称3000RPM实际<1500RPM）
        └─ 转速持续下降 → 轴承磨损，18个月内更换
```

### 2B: 单卡散热问题

单卡温度比同节点其他卡高 > 10°C，风扇正常。

**诊断流程**:
```
单卡高温
  ├─ Step 1: 确认温度差异
  │     nvidia-smi --query-gpu=index,temperature.gpu,fan.speed --format=csv
  │
  ├─ Step 2: 分析热耦合
  │     # GPU布局与温度分布对照：
  │     # - 后端插槽(靠电源侧)通常温度最高
  │     # - 相邻GPU容易热耦合
  │     # - 靠墙位置可能风道死角
  │
  ├─ Step 3: 临时缓解
  │     nvidia-smi -pl 400 -i <gpu_index>  # 降低功耗
  │     nvidia-smi -lgc 300,1800 -i <gpu_index>  # 限制频率
  │
  └─ Step 4: 长期方案
        ├─ 物理检修（散热器贴合/导热膏）
        ├─ 调整GPU插槽位置
        └─ 增加GPU间距或导流板
```

### 2C: 环境/机柜级散热

多个节点或全部GPU同时高温。

**诊断流程**:
```
环境级高温
  ├─ Step 1: 检查进风温度
  │     # 通过 IPMI 查询
  │     ipmitool -I lanplus -H <bmc_host> -U admin -P Admin@9000 sensor get "Ambient Temp"
  │     # 或使用系统 sensors 命令
  │     ssh -o StrictHostKeyChecking=no yuyonghao@<ssh_host> 'sensors'
  │     # 理想: 18-25°C | 警告: >27°C | 危险: >32°C
  │
  ├─ Step 2: 检查机柜风道
  │     # 盲板是否完整
  │     # 机柜前后有无障碍物
  │     # 热空气是否回流
  │
  ├─ Step 3: 检查冷却设备
  │     # CRAC/CRAH运行状态
  │     # 冷却水温度
  │
  └─ Step 4: 缓解措施
        ├─ 临时降低机柜负载
        ├─ 迁移任务到其他节点
        └─ 增加临时散热设备
```

### 2D: Thermal Throttling

温度 > 83°C，触发GPU热保护降频。

**诊断流程**:
```
热节流检测
  ├─ Step 1: 确认节流状态
  │     nvidia-smi -q -d PERFORMANCE
  │     # 查找 "Thermal Slowdown"
  │
  ├─ Step 2: 分析降频幅度
  │     # RTX 5090典型: P0=2400MHz, P2=1800MHz, P3=1200MHz
  │     # 降频30% → 推理延迟增加约24%
  │
  ├─ Step 3: 紧急降温
  │     nvidia-smi -pl 400 -i <gpu_index>
  │     # 迁移推理任务
  │
  └─ Step 4: 根因分类
        ├─ 风扇问题 → Phase 2A
        ├─ 环境问题 → Phase 2C
        └─ 单卡问题 → Phase 2B
```

## Phase 3: 紧急操作

```bash
# 紧急降温（按优先级）
# 1. 降低功耗
nvidia-smi -pl 400 -i <gpu_index>

# 2. 限制频率
nvidia-smi -lgc 300,1800 -i <gpu_index>

# 3. 迁移/停止推理服务
systemctl stop vllm@<instance>

# 监控命令
watch -n 5 'nvidia-smi --query-gpu=index,temperature.gpu,fan.speed,pstate --format=csv'
```

## Phase 4: 预防性维护

| 维护项 | 频率 | 方法 | 阈值 |
|--------|------|------|------|
| 温度基线 | 每日 | nvidia-smi dmon | >75°C预警 |
| 风扇转速 | 每日 | 对比历史趋势 | 下降>10%预警 |
| 环境温度 | 实时 | BMC监控 | 进风>27°C |
| 风道检查 | 每周 | 物理检查 | 堵塞立即处理 |

## Phase 5: 根因分类与报告

| 根因类别 | 典型表现 | 修复方向 |
|----------|----------|----------|
| BMC风扇策略 | Manual PWM固定/Disabled | 恢复自动控制 |
| 风扇硬件 | 转速异常/异响 | 更换风扇模块 |
| 散热器 | 单卡高温 | 物理检修 |
| 环境温度 | 全部高温 | 机房空调调节 |
| 风道堵塞 | 局部高温 | 清理/加盲板 |
| 热耦合 | 相邻GPU同步高温 | 调整布局 |

```

