---
id: gpu-fault-sop
name: gpu-fault-sop
description: >
  AIDC GPU故障诊断与恢复的标准操作流程(SOP)，专为消费级GPU(RTX 5090/4090等)组建的
  推理/训练集群设计。当用户报告GPU相关故障、XID错误、GPU掉卡、显存错误、温度异常、
  功耗异常、PCIe错误、驱动崩溃、多GPU任务卡死、nvidia-smi异常、推理/训练任务中断、
  或任何GPU运维问题时，必须触发此skill。
  也适用于：GPU健康检查、GPU集群巡检、GPU故障预防性维护、GPU故障根因分析(RCA)。
  即使用户只是模糊地提到"卡挂了"、"任务跑不动"、"显存报错"、"推理变慢"等，也应触发。
  支持NVIDIA GeForce RTX 5090/4090及其他消费级GPU用于数据中心推理/微调场景。
tags:
  - gpu
  - xid
  - nvidia
  - pcie
  - thermal
  - memory
  - fault
  - nvidia-smi
---

# AIDC GPU故障诊断与恢复SOP

## Script Selection Guidance

本 skill 当前提供两个脚本，它们的用途不同，选择错误会误导诊断：

- `gpu_health_check.sh`
  - 默认首选诊断脚本。
  - 适用于：温度过高、XID 错误、掉卡、显存异常、PCIe 异常、`nvidia-smi` 异常、健康巡检。
  - 当告警是 health / thermal / fault / crash / throttle / XID / PCIe / memory 类问题时，应优先执行这个脚本。

- `gpu_benchmark.sh`
  - 仅用于性能基线采集或与历史基线做退化对比。
  - 适用于：用户明确要求做 benchmark、吞吐退化分析、与历史性能基线 compare。
  - 不适用于：温度告警、健康检查、硬件故障首轮排查。

强约束：

- 对于 `GPUTemperatureHigh`、thermal throttling、过热、风扇异常、XID、掉卡、PCIe/AER、显存错误这类告警，先执行 `gpu_health_check.sh`。
- 不要把 `gpu_benchmark.sh` 作为 thermal / health alert 的首轮动作，因为 benchmark 会主动增加 GPU 负载，可能让温度更高并放大故障。
- 只有在节点已经基本稳定、且需要验证“是否存在性能退化”时，才执行 `gpu_benchmark.sh`。

## 适用集群概况

本SOP针对以下典型部署环境设计，但原则适用于所有消费级GPU数据中心集群：

| 参数 | 规格 |
|------|------|
| 节点数 | 4 |
| 每节点GPU | 4 × NVIDIA RTX 5090 (GB202, Blackwell) |
| 总GPU数 | 16 |
| 单卡显存 | 32GB GDDR7, 512-bit, 1,792 GB/s |
| 互联 | 无NVLink，GPU间通信走PCIe 5.0 |
| ECC | 无硬件ECC（GDDR7内置CRC校验，非传统ECC） |
| TDP | 575W/卡 |
| 典型用途 | LLM推理服务、模型微调、小规模训练 |

### RTX 5090与数据中心GPU的关键差异

理解这些差异对正确诊断和处理故障至关重要：

**1. 无NVLink — 多GPU通信依赖PCIe**
RTX 5090不支持NVLink，多GPU通信（如NCCL all-reduce）通过PCIe总线或网络（跨节点时走RoCE/InfiniBand）。这意味着：
- PCIe带宽和稳定性对多GPU任务至关重要
- PCIe AER(Advanced Error Reporting)错误需重点关注
- 不存在NVSwitch/Fabric Manager相关故障
- 跨节点通信瓶颈更容易暴露网络问题

**2. 无硬件ECC — 内存错误处理不同**
RTX 5090使用GDDR7，内置CRC校验但无传统ECC。这意味着：
- nvidia-smi中ECC相关字段不可用或返回N/A
- 无法查询correctable/uncorrectable ECC计数
- 无page retirement机制
- 内存错误通常表现为CUDA计算结果异常、Xid 31/13错误
- 需要通过应用层校验（如推理结果一致性检查）来间接检测内存问题

