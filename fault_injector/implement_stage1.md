# Fault Injector 实现进度 - Stage 1

> 更新日期：2026-02-25  
> 基于 `fault-injector.md` 设计规格

## 1. Channel 层完成情况

### 1.1 已完成 ✅

| 文件 | 类名 | 功能 | 状态 |
|------|------|------|------|
| `channels/base.py` | `BaseChannel` | Channel 基类，统一 dry_run 和 WAL 集成 | ✅ 完成 |
| `channels/ssh.py` | `SSHChannel` | SSH 命令执行，支持连接池、sudo、dry_run | ✅ 完成 |
| `channels/prometheus.py` | `PrometheusChannel` | Prometheus HTTP API 查询（即时/范围） | ✅ 完成 |
| `channels/redfish.py` | `RedfishChannel` | BMC Redfish REST API 带外管理 | ✅ 完成 |
| `channels/switch.py` | `SwitchChannel` | H3C 交换机 CLI over SSH | ✅ 完成 |
| `channels/kubernetes.py` | `K8sChannel` | Kubernetes API 操作（Pod/Deployment） | ✅ 完成 |

### 1.2 Channel 代码质量

- ✅ 统一继承 `BaseChannel`
- ✅ 支持 `dry_run` 模式
- ✅ 集成 `RollbackJournal` (WAL)
- ✅ 集成 `SafetyGuard` 安全守卫
- ✅ 异步实现 (async/await)

---

## 2. 必选场景完成情况

### 2.1 vLLM Latency 场景 (RC-1~RC-6) ✅ 全部完成

| ID | 场景名 | 描述 | 层级 | 状态 | 实现文件 |
|----|--------|------|------|------|----------|
| RC-1 | `gpu_contention` | GPU 资源争抢 (gpu-burn) | hardware | ✅ | `vllm_latency.py` |
| RC-2 | `network_jitter` | 网络延迟抖动 (tc netem) | os | ✅ | `rdma_anomaly.py` |
| RC-3 | `storage_io_interference` | 存储 I/O 干扰 (fio) | os | ✅ | `vllm_latency.py` |
| RC-4 | `platform_cascade` | 平台组件级联延迟 | platform | ✅ | `vllm_latency.py` |
| RC-5 | `os_resource_pressure` | OS 资源压力 (stress-ng) | os | ✅ | `vllm_latency.py` |
| RC-6 | `thermal_throttling` | 热降频 (nvidia-smi 功率限制) | hardware | ✅ | `vllm_latency.py` |

### 2.2 RDMA 异常场景 (F-1~F-6) ✅ 全部完成

| ID | 场景名 | 描述 | 层级 | 状态 | 实现文件 |
|----|--------|------|------|------|----------|
| F-1 | `pfc_deadlock` | PFC 死锁 | hardware | ✅ | `rdma_anomaly.py` |
| F-2 | `ecn_misconfiguration` | ECN 标记阈值错配 | hardware | ✅ | `rdma_anomaly.py` |
| F-3 | `rdma_load_imbalance` | 不均衡 RDMA 负载 | hardware | ✅ | `rdma_anomaly.py` |
| F-4 | `rdma_link_flap` | RDMA 链路间歇性中断 | hardware | ✅ | `rdma_anomaly.py` |
| F-5 | `roce_mtu_mismatch` | RoCE 网络 MTU 不一致 | os | ✅ | `rdma_anomaly.py` |
| F-6 | `rdma_qos_downgrade` | RDMA QoS 降级 | os | ✅ | `rdma_anomaly.py` |

### 2.3 场景注册表

所有必选场景已在 `scenarios/registry.py` 中注册：

```python
SCENARIO_REGISTRY: dict[str, Type[BaseScenario]] = {
    # vLLM 延迟场景 (RC-1~RC-6)
    "gpu_contention": GPUContentionScenario,
    "network_jitter": NetworkJitterScenario,
    "storage_io_interference": StorageIOInterferenceScenario,
    "platform_cascade": PlatformCascadeScenario,
    "os_resource_pressure": OSResourcePressureScenario,
    "thermal_throttling": ThermalThrottlingScenario,
    
    # RDMA 异常场景 (F-1~F-6)
    "pfc_deadlock": PFCDeadlockScenario,
    "ecn_misconfiguration": ECNMisconfigurationScenario,
    "rdma_load_imbalance": RDMALoadImbalanceScenario,
    "rdma_link_flap": RDMALinkFlapScenario,
    "roce_mtu_mismatch": RoCEMTUMismatchScenario,
    "rdma_qos_downgrade": RDMAQoSDowngradeScenario,
}
```

---

## 3. 扩展场景完成情况 ✅ 全部完成

所有扩展场景已实现并注册到 `scenarios/registry.py`。

### 3.1 硬件层 (4 个) ✅

