---
id: builtin-gpu-thermal-diagnosis
name: GPU Thermal Diagnosis
description: Diagnose GPU overheat, thermal throttling, fan-control anomalies, and cooling-path issues.
tags:
  - gpu
  - thermal
  - temperature
  - fan
  - cooling
  - throttle
  - bmc
  - alert 
  - high 
recommended_tools:
  - tool: gpu.get_metrics
    params:
      node: ${host_ip}
  - tool: gpu.get_processes
    params:
      node: ${host_ip}
  - tool: bmc.get_fan_status
    params:
      node: ${host_ip}
---

# GPU Thermal Diagnosis

## When To Use

- `GPUTemperatureHigh`、thermal throttling、持续高温、频繁降频。
- 明显涉及风扇模式、固定 PWM、机柜风道、热耦合、环境温度等热管理问题。

## Alert Labels → Variables

告警的 `labels` 字段包含关键信息，系统会自动将其注入为变量供工具调用使用：

### 关键 Label 字段

| Alert Label | 变量名 | 示例值 | 用途 |
|-------------|--------|--------|------|
| `host_ip` | `${host_ip}` | `10.11.4.13` | **SSH host IP**，用于 GPU/BMC 工具的 `node` 参数 |
| `Hostname` | `${labels.Hostname}` | `wj-lab-cpt-04` | K8s node name，用于 `k8s.get_node_ip` |
| `instance` | `${labels.instance}` | `10.0.12.191:9400` | Prometheus target，含 IP 和端口 |
| `gpu` | `${labels.gpu}` | `0`, `2` | GPU index |
| `UUID` | `${labels.UUID}` | `GPU-2656ec14-...` | GPU UUID |

### host_ip 获取规则

1. **优先**：`labels.host_ip`（如果告警直接包含）
2. **备选**：从 `labels.instance` 提取（去掉端口部分）
   - `instance=10.0.12.191:9400` → host_ip = `10.0.12.191`
3. **兜底**：`labels.Hostname`（需通过 `k8s.get_node_ip` 转换为 IP）

## Tool Parameter Reference

### 变量插值语法

工具参数支持 `${variable}` 语法，系统会自动替换为实际值：

```yaml
params:
  node: ${host_ip}       # 替换为 "10.11.4.13"
  gpu_uuid: ${labels.UUID}  # 替换为 "GPU-2656ec14-..."
```

### GPU 工具

| 工具 | 必需参数 | 参数来源 | 示例调用 |
|------|---------|---------|---------|
| `gpu.get_metrics` | `node` | `${host_ip}` | `gpu.get_metrics(node=10.11.4.13)` |
| `gpu.get_processes` | `node` | `${host_ip}` | `gpu.get_processes(node=10.11.4.13)` |

**返回格式**（nvidia-smi CSV）：
```
index, name, utilization.gpu, memory.used, memory.total, temperature.gpu
0, NVIDIA GeForce RTX 5090, 100, 31809, 32607, 56
```

### BMC 工具

**重要**：BMC 工具使用 `node` 参数（SSH host IP），系统会自动映射到 BMC IP。

| 工具 | 必需参数 | 参数来源 | 示例调用 |
|------|---------|---------|---------|
| `bmc.get_fan_status` | `node` | `${host_ip}` | `bmc.get_fan_status(node=10.11.4.13)` |
| `bmc.get_thermal` | `node` | `${host_ip}` | `bmc.get_thermal(node=10.11.4.13)` |

**IP 映射逻辑**（在 inventory 中配置）：
```
SSH host: 10.11.4.13  → BMC IP: 10.11.8.13
SSH host: 10.11.4.14  → BMC IP: 10.11.8.14
```

**返回字段**：
```json
{
  "fan_status_summary": {
    "mode_name": "Manual",      // Auto 或 Manual
    "is_manual": true,
    "is_fixed_pwm": true,       // PWM 是否固定
    "fixed_pwm": 10,            // 固定 PWM 值
    "fan_count": 6
  }
}
```

### K8s 工具

| 工具 | 必需参数 | 参数来源 | 用途 |
|------|---------|---------|------|
| `k8s.get_node_ip` | `node_name` | `${labels.Hostname}` | K8s hostname → Node IP |
| `k8s.list_pods` | `namespace` | `${labels.namespace}` | 列出 Pod |

## Recommended Evidence

按以下顺序收集证据：

1. **GPU 状态**：
   ```
   gpu.get_metrics(node=${host_ip})
   gpu.get_processes(node=${host_ip})
   ```

2. **BMC 风扇状态**：
   ```
   bmc.get_fan_status(node=${host_ip})
   ```

3. **如果 host_ip 缺失**：
   ```
   k8s.get_node_ip(node_name=${labels.Hostname})  → 获取 node_ip
   ```

## Thermal Triage Heuristics

### 单卡高温，其他卡正常

- 先看 `fan.speed`、`power.draw`、`clocks.current.sm` 是否同步异常。
- 检查 BMC 风扇控制模式：
  - `mode_name=Manual` + `is_fixed_pwm=true` → 风扇控制策略异常（强根因）
  - `fixed_pwm < 30` → PWM 过低，散热不足
- 若风扇正常但温度高 → 怀疑散热器贴合、导热材料老化
- 检查 GPU 进程是否长期满载

### 全部 GPU 都高温

- 优先怀疑环境温度、机柜风道堵塞
- 对比相邻节点温度，排除机房级问题
- 检查统一功耗上升和热节流

### 相邻 GPU 高温

- 物理布局热耦合问题
- 检查机箱风道死角

## Diagnosis Output

诊断结果应包含：

1. **root_cause**：明确指出是"负载升温"还是"散热异常"
2. **evidence引用**：引用具体工具输出数据（如 `fan.fixed_pwm=10`）
3. **层级分类**：hardware/service/environment

## Common Pitfalls

### 不要直接用 bmc_host 参数

❌ 错误：`bmc.get_fan_status(bmc_host=10.0.12.191)`（instance IP）
✅ 正确：`bmc.get_fan_status(node=10.11.4.13)`（host_ip，自动映射）

### 不要用 hostname 作为 node 参数

❌ 错误：`gpu.get_metrics(node=wj-lab-cpt-04)`（不在 inventory）
✅ 正确：`gpu.get_metrics(node=${host_ip})`（使用 IP）

### 确保 BMC credentials 已配置

Inventory 中需要配置 BMC credentials：
```yaml
- name: worker-04
  k8s_node_name: wj-lab-cpt-04
  ssh:
    host: 10.11.4.13
  redfish:
    bmc_host: 10.11.8.13
    username: admin
    password: Admin@9000
    verify_tls: false
```