**3. 散热压力大 — 消费级散热设计非7×24优化**
575W TDP + 风冷散热设计，在数据中心高密度部署下：
- 环境温度对GPU温度影响更直接
- 风扇轴承在持续满载下退化更快（寿命约1-2年）
- 温度波动导致频率动态调整（GPU Boost），影响推理延迟一致性
- 需要更积极的温度监控和功耗管理策略

**4. GSP固件问题 — Blackwell架构已知问题**
RTX 5090(GB202)在Linux下有已知的GSP(GPU System Processor)固件问题：
- XID 119: GSP heartbeat timeout — GSP固件无响应
- XID 109: CTX SWITCH TIMEOUT — 上下文切换超时，常由GSP timeout引发
- XID 8: 通道异常
- 这些问题可能在高负载下触发，需要PCI Secondary Bus Reset恢复

---

## Phase 0: 告警接收与分类

当收到GPU故障报告时，按以下分类确定优先级：

| 优先级 | 故障类型 | 典型表现 | 紧急度 |
|--------|---------|---------|--------|
| P0 | XID 79 (GPU掉卡) | nvidia-smi看不到GPU，lspci无设备 | 立即 |
| P0 | XID 119/109 (GSP超时) | GPU锁死，进程挂起不返回 | 立即 |
| P1 | XID 43/45 (Watchdog超时) | CUDA任务超时，GPU不响应 | 15分钟 |
| P1 | 温度 >83°C | 接近85°C降频阈值 | 15分钟 |
| P2 | 推理性能退化 >20% | token/s下降，延迟升高 | 1小时 |
| P2 | XID 31/13 (内存/通用错误) | CUDA错误，推理结果异常 | 1小时 |
| P3 | 驱动问题 | nvidia-smi返回错误、驱动加载失败 | 4小时 |
| P3 | PCIe带宽退化 | 多GPU任务变慢，单GPU正常 | 4小时 |

Agent收到故障报告后的标准动作：
1. 提取关键信息：节点IP/hostname、GPU index（0-3）、错误代码、时间戳
2. 匹配上表确定优先级
3. 进入对应的诊断Phase

---

## Phase 1: 快速信息采集

在任何诊断前，先采集节点完整状态快照。此阶段不做任何修复。

### 1.1 采集命令集

在目标节点执行（可使用 `scripts/gpu_health_check.sh` 自动化执行）：

```bash
# === 基础状态 ===

# 1. GPU识别与基本状态
nvidia-smi --query-gpu=index,name,serial,uuid,driver_version,temperature.gpu,power.draw,power.limit,memory.used,memory.total,pstate,clocks.current.sm,clocks.max.sm,fan.speed --format=csv

# 2. GPU数量确认（预期：4张）
nvidia-smi -L
lspci -d 10de: | grep -i "3D\|VGA\|Display"

# 3. XID错误（最近24h）
dmesg -T | grep -iE "xid|nvrm.*error|GSP.*heartbeat|CTX SWITCH" | tail -100

# 4. PCIe链路状态（关键！5090依赖PCIe通信）
lspci -d 10de: -vvv 2>/dev/null | grep -E "LnkSta:|LnkCap:|Width:|Speed:"

# 5. PCIe AER错误
dmesg -T | grep -iE "aer|pcie.*error|link.*down|correctable|uncorrectable"

# === 温度与功耗（消费卡重点关注） ===

# 6. 详细温度/功耗/时钟快照（5秒采样×5次）
nvidia-smi dmon -s pucvmet -c 5 -d 1

# 7. 风扇状态
nvidia-smi --query-gpu=index,fan.speed,temperature.gpu --format=csv

# === 多GPU通信（无NVLink，检查PCIe拓扑） ===

# 8. GPU拓扑（确认PCIe switch关系）
nvidia-smi topo -m

# 9. PCIe带宽测试（如果安装了cuda-samples）
# /usr/local/cuda/samples/1_Utilities/bandwidthTest/bandwidthTest

# === 系统级 ===

# 10. 驱动与内核
uname -r
cat /proc/driver/nvidia/version
nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1

# 11. GPU进程
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv

# 12. 系统资源
free -h
uptime
```