| 场景名 | 描述 | 注入方法 | 状态 | 实现文件 |
|--------|------|----------|------|----------|
| `cpu_stress` | CPU 压力 | stress-ng --cpu | ✅ | `hardware.py` |
| `gpu_removal` | GPU 掉卡 | PCIe remove | ✅ | `hardware.py` |
| `thermal_throttle_hw` | 热降频 (硬件版) | Redfish 风扇控制 | ✅ | `hardware.py` |
| `network_delay_hw` | 网络延迟 (硬件版) | 交换机端口限速 | ✅ | `hardware.py` |

### 3.2 OS 层 (3 个) ✅

| 场景名 | 描述 | 注入方法 | 状态 | 实现文件 |
|--------|------|----------|------|----------|
| `memory_pressure` | 内存压力 | stress-ng --vm | ✅ | `os_fault.py` |
| `disk_full` | 磁盘满 | fallocate | ✅ | `os_fault.py` |
| `time_skew` | 时钟偏移 | date / chronyc | ✅ | `os_fault.py` |

### 3.3 平台层 (4 个) ✅

| 场景名 | 描述 | 注入方法 | 状态 | 实现文件 |
|--------|------|----------|------|----------|
| `mysql_connection_drop` | MySQL 连接中断 | iptables | ✅ | `platform.py` |
| `redis_unavailable` | Redis 不可用 | redis-cli SHUTDOWN | ✅ | `platform.py` |
| `celery_worker_kill` | Celery Worker 终止 | kubectl delete pod | ✅ | `platform.py` |
| `istio_gateway_kill` | Istio Gateway 终止 | kubectl delete pod | ✅ | `platform.py` |

### 3.4 服务层 (3 个) ✅

| 场景名 | 描述 | 注入方法 | 状态 | 实现文件 |
|--------|------|----------|------|----------|
| `inference_pod_kill` | 推理 Pod 终止 | kubectl delete pod | ✅ | `service.py` |
| `pipeline_workflow_cancel` | Pipeline 取消 | kubectl delete workflow | ✅ | `service.py` |
| `notebook_pod_kill` | Notebook Pod 终止 | kubectl delete pod | ✅ | `service.py` |

---

## 4. 框架目录完成情况

### 4.1 orchestrator/ - 编排引擎 ✅ 框架完成

**作用**: 管理故障注入的完整生命周期

**已创建文件**:
| 文件 | 状态 | 说明 |
|------|------|------|
| `orchestrator/__init__.py` | ✅ | 模块入口 |
| `orchestrator/engine.py` | ✅ | FaultOrchestrator 主引擎 |
| `orchestrator/session.py` | ✅ | Session 状态管理 |
| `orchestrator/watchdog.py` | ✅ | 回滚看门狗 |

**注意**: 这些是框架代码，完整功能需要后续补充

### 4.2 agents/ - Agent 层 ✅ 框架完成

**作用**: 封装不同层级的故障注入逻辑

**已创建文件**:
| 文件 | 状态 | 说明 |
|------|------|------|
| `agents/__init__.py` | ✅ | 模块入口 |
| `agents/base.py` | ✅ | BaseAgent 基类 |

**待实现 Agent**:
- `agents/hardware.py` - HardwareFaultAgent
- `agents/os_fault.py` - OSFaultAgent
- `agents/platform.py` - PlatformFaultAgent
- `agents/service.py` - ServiceFaultAgent
- `agents/monitor.py` - MonitorAgent
- `agents/diagnosis.py` - DiagnosisAgent (LLM)

### 4.3 reporting/ - 报告生成 ✅ 框架完成

**作用**: 生成故障注入报告和可视化

**已创建文件**:
| 文件 | 状态 | 说明 |
|------|------|------|
| `reporting/__init__.py` | ✅ | 模块入口，包含基础类 |

**框架包含**:
- `TimelineBuilder` - 故障时间线
- `HTMLReporter` - HTML 报告
- `ChartGenerator` - Plotly 图表
- `ResilienceScorer` - 韧性评分

**注意**: 这些是框架类，具体实现需要后续补充

---

## 5. 已完成模块

### 5.1 config/ - 配置层 ✅

- `config/schema.py` - Pydantic v2 配置模型
- `config/loader.py` - YAML 加载 + 校验
- `config/defaults.py` - 默认配置
- `config/templates/` - 配置模板

### 5.2 safety/ - 安全层 ✅

- `safety/guard.py` - SafetyGuard 安全守卫
- `safety/rollback.py` - RollbackJournal WAL 回滚日志

### 5.3 tests/ - 测试 ✅

- `tests/TEST_GUIDE.md` - 测试指南
- `tests/mocktest/` - Mock 测试框架
- `tests/mocktest/test_ssh_channel.py` - SSH Channel 测试

---

## 6. 下一步计划

### 6.1 优先级 P0 (框架代码)

