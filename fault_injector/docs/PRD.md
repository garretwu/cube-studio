<!--
=============================================================================
FILE: PRD.md (Product Requirements Document)
PURPOSE: Define product vision, goals, user stories, and acceptance criteria
GUIDANCE FOR AI AGENTS:
- This file defines WHAT the product should do, not HOW
- Before implementing features, ensure they align with requirements here
- Update this file when adding new features or changing existing ones
- Each feature should have clear acceptance criteria
- Reference this file when making implementation decisions
=============================================================================
-->

# Product Requirements Document: Fault Injector

## Document Metadata
| Field | Value |
|-------|-------|
| Product Name | Fault Injector |
| Version | 1.0 |
| Status | Implemented |
| Last Updated | 2026-02-27 |
| Owner | AIDC Auto-SRE Team |

---

## 1. Executive Summary

### 1.1 Product Vision

Fault Injector 是 Cube Studio 平台的多维度故障注入系统，采用 **确定性编排 + LLM 诊断** 的混合架构，为智算数据中心（AIDC）提供安全可控的韧性测试能力。系统能够在硬件、OS、平台、服务四个层次注入故障，并与 Load Simulator 联动进行过载测试。

### 1.2 Problem Statement

智算数据中心面临以下挑战：
- **故障复杂度高**：GPU、RDMA、K8s 等多层组件的故障传播链难以预测
- **根因定位困难**：vLLM 推理延迟不稳定可能有多种 root cause（GPU 争用、网络抖动、存储 I/O、热降频等）
- **韧性验证缺失**：缺乏系统化的故障注入和恢复验证手段
- **运维经验难以积累**：每次故障处理的上下文和模式未能沉淀

### 1.3 Target Users

| 用户角色 | 使用场景 |
|---------|---------|
| SRE 工程师 | 验证系统韧性、演练故障恢复流程 |
| 平台开发者 | 测试服务容错能力、验证降级策略 |
| 运维团队 | 建立故障知识库、培训应急响应能力 |

---

## 2. Product Goals

### 2.1 Business Goals
- [x] 实现 22+ 故障场景覆盖（硬件/OS/平台/服务四层）
- [x] 完成必选场景：vLLM 延迟不稳定（6 个 root cause）和 RDMA 异常（6 个子场景）
- [x] 提供可恢复的故障注入能力（WAL 回滚保证）
- [ ] 与 Load Simulator 完成联动测试
- [ ] 生成韧性评估报告

### 2.2 User Goals
- [x] SRE 工程师能够通过 CLI 一键注入指定故障场景
- [x] 系统自动记录故障时间线和恢复过程
- [x] 支持干运行（dry-run）验证配置
- [ ] 生成可视化 HTML 报告
- [ ] LLM 辅助根因分析和韧性评分

### 2.3 Non-Goals
- 不支持生产环境的实时故障注入（仅限测试/演练环境）
- 不支持 BMC 网络配置变更（安全限制）
- 不支持跨 Session 的故障恢复（Demo 阶段单租户限制）
- 不直接使用 LLM 执行故障注入（仅用于诊断分析）

---

## 3. Features

### Feature 1: 故障场景注入引擎

| Field | Description |
|-------|-------------|
| **Priority** | P0 |
| **Status** | Complete |
| **Description** | 核心故障注入引擎，支持 22+ 预定义场景的注入、监控、恢复、验证 |
| **User Story** | 作为 SRE 工程师，我希望能够一键注入指定的故障场景，以便验证系统的故障恢复能力 |

**Acceptance Criteria:**
- [x] 支持通过 CLI `--scenario <name>` 指定场景
- [x] 支持通过 YAML 配置文件定义场景参数
- [x] 每个场景实现 `inject()`, `monitor_queries()`, `recover()`, `verify()` 四个接口
- [x] 故障注入前自动记录恢复命令到 WAL
- [x] 支持干运行模式（`--dry-run`）仅打印操作不执行

**Dependencies:**
- Channel 层（SSH/Redfish/Switch/K8s/Prometheus）
- Safety Guard 安全守卫
- Rollback Journal WAL 回滚日志

---

### Feature 2: 必选场景 — vLLM 延迟不稳定