### 1.2 数据解读规则

Agent按以下决策树判断故障类型并路由到对应Phase：

```
采集数据分析
├─ nvidia-smi完全无法执行 (返回错误9)
│     └─ → Phase 3A (驱动修复)
├─ GPU数量 < 预期(4张)
│     ├─ lspci也看不到 → Phase 2A (XID 79 掉卡处理)
│     └─ lspci能看到但nvidia-smi看不到 → Phase 3A (驱动问题)
├─ dmesg含XID 119/109 (GSP超时)
│     └─ → Phase 2B (GSP超时处理，5090特有)
├─ dmesg含XID 43/45 (Watchdog超时)
│     └─ → Phase 2C (GPU Watchdog处理)
├─ dmesg含XID 31/13 (内存/通用错误)
│     └─ → Phase 2D (CUDA错误处理)
├─ 温度 > 83°C
│     └─ → Phase 2E (温度处理)
├─ 时钟频率 < 预期的70%
│     └─ → Phase 2F (性能退化)
├─ PCIe LnkSta降速 (如 x16→x8, Gen5→Gen4)
│     └─ → Phase 2G (PCIe问题)
├─ 多GPU任务卡死，单GPU正常
│     └─ → Phase 3B (多GPU通信排查)
└─ 以上均无异常，但任务失败
      └─ → Phase 3C (应用层排查)
```

---

## Phase 2: 硬件故障诊断与恢复

### 2A: GPU掉卡 (XID 79 — "GPU has fallen off the bus")

RTX 5090的575W TDP + PCIe 5.0信号速率使其对PCIe连接质量非常敏感。
社区已有RTX 5090在负载下触发XID 79的报告。

**诊断流程**:
```
GPU从PCIe总线消失
  ├─ Step 1: 确认状态
  │     lspci -d 10de: | wc -l  # 预期4，少于4则有掉卡
  │     nvidia-smi -L            # 确认哪张消失
  │
  ├─ Step 2: 检查PCIe AER错误
  │     dmesg -T | grep -iE "aer|pcie.*error|link.*down"
  │     # 看是否有Unsupported Request / Completion Timeout
  │
  ├─ Step 3: 尝试PCIe Secondary Bus Reset
  │     # 找到消失GPU的parent bridge
  │     # 注意：需要确认BDF地址
  │     echo 1 > /sys/bus/pci/devices/<parent_bridge_BDF>/reset
  │     sleep 3
  │     echo 1 > /sys/bus/pci/rescan
  │     # 检查GPU是否回来
  │     lspci -d 10de:
  │
  ├─ Step 4: GPU回来了
  │     ├─ nvidia-smi确认可访问
  │     ├─ 运行简单CUDA测试验证
  │     │     python3 -c "import torch; t=torch.randn(1000,1000).cuda(<idx>); print(t.sum())"
  │     ├─ 通过 → 标记"观察中"，监控24h
  │     └─ 仍报错 → Step 5
  │
  └─ Step 5: GPU无法恢复
        ├─ 需要物理power cycle（冷重启节点）
        ├─ 重启后仍不识别 → 物理问题
        │     ├─ 检查PCIe插槽/金手指/辅助供电
        │     └─ 尝试将GPU换到其他PCIe插槽
        └─ 确认GPU硬件死亡 → Phase 4 (RMA)
```

**Agent自动化动作**:
1. 立即将该节点从推理调度池移除
2. 评估剩余3张GPU是否可继续承载推理负载
3. 通知负载均衡器将流量转移到其他节点
4. 创建工单记录GPU序列号、BDF地址、PCIe AER日志

### 2B: GSP Heartbeat Timeout (XID 119/109) — RTX 5090特有高频问题

RTX 5090(GB202 Blackwell)有已知的GSP固件稳定性问题。在高负载（推理或训练）时，
GSP固件可能停止心跳，导致级联故障：XID 119 → XID 109 → XID 8 → GPU锁死。

