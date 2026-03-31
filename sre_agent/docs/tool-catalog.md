# SRE Agent Tool Catalog

本文档整理当前项目中通过 [`build_default_registry()`](/root/workspace/cube-studio/sre_agent/tools/registry.py#L230) 实际注册的工具。

## 1. 说明

| 字段 | 含义 |
|------|------|
| Tool | 工具名 |
| Level | 安全级别 |
| Approval | 是否默认需要审批 |
| Channel | 运行该工具依赖的底层 channel |
| Use Case | 典型用途 |

## 2. 只读工具

| Tool | Level | Approval | Channel | Use Case |
|------|------|------|------|------|
| `prometheus.query_instant` | `read_only` | 否 | `prometheus` / `metrics` | 查询单点 PromQL 指标 |
| `prometheus.query_range` | `read_only` | 否 | `prometheus` / `metrics` | 查询时间区间 PromQL 数据 |
| `gpu.get_metrics` | `read_only` | 否 | `ssh` | 查看 GPU 利用率、显存、温度 |
| `gpu.get_processes` | `read_only` | 否 | `ssh` | 查看 GPU 上的计算进程 |
| `k8s.list_pods` | `read_only` | 否 | `k8s` | 列出 namespace 下的 Pod |
| `k8s.describe_pod` | `read_only` | 否 | `k8s` | 查看 Pod 状态摘要 |
| `k8s.read_pod_logs` | `read_only` | 否 | `log` | 读取 Pod 日志 |
| `k8s.top_pending` | `read_only` | 否 | `k8s` | 统计 Pending Pod 数量 |
| `k8s.top_oomkilled` | `read_only` | 否 | `k8s` | 统计 OOMKilled Pod 数量 |
| `network.get_rdma_stats` | `read_only` | 否 | `ssh` | 查看 RDMA 和链路健康信息 |
| `network.get_switch_port_counters` | `read_only` | 否 | `switch` | 查看交换机端口计数器和状态 |
| `ontology.query` | `read_only` | 否 | `ontology` | 查询实体或过滤实体 |
| `ontology.path` | `read_only` | 否 | `ontology` | 查询实体间路径 |
| `ontology.blast_radius` | `read_only` | 否 | `ontology` | 查询影响面 |
| `memory.search_incidents` | `read_only` | 否 | `memory` | 检索历史事件 |
| `memory.search_patterns` | `read_only` | 否 | `memory` | 检索学习到的模式 |
| `memory.get_config_baseline` | `read_only` | 否 | `memory` | 获取配置基线 |

## 3. 写工具

| Tool | Level | Approval | Channel | Use Case |
|------|------|------|------|------|
| `kill_process` | `high` | 是 | `ssh` | 终止节点上的异常进程 |
| `remediation.execute_plan` | `critical` | 是 | `remediation` | 执行 remediation plan |
| `k8s.apply_manifest` | `high` | 是 | `k8s` | 应用 K8s manifest |
| `k8s.delete_pod` | `high` | 是 | `k8s` | 删除 Pod |
| `k8s.scale_deployment` | `high` | 是 | `k8s` | 扩缩容 Deployment |
| `k8s.cordon_node` | `high` | 是 | `k8s` | 将节点标记为不可调度 |
| `k8s.drain_node` | `critical` | 是 | `k8s` | 驱逐节点工作负载 |
| `network.switch_port_enable` | `high` | 是 | `switch` | 启用交换机端口 |
| `network.switch_port_disable` | `critical` | 是 | `switch` | 禁用交换机端口 |
| `network.update_route` | `critical` | 是 | `switch` | 更新交换机路由或原始配置 |
| `network.set_bmc_vlan` | `critical` | 是 | `redfish` | 修改 BMC VLAN |
| `network.set_bmc_mtu` | `critical` | 是 | `redfish` | 修改 BMC MTU |

## 4. 当前 demo 常用工具

### 4.1 vLLM 延迟诊断 Demo

常用工具：

- `prometheus.query_instant`
- `gpu.get_metrics`
- `gpu.get_processes`
- `k8s.list_pods`
- `network.get_rdma_stats`
- `kill_process`（修复阶段）

对应脚本：

- [`vllm_latency_p95_live_demo.py`](/root/workspace/cube-studio/sre_agent/scripts/vllm_latency_p95_live_demo.py)
- [`run_vllm_latency_p95_live_demo.sh`](/root/workspace/cube-studio/sre_agent/scripts/run_vllm_latency_p95_live_demo.sh)

### 4.2 GPU 利用率诊断 Demo

常用工具：

- `gpu.get_metrics`
- `gpu.get_processes`
- `k8s.list_pods`
- `prometheus.query_instant`
- `kill_process`（修复阶段）

对应脚本：

- [`gpu_utilization_high_live_demo.py`](/root/workspace/cube-studio/sre_agent/scripts/gpu_utilization_high_live_demo.py)

## 5. 注意事项

- 大部分写工具都要求审批，直接执行时需要 `ToolExecutionContext.write_approved=True` 或经过 remediation/approval 流程。
- `network.set_bmc_vlan` 和 `network.set_bmc_mtu` 除了需要审批，还受 registry 硬阻断策略限制。
- 实际 demo 会通过 `allowed_tool_names` 进一步限制 Agent 本轮能使用的工具，不是所有注册工具都会在每次诊断中暴露给模型。