| Field | Description |
|-------|-------------|
| **Priority** | P0 |
| **Status** | Complete |
| **Description** | 覆盖导致 vLLM 推理延迟不稳定的 6 种 root cause 场景 |
| **User Story** | 作为平台开发者，我希望能够模拟各种导致推理延迟的场景，以便验证服务的降级能力 |

**Root Cause Scenarios:**
| ID | 场景名 | 描述 | 注入方法 |
|----|-------|------|---------|
| RC-1 | gpu_contention | GPU 资源争抢 | gpu-burn + GPU 显存压力 |
| RC-2 | network_jitter | 网络链路抖动 | tc netem (Pareto 分布延迟) |
| RC-3 | storage_io_interference | 存储 I/O 干扰 | fio 随机读写压力 |
| RC-4 | platform_cascade | 平台组件级联延迟 | MySQL 慢查询 / Redis 延迟 |
| RC-5 | os_resource_pressure | OS 资源压力 | stress-ng (CPU/内存压力) |
| RC-6 | thermal_throttling | 热降频 | Redfish 风扇降速 + GPU 满载 |

**Acceptance Criteria:**
- [x] 6 个场景全部实现并通过单元测试
- [x] 每个场景定义至少 4 个监控 PromQL 查询
- [x] 验证恢复后指标回到基线 ±15% 容差

---

### Feature 3: 必选场景 — RDMA 网络异常

| Field | Description |
|-------|-------------|
| **Priority** | P0 |
| **Status** | Complete |
| **Description** | 覆盖 RDMA (RoCEv2) 网络的 6 种异常场景 |
| **User Story** | 作为 SRE 工程师，我希望能够模拟 RDMA 网络故障，以便验证分布式训练的容错能力 |

**RDMA Scenarios:**
| ID | 场景名 | 描述 | 注入方法 |
|----|-------|------|---------|
| F-1 | pfc_deadlock | PFC 死锁 | 交换机 PFC 全优先级 no-drop |
| F-2 | ecn_misconfiguration | ECN 阈值错配 | 交换机 ECN marking threshold 设为极低值 |
| F-3 | rdma_load_imbalance | RDMA 负载不均衡 | 关闭 ECMP 部分路径 |
| F-4 | rdma_link_flap | RDMA 链路间歇中断 | 交换机端口周期性 shutdown/undo shutdown |
| F-5 | roce_mtu_mismatch | RoCE MTU 不匹配 | 节点 NIC MTU 设为 4096（其他为 9000） |
| F-6 | rdma_qos_downgrade | RDMA QoS 降级 | DSCP→TC 映射到低优先级队列 |

**Acceptance Criteria:**
- [x] 6 个场景全部实现并通过单元测试
- [x] 支持通过 H3C 交换机 NETCONF/SSH CLI 注入
- [x] 提供验证 RDMA 连通性的辅助命令

---

### Feature 4: 扩展场景 — 硬件/OS/平台/服务层

| Field | Description |
|-------|-------------|
| **Priority** | P1 |
| **Status** | Complete |
| **Description** | 扩展场景覆盖 CPU/GPU/内存/网络/存储/平台组件/服务 |
| **User Story** | 作为 SRE 工程师，我希望能够注入各层级的故障，以便全面验证系统韧性 |

**Hardware Scenarios:**
- `cpu_stress`: CPU 过载 (stress-ng)
- `gpu_removal`: GPU 掉卡 (PCIe remove)
- `thermal_throttle_hw`: 热降频 (Redfish 风扇控制)
- `network_delay_hw`: 网络延迟 (tc netem)

**OS Scenarios:**
- `memory_pressure`: 内存压力 (stress-ng --vm)
- `disk_full`: 磁盘空间耗尽 (fallocate)
- `time_skew`: 时钟偏移 (date -s)

**Platform Scenarios:**
- `mysql_connection_drop`: MySQL 连接中断 (iptables)
- `redis_unavailable`: Redis 不可用 (redis-cli SHUTDOWN)
- `celery_worker_kill`: Celery Worker Kill (kubectl delete pod)
- `istio_gateway_kill`: Istio Gateway Kill (kubectl delete pod)

**Service Scenarios:**
- `inference_pod_kill`: 推理 Pod Kill
- `pipeline_workflow_cancel`: Pipeline Workflow 取消
- `notebook_pod_kill`: Notebook Pod Kill