**诊断流程**:
```
dmesg显示GSP heartbeat timeout 或 CTX SWITCH TIMEOUT
  ├─ Step 1: 确认错误模式
  │     dmesg -T | grep -iE "GSP.*heartbeat|CTX SWITCH|Xid.*119|Xid.*109|Xid.*8"
  │     # 典型级联: heartbeat timeout → Xid 109 → Xid 8
  │
  ├─ Step 2: 检查GPU是否还在bus上
  │     nvidia-smi  # 可能挂起(hang)
  │     timeout 10 nvidia-smi  # 加超时保护
  │
  ├─ Step 3: 如果nvidia-smi挂起 → GPU处于"dirty state"
  │     # 需要PCI Secondary Bus Reset（不是nvidia-smi -r）
  │     # 找到GPU的BDF
  │     lspci -d 10de: -D
  │     # 执行reset
  │     echo 1 > /sys/bus/pci/devices/<BDF>/reset
  │     sleep 5
  │     nvidia-smi  # 验证恢复
  │
  ├─ Step 4: 恢复后的稳定性检查
  │     ├─ 检查驱动版本是否为最新
  │     │     cat /proc/driver/nvidia/version
  │     │     # RTX 5090建议使用 >= 570.x 系列驱动
  │     ├─ 检查GSP固件模式
  │     │     dmesg | grep -i "GSP\|firmware"
  │     │     # 可尝试禁用GSP: options nvidia NVreg_EnableGpuFirmware=0
  │     └─ 如果频繁复发(>3次/天)
  │           ├─ 尝试固定GPU时钟避免动态调频触发
  │           │     nvidia-smi -lgc <min>,<max> -i <gpu_idx>
  │           ├─ 降低功耗限制减少热应力
  │           │     nvidia-smi -pl 400 -i <gpu_idx>  # 从575W降到400W
  │           └─ 更新到最新驱动版本
  │
  └─ Step 5: 持续不稳定
        └─ 创建工单，可能需要RMA或固件更新
```

### 2C: GPU Watchdog Timeout (XID 43/45)

GPU上提交的工作超时未完成。在推理场景中，可能是模型加载卡死或某请求触发了异常。

**诊断流程**:
```
XID 43/45 detected
  ├─ Step 1: 确认触发时的工作负载
  │     # 是否特定推理请求触发？还是随机发生？
  │     nvidia-smi --query-compute-apps=pid,name,used_memory --format=csv
  │
  ├─ Step 2: 尝试GPU reset
  │     # 先终止GPU上所有进程
  │     nvidia-smi --query-compute-apps=pid --format=csv,noheader -i <idx> | xargs -r kill -9
  │     sleep 2
  │     nvidia-smi -r -i <gpu_index>
  │
  ├─ Step 3: Reset后验证
  │     nvidia-smi -i <gpu_index>
  │     python3 -c "import torch; x=torch.randn(100,100).cuda(<idx>); print(x.mm(x).sum())"
  │
  ├─ Step 4: 判断根因
  │     ├─ 与特定模型/请求相关 → 应用层问题
  │     ├─ 随机发生 + 温度高 → 热降频导致超时 → Phase 2E
  │     ├─ 随机发生 + 伴随PCIe错误 → PCIe问题 → Phase 2G
  │     └─ 频繁发生 → 可能是硬件退化
  │
  └─ Step 5: 如果reset失败
        └─ 使用PCI bus reset (同Phase 2B Step 3)
```

### 2D: CUDA错误 (XID 31/13)

由于RTX 5090无ECC，内存bit flip不会被静默纠正，可能直接导致CUDA错误。