1. 创建 `orchestrator/` 目录框架
2. 创建 `agents/` 目录框架
3. 创建 `reporting/` 目录框架

### 6.2 优先级 P1 (扩展场景)

1. 实现 `cpu_stress` 场景
2. 实现 `memory_pressure` 场景
3. 实现 `mysql_connection_drop` 场景
4. 实现 `redis_unavailable` 场景
5. 实现 `inference_pod_kill` 场景

### 6.3 优先级 P2 (其余扩展场景)

1. 实现其余 10 个扩展场景
2. 完善测试覆盖

---

## 7. 总结

### 已完成 ✅

- ✅ **Channel 层**: 6 个 Channel 全部完成
- ✅ **必选场景**: 12 个必选场景全部完成 (vLLM 6 + RDMA 6)
- ✅ **扩展场景**: 14 个扩展场景全部完成 (硬件 4 + OS 3 + 平台 4 + 服务 3)
- ✅ **配置层**: 完整实现
- ✅ **安全层**: 完整实现
- ✅ **测试框架**: 基础测试完成
- ✅ **编排引擎框架**: orchestrator/ 框架代码完成
  - `engine.py` - FaultOrchestrator 主引擎
  - `session.py` - Session 状态管理
  - `watchdog.py` - 回滚看门狗
- ✅ **Agent 层框架**: agents/ 框架代码完成
  - `base.py` - BaseAgent 基类
- ✅ **报告生成框架**: reporting/ 框架代码完成
  - TimelineBuilder, HTMLReporter, ChartGenerator, ResilienceScorer

### 待完成

- ⏳ **Agent 实现**: 具体 Agent 类待实现
- ⏳ **报告完善**: 报告生成具体实现待补充
- ⏳ **测试覆盖**: 场景单元测试待添加

### 目录结构概览

```
fault_injector/
├── __init__.py
├── __main__.py
├── cli.py
├── FAULT_INJECTOR_IMPLEMENTATION_GUIDE.md
├── implement_stage1.md (本文档)
│
├── channels/           ✅ 完成
│   ├── __init__.py
│   ├── base.py
│   ├── ssh.py
│   ├── prometheus.py
│   ├── redfish.py
│   ├── switch.py
│   └── kubernetes.py
│
├── scenarios/          ✅ 全部场景完成 (26 个)
│   ├── __init__.py
│   ├── base.py
│   ├── registry.py       (场景注册表)
│   ├── vllm_latency.py   (RC-1, RC-3~RC-6: 5 个)
│   ├── rdma_anomaly.py   (RC-2, F-1~F-6: 7 个)
│   ├── hardware.py       (4 个硬件扩展场景)
│   ├── os_fault.py       (3 个 OS 扩展场景)
│   ├── platform.py       (4 个平台扩展场景)
│   └── service.py        (3 个服务扩展场景)
│
├── orchestrator/       ✅ 框架完成
│   ├── __init__.py
│   ├── engine.py
│   ├── session.py
│   └── watchdog.py
│
├── agents/             ✅ 框架完成
│   ├── __init__.py
│   └── base.py
│
├── reporting/          ✅ 框架完成
│   └── __init__.py
│
├── config/             ✅ 完成
│   ├── __init__.py
│   ├── schema.py
│   ├── loader.py
│   ├── defaults.py
│   └── templates/
│
├── safety/             ✅ 完成
│   ├── __init__.py
│   ├── guard.py
│   └── rollback.py
│
└── tests/              ✅ 基础测试完成
    ├── __init__.py
    ├── TEST_GUIDE.md
    └── mocktest/
```

---

## 8. 下一步行动

### Stage 2 计划

1. **完善 Agent 层**
   - 实现 HardwareFaultAgent
   - 实现 OSFaultAgent
   - 实现 PlatformFaultAgent
   - 实现 ServiceFaultAgent
   - 实现 MonitorAgent
   - 实现 DiagnosisAgent (LLM)

2. **完善报告生成**
   - 实现 HTML 报告模板
   - 实现指标图表 (Plotly)
   - 实现韧性评分算法

3. **测试覆盖**
   - 添加场景单元测试
   - 添加集成测试
   - 添加端到端测试

4. **Load Simulator 联动**
   - 实现与 load_simulator 的集成
   - 实现过载制造故障场景

---

## 9. 场景完整清单

| 分类 | 数量 | 状态 |
|------|------|------|
| vLLM 延迟必选场景 (RC-1~RC-6) | 6 | ✅ 完成 |
| RDMA 必选场景 (F-1~F-6) | 6 | ✅ 完成 |
| 硬件层扩展场景 | 4 | ✅ 完成 |
| OS 层扩展场景 | 3 | ✅ 完成 |
| 平台层扩展场景 | 4 | ✅ 完成 |
| 服务层扩展场景 | 3 | ✅ 完成 |
| **总计** | **26** | **✅ 全部完成** |