---

### Feature 5: 安全保护机制

| Field | Description |
|-------|-------------|
| **Priority** | P0 |
| **Status** | Complete |
| **Description** | 多层安全防护，确保故障注入可恢复、可审计 |
| **User Story** | 作为平台管理员，我希望故障注入有安全边界，避免不可逆损害 |

**Safety Mechanisms:**
| 机制 | 说明 | 状态 |
|------|------|------|
| WAL 回滚日志 | 注入前写入恢复命令，崩溃后可恢复 | ✅ Complete |
| Safety Guard | 硬编码禁止危险操作（rm -rf /、BMC 恢复出厂等） | ✅ Complete |
| dry-run 模式 | 仅打印操作不实际执行 | ✅ Complete |
| 自动恢复看门狗 | 超时后自动恢复所有故障 | ✅ Complete |
| 确认门控 | 危险操作需终端交互确认 | ✅ Complete |

---

### Feature 6: LLM 诊断分析

| Field | Description |
|-------|-------------|
| **Priority** | P2 |
| **Status** | Planned |
| **Description** | 使用 MiniMax-2.1 LLM 进行故障传播链分析和韧性评分 |
| **User Story** | 作为 SRE 工程师，我希望系统能自动分析故障影响并给出改进建议 |

**Acceptance Criteria:**
- [ ] 生成故障传播链（Propagation Chain）
- [ ] 输出根因分析和置信度
- [ ] 计算韧性评分（0-100）
- [ ] 发现未预期影响
- [ ] 给出改进建议

---

### Feature 7: 报告生成

| Field | Description |
|-------|-------------|
| **Priority** | P1 |
| **Status** | Planned |
| **Description** | 生成 HTML 韧性报告和可视化图表 |
| **User Story** | 作为 SRE 工程师，我希望获得可视化的故障测试报告 |

**Report Contents:**
- 故障时间线（Event Timeline）
- 指标对比（基线 vs 故障期 vs 恢复后）
- 韧性评分雷达图
- 诊断结论和建议

---

## 4. Technical Constraints

### 4.1 Must Have
- Python 3.11+ with asyncio 异步框架
- Pydantic v2 配置校验
- 所有写操作通过 WAL 记录恢复命令
- SSH 命令参数必须 shlex.quote() 防注入
- Safety Guard 白名单机制

### 4.2 Nice to Have
- 与 Load Simulator 联动（通过子进程调用）
- LLM 诊断集成（MiniMax-2.1 API）
- HTML 报告生成（Jinja2 + Plotly）
- GUI 管理界面

### 4.3 Out of Scope
- 生产环境实时故障注入
- BMC 网络配置变更
- 多租户 Session 隔离

---

## 5. Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| 场景覆盖率 | 22+ scenarios | `fault-injector --list-scenarios` |
| 场景可恢复率 | 100% | 所有场景有 recover() 实现 |
| WAL 崩溃恢复 | 100% | kill 进程后 --resume 成功 |
| 安全拦截率 | 100% | 禁止操作均被拦截 |
| vLLM 场景验证 | 6/6 | RC-1~RC-6 全部通过 |
| RDMA 场景验证 | 6/6 | F-1~F-6 全部通过 |

---

## 6. Timeline

| Phase | Features | Status |
|-------|----------|--------|
| Sprint 1 | CLI + Config + Safety + Channels | ✅ Complete |
| Sprint 2 | vLLM 6 场景 + RDMA 6 场景 | ✅ Complete |
| Sprint 3 | 扩展场景 + 单元测试 | ✅ Complete |
| Sprint 4 | 报告生成 + LLM 诊断 | 🔄 Planned |
| Sprint 5 | Load Simulator 联动 + E2E 测试 | 🔄 Planned |

---

## 7. Open Questions

- [ ] LLM 诊断的 API Key 管理策略
- [ ] 与 SRE Agent 的集成接口
- [ ] 多集群故障注入的支持方式
- [ ] 韧性评分算法的详细定义

---

## 8. Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-02-27 | 1.0 | AIDC Auto-SRE Team | 基于 fault-injector.md 设计规格更新，反映实现状态 |