**诊断流程**:
```
XID 31 (内存page fault) 或 XID 13 (通用GPU错误)
  ├─ Step 1: 区分应用Bug还是硬件问题
  │     ├─ 同一GPU上所有任务都出错 → 可能是硬件
  │     ├─ 仅特定任务出错 → 可能是应用代码
  │     └─ 某GPU上的任务迁移到其他GPU后正常 → 确认是该GPU问题
  │
  ├─ Step 2: 针对可疑GPU运行诊断
  │     # 由于没有ECC和DCGM Level3支持，使用替代方法：
  │     # (a) CUDA memtest
  │     cuda_memcheck --tool memcheck python3 -c "
  │         import torch
  │         x = torch.randn(8192, 8192, device='cuda:<idx>')
  │         for i in range(100):
  │             x = x @ x.t()
  │             x = x / x.norm()
  │         print('PASS')
  │     "
  │     # (b) GPU压力测试（Blackwell tensor core）
  │     python3 -c "
  │         import torch, time
  │         dev = torch.device('cuda:<idx>')
  │         a = torch.randn(4096, 4096, dtype=torch.float16, device=dev)
  │         start = time.time()
  │         for i in range(1000):
  │             c = torch.mm(a, a)
  │         torch.cuda.synchronize()
  │         elapsed = time.time() - start
  │         tflops = (2 * 4096**3 * 1000) / elapsed / 1e12
  │         print(f'TFLOPS: {tflops:.1f}  (elapsed: {elapsed:.1f}s)')
  │     "
  │
  ├─ Step 3: 对比同节点其他GPU的性能
  │     # 同一测试在4张GPU上分别运行，对比TFLOPS
  │     # 偏差 > 10% → 该GPU可能有问题
  │
  └─ Step 4: 确认硬件问题
        ├─ 重启GPU后仍频繁出错 → 显存可能有坏块
        ├─ 冷重启后恢复 → 可能是热相关问题
        └─ 持续异常 → Phase 4 (RMA)
```

### 2E: 温度异常

RTX 5090的575W TDP在密集部署下温度管理是核心挑战。

**诊断流程**:
```
GPU温度 > 83°C 或 频繁降频
  ├─ Step 1: 采集温度分布
  │     # 全部4张GPU温度对比
  │     nvidia-smi --query-gpu=index,temperature.gpu,fan.speed,power.draw,clocks.current.sm --format=csv
  │     # 持续监控（每2秒采集60次）
  │     nvidia-smi dmon -s pt -d 2 -c 60
  │
  ├─ Step 2: 判断是单卡还是全局问题
  │     ├─ 单卡高温，其他正常
  │     │     ├─ 检查风扇转速: fan.speed = 0% → 风扇故障
  │     │     ├─ 风扇正常但温度高 → 散热器/导热垫问题
  │     │     └─ 检查是否有GPU进程异常占用
  │     ├─ 全部GPU高温
  │     │     ├─ 检查机房环境温度（进风口温度）
  │     │     ├─ 检查机柜风道是否堵塞
  │     │     └─ 检查相邻节点是否也高温（排温联动）
  │     └─ 靠近的两张卡高温（如GPU 1和GPU 2）
  │           └─ 可能是物理位置导致的热耦合
  │
  ├─ Step 3: 临时缓解
  │     # 降低功耗限制
  │     nvidia-smi -pl 400 -i <gpu_index>   # 从575W降到400W
  │     # 或限制最大频率
  │     nvidia-smi -lgc 300,1800 -i <gpu_index>
  │     # 重新分配负载：将该GPU的推理任务临时迁移
  │
  └─ Step 4: 长期解决
        ├─ 优化机柜风道设计
        ├─ 考虑GPU间留空槽位增加散热间距
        ├─ 监控风扇转速趋势（下降10%表明轴承磨损）
        └─ 在负载调度中加入温度感知
```

### 2F: 性能退化

推理延迟升高或吞吐下降，但GPU看起来"正常"。

**诊断流程**:
```
性能退化但无明显错误
  ├─ Step 1: 检查GPU时钟
  │     nvidia-smi -q -d CLOCK -i <gpu_index>
  │     # 对比当前SM时钟与最大时钟
  │     # 如果持续在低P-state（P2/P3而非P0）→ 可能是热降频
  │
  ├─ Step 2: 检查power throttle原因
  │     nvidia-smi -q -d PERFORMANCE -i <gpu_index>
  │     # 关注 "SW Thermal Slowdown" / "HW Thermal Slowdown"
  │     # 关注 "SW Power Cap"
  │
  ├─ Step 3: 检查PCIe带宽（多GPU场景）
  │     nvidia-smi topo -m
  │     # 如果GPU间显示PHB/SYS而非PIX/PXB → PCIe拓扑不理想
  │     # 确认PCIe链路未降速
  │     lspci -d 10de: -vvv | grep LnkSta
  │     # 期望: Speed 32GT/s (Gen5), Width x16
  │
  ├─ Step 4: 基线对比
  │     # 运行标准benchmark与初始基线对比
  │     # 参考scripts/gpu_benchmark.sh
  │
  └─ Step 5: 根因定位
        ├─ 热降频 → Phase 2E
        ├─ PCIe降速 → Phase 2G
        ├─ 无降频降速但性能低 → 可能是GPU硬件退化
        └─ 所有GPU都慢 → 检查系统层面(CPU/内存/存储瓶颈)
```

### 2G: PCIe问题

RTX 5090使用PCIe 5.0 x16，在数据中心环境下PCIe信号完整性问题更值得关注。

**诊断流程**:
```
PCIe相关异常
  ├─ Step 1: 检查链路状态
  │     lspci -d 10de: -vvv | grep -E "LnkCap|LnkSta"
  │     # 正常: LnkSta: Speed 32GT/s, Width x16
  │     # 异常: Speed降到16GT/s(Gen4) 或 Width降到x8
  │
  ├─ Step 2: 检查AER错误计数
  │     # 方法1: dmesg
  │     dmesg -T | grep -i aer
  │     # 方法2: sysfs
  │     cat /sys/bus/pci/devices/<BDF>/aer_dev_correctable
  │     cat /sys/bus/pci/devices/<BDF>/aer_dev_fatal
  │
  ├─ Step 3: 如果链路降速
  │     ├─ 尝试PCIe链路retrain
  │     │     # 通过setpci强制retrain (需要root)
  │     │     setpci -s <BDF> CAP_EXP+10.w=0020:0020
  │     ├─ Retrain后恢复 → 记录，监控
  │     └─ 持续降速 → 可能是线缆/riser/插槽/主板问题
  │
  └─ Step 4: AER错误持续
        ├─ Correctable AER → 不紧急，但需监控增长趋势
        ├─ Uncorrectable non-fatal → 安排维护窗口检查
        └─ Fatal AER → 立即隔离，物理检查
```

---

## Phase 3: 软件层故障排查

### 3A: 驱动问题

RTX 5090需要较新的驱动版本（建议 >= 570.x），且Blackwell架构对open-gpu-kernel-modules
支持仍在完善中。

```
nvidia-smi失败或异常
  ├─ Step 1: 确认驱动状态
  │     lsmod | grep nvidia
  │     cat /proc/driver/nvidia/version
  │     dmesg | grep -i "nvrm\|nvidia" | tail -20
  │
  ├─ Step 2: 驱动加载但nvidia-smi失败
  │     ├─ 检查是否是GSP相关: dmesg | grep -i gsp
  │     ├─ 尝试重载驱动:
  │     │     # 确保无GPU进程
  │     │     fuser -v /dev/nvidia* 2>/dev/null
  │     │     lsof /dev/nvidia* 2>/dev/null
  │     │     # 停止相关服务
  │     │     systemctl stop <inference_service>
  │     │     # 卸载+重载
  │     │     rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia
  │     │     modprobe nvidia
  │     │     nvidia-smi
  │     └─ 如果是open kernel module相关问题
  │           # RTX 5090建议使用open kernel modules但可能有bug
  │           # 可尝试切换到闭源驱动对比
  │
  ├─ Step 3: 驱动完全无法加载
  │     ├─ 检查内核版本兼容性: uname -r
  │     ├─ 检查Secure Boot: mokutil --sb-state
  │     ├─ 检查DKMS构建日志: dkms status
  │     └─ 重新安装驱动
  │
  └─ Step 4: 驱动版本过旧
        ├─ RTX 5090 (GB202) 最低要求驱动版本 570.x
        ├─ 已知XID 13 "Illegal Instruction Encoding"问题
        │     出现在nvidia-driver-580-open上运行vLLM时
        └─ 建议关注NVIDIA发布的Blackwell专项修复
```

### 3B: 多GPU通信排查

RTX 5090无NVLink，多GPU通信走PCIe（节点内）和网络（跨节点）。

```
多GPU任务卡死或报错，单GPU任务正常
  ├─ Step 1: 确认GPU拓扑
  │     nvidia-smi topo -m
  │     # 了解GPU之间经过哪些PCIe switch
  │     # 理想: GPU对走同一PCIe switch (PIX/PXB)
  │     # 不理想: 跨NUMA节点 (SYS)
  │
  ├─ Step 2: NCCL通信测试（节点内4卡）
  │     # 使用nccl-tests
  │     NCCL_DEBUG=INFO \
  │     NCCL_P2P_LEVEL=PXB \
  │     mpirun -np 4 ./all_reduce_perf -b 1M -e 1G -g 1
  │     # 关注：带宽是否正常(PCIe 5.0 x16理论~64GB/s)
  │     # 关注：是否有NCCL WARN或超时
  │
  ├─ Step 3: 跨节点通信测试
  │     # 确认网络连通性
  │     # 检查InfiniBand/RoCE状态
  │     ibstat 2>/dev/null || echo "No IB detected"
  │     # 跨节点NCCL测试
  │     NCCL_DEBUG=INFO \
  │     NCCL_IB_DISABLE=0 \
  │     mpirun -np 8 --host node1:4,node2:4 ./all_reduce_perf -b 1M -e 1G -g 1
  │
  ├─ Step 4: PCIe P2P通信问题
  │     # RTX 5090可能不支持或部分支持PCIe P2P
  │     # 如果NCCL报P2P失败，尝试禁用
  │     NCCL_P2P_DISABLE=1 <command>
  │     # 或指定通过共享内存通信
  │     NCCL_SHM_DISABLE=0 <command>
  │
  └─ Step 5: IOMMU/ACS问题
        # 检查IOMMU是否影响P2P
        dmesg | grep -i iommu
        # 检查ACS
        lspci -vvv | grep -i "Access Control Services"
        # 如果ACS开启可能阻止P2P → 在BIOS中调整
```

### 3C: 应用层排查

```
GPU健康但推理/训练任务失败
  ├─ Step 1: GPU基本验证
  │     python3 -c "
  │         import torch
  │         for i in range(torch.cuda.device_count()):
  │             print(f'GPU {i}: {torch.cuda.get_device_name(i)}, '
  │                   f'{torch.cuda.get_device_properties(i).total_mem/1e9:.1f}GB')
  │     "
  │
  ├─ Step 2: CUDA/PyTorch版本检查
  │     python3 -c "import torch; print(f'PyTorch: {torch.__version__}, CUDA: {torch.version.cuda}')"
  │     nvcc --version
  │     # RTX 5090 (Blackwell/SM100) 需要 CUDA 12.8+
  │
  ├─ Step 3: 推理框架特定检查（vLLM/SGLang/TensorRT-LLM）
  │     # vLLM: 检查是否支持RTX 5090的FP4/FP8
  │     # SGLang: 检查FlashInfer backend兼容性
  │     # TRT-LLM: 需要Blackwell支持的版本
  │
  ├─ Step 4: OOM问题
  │     # RTX 5090只有32GB，大模型需要仔细管理
  │     nvidia-smi --query-gpu=memory.used,memory.total --format=csv
  │     # 检查KV cache配置是否过大
  │
  └─ Step 5: 推理结果异常(无ECC保护的潜在影响)
        # 对比不同GPU上相同输入的推理结果
        # 如果特定GPU持续产生异常结果
        # → 可能是GDDR7内存bit rot（无ECC无法自动纠正）
        # → Phase 2D 进一步诊断
```

---

## Phase 4: 硬件更换与恢复

1. **隔离确认**: 节点已从调度池移除
2. **工单创建**:
   - GPU序列号: `nvidia-smi --query-gpu=serial --format=csv`
   - GPU UUID: `nvidia-smi --query-gpu=uuid --format=csv`
   - PCIe BDF地址: `lspci -d 10de: -D`
   - 故障现象、XID代码、诊断日志
   - 节点位置（机房/机架/U位/PCIe槽位）
3. **替换后验证**:
   ```bash
   # 新GPU安装后
   nvidia-smi                        # 基本识别
   nvidia-smi -q                     # 详细状态
   lspci -d 10de: -vvv | grep LnkSta # PCIe链路确认Gen5 x16

   # GPU功能验证
   python3 -c "
       import torch
       dev = torch.device('cuda:<new_idx>')
       # 基础计算
       x = torch.randn(4096, 4096, device=dev)
       y = x @ x.t()
       print(f'Basic compute: OK ({y.shape})')
       # FP16 tensor core
       a = x.half()
       b = (a @ a).float()
       print(f'FP16 tensor core: OK ({b.mean():.4f})')
       # 显存压力
       big = torch.randn(8192, 8192, device=dev)
       print(f'Memory stress: OK ({big.element_size() * big.nelement() / 1e9:.1f}GB used)')
   "

   # 多GPU验证（4卡all-reduce）
   # 如有nccl-tests：
   mpirun -np 4 ./all_reduce_perf -b 1M -e 256M -g 1
   ```
4. **恢复服务**: 验证通过后将节点加入调度池

---

## Phase 5: 事后分析与预防

### RCA报告模板

```
故障ID: [自动生成]
集群: [集群名称]
节点: [hostname / IP]
GPU: [index / serial / UUID]
时间: [故障发生] - [恢复完成]
影响: [受影响推理请求数 / 用户数 / SLA影响]
XID: [错误码，如果有]
根因分类: [硬件-GPU / 硬件-PCIe / 硬件-散热 / 软件-驱动 / 软件-应用 / 环境]
故障现象: [简述]
处理过程: [各步骤及耗时]
自动化处理比例: [自动/半自动/人工]
改进建议: [具体建议]
```

### 消费级GPU集群的预防性维护重点

| 维护项 | 频率 | 方法 |
|--------|------|------|
| GPU温度基线采集 | 每日 | nvidia-smi dmon自动采集，对比历史趋势 |
| 风扇转速监控 | 每日 | 转速下降10%预警轴承磨损(30天更换窗口) |
| PCIe链路状态检查 | 每周 | lspci检查链路速率/宽度有无退化 |
| CUDA计算一致性 | 每周 | 相同输入在不同GPU上对比输出(无ECC替代方案) |
| 推理性能基线对比 | 每周 | 标准benchmark vs 初始基线，>10%退化告警 |
| 物理检查(灰尘/线缆) | 每月 | 清理灰尘，检查PCIe/供电连接 |
| 驱动版本审查 | 每月 | 关注NVIDIA发布的Blackwell修复 |

---

## 附录: RTX 5090关键XID速查表

注意：由于RTX 5090是消费级Blackwell(GB202)，某些XID可能与数据中心GPU表现不同。
SXid(NVSwitch相关)不适用于RTX 5090。

| XID | 含义 | 严重性 | RTX 5090注意事项 | 自动化动作 |
|-----|------|--------|-----------------|-----------|
| 8 | GPU channel异常 | Warning | 常伴随XID 119/109级联出现 | 记录，检查是否有GSP超时 |
| 13 | General GPU error | Warning | 已知驱动兼容性问题(vLLM+580-open) | 记录，检查驱动版本 |
| 31 | Memory page fault | Warning | 无ECC，可能是显存问题而非代码bug | 跨GPU对比验证 |
| 43 | Watchdog timeout | Critical | 高温降频时更易触发 | GPU reset，不恢复则bus reset |
| 45 | Preemptive cleanup | Critical | 伴随XID 43 | 同XID 43 |
| 62 | Thermal violation | Critical | 575W TDP，消费散热器易触发 | 降功耗至400W |
| 79 | GPU掉卡 | Fatal | PCIe 5.0信号敏感，已有5090报告 | 隔离+bus reset+power cycle |
| 109 | CTX SWITCH TIMEOUT | Critical | **5090高频问题**，GSP固件bug | PCI bus reset |
| 119 | GSP heartbeat timeout | Critical | **5090高频问题**，Blackwell固件 | PCI bus reset，考虑禁用GSP |
