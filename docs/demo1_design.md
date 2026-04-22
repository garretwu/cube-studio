# Demo 1 设计：vLLM P90 延迟异常 → 自动诊断 → 灰度修复

> 目标：用一个端到端的 live demo，证明 AIDC Auto SRE Agent 在真实推理场景下"能发现、能诊断、能修复、越用越懂"。
> 参考：`AIDC-auto-SRE.md` §1–2、§7（Remediation Engine + Canary + WAL）、§8.3（Runbook）、`sre_agent/`、`fault_injector/scenarios/vllm_latency.py`、`load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml`。

---

## 一、为什么选这个场景

- **业务可感**：P90/P99 延迟是推理服务最直接的 SLO；GPU util、显存、温度这些指标对非运维同学不直观，但"用户等了多久拿到答案"所有人都看得懂。
- **指标链路完整**：`vllm:e2e_request_latency_seconds_bucket`、`vllm:time_to_first_token_seconds_bucket`、`vllm:inter_token_latency_seconds_bucket`、`vllm:request_queue_depth`、DCGM GPU 指标均已埋点，无需新增。
- **基础设施到位**：
  - `fault_injector/vllm-latency-p95-live-demo.yaml` 已证跑通 `gpu_contention` 注入 → 诊断链路；
  - `load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml` 已有常驻推理负载（Qwen3-32B-FP8，concurrency=16）；
  - Channel 层 SSH/Redfish/K8s/Prometheus 都已就绪。
- **能体现 ReAct 的价值**：P90 劣化可由多种根因引起，需要"采集 → 排除 → 验证"循环，而不是单次推理就能定位。这正是我们选择 LangGraph ReAct（而非规则引擎）的原因。

---

## 二、推荐的错误注入（业界真实问题，按推荐度排序）

| # | 故障 | 业界真实成因 | 注入手段（复用现有能力） | Demo 友好度 | 主要劣化信号 |
|---|------|--------------|--------------------------|--------------|---------------|
| **A** ⭐ | **同卡 GPU 争用（noisy neighbor）** | 残留训练作业/DaemonSet/未清理的 gpu-burn sidecar 与推理 Pod 共享 GPU；MIG/MPS 误配置；多租户调度失效 | `GPUContentionScenario`（已有，`gpu-burn -m 90% -i 0`） | ★★★★★（秒级起效、可见、可杀掉） | ITL P90↑、SM util≈100%、KV cache eviction、`nvidia-smi` GPU process 列表出现非 vLLM PID |
| **B** ⭐ | **主机侧 CPU/内存压榨（tokenizer/scheduler 抢占）** | 同节点跑了数据预处理、日志导出、或 cgroup 配额过大的 sidecar；OOM 致 kswapd 抖动 | `OSResourcePressureScenario`（已有，`stress-ng`） | ★★★★ | TTFT P90↑（不是 ITL）、CPU util↑、tokenize 延迟上升、`request_queue_depth`↑ |
| **C** | **热降频（Thermal throttle）** | 机柜冷通道故障/风扇卡死/BMC 风扇曲线错配；长时间高负载致温度累加 | `ThermalThrottlingScenario`（已有，Redfish 改风扇 + `nvidia-smi -pl 150`） | ★★★ | GPU temp↑、SM clock↓、throughput↓、ITL 与 TTFT 同步劣化 |
| **D** | **存储 I/O 挤占（NVMe/对象存储）** | LoRA/adapter 按需加载、prompt log 外写、邻居 Pod 做 checkpoint | `StorageIOInterferenceScenario`（已有，`fio`） | ★★ | 冷启动 TTFT 抖动、长尾 P99 |
| **E** | **网络抖动（客户端侧/TP 链路）** | RoCE PFC 误发、网卡固件抖动、交换机端口 micro-burst | `NetworkJitterScenario`（已有，`tc netem`） | ★★ | e2e P90↑ 但 ITL 稳、TTFT 正常、server-side 指标正常 |
| **F** | **副本减少 / Pod 被驱逐** | Node NotReady、OOMKilled、HPA bug、预留资源不足 | K8s Channel：`kubectl delete pod` 或 `scale deploy --replicas=N-1` | ★★★ | QPS 不变但单 Pod 负载翻倍、`queue_depth`↑、e2e P90↑ |

### 强烈推荐组合

> **主场景 = A（GPU 争用）**；**可选混入 C（热降频）作为"假阳性诱饵"**。

原因：
1. A 是生产环境最常见、最被 SRE 吐槽的问题之一（"又有人把 gpu-burn 忘了关"）。
2. C 的症状（SM clock 异常）与 A 有部分重叠，需要 Agent 通过 `get_gpu_processes`、`get_thermal_status`、`DCGM_FI_DEV_GPU_TEMP` 做排除性推理——这正是 ReAct 相对于规则引擎的价值。
3. A 的修复动作（kill 进程）爆炸半径小、可回滚、适合做 canary 演示。

---

## 三、推荐的端到端流程

相对用户的参考流程，建议在开头加 **"稳态基线"**、在诊断处强制展示 **ReAct 过程**、修复段强调 **WAL + 灰度**、结尾加 **知识沉淀**。完整 7 幕：

### Phase 0 — 背景铺垫（0:00–0:30）
- 打开 Ontology UI：展示 `qwen3-32b-fp8` → Pod → Node(`worker-03`) → GPU0 的拓扑。
- 一句台词："Agent 在看到告警前就已经理解这个服务长什么样。"

### Phase 1 — 稳态 + 负载上压（0:30–2:00）
- 启动 `load_simulator` 常驻 inference 负载（concurrency=16，已配置）。
- Grafana 面板展示三条基线：
  - `histogram_quantile(0.90, sum by(le) (rate(vllm:e2e_request_latency_seconds_bucket[1m])))` ≈ 200–300 ms
  - TTFT P90、ITL P90
  - `DCGM_FI_DEV_GPU_UTIL` ≈ 60–80%
- **强调**："这是真实负载下的真实指标，不是 mock。"

### Phase 2 — 故障注入（2:00–2:30）
- 在 worker-03 GPU0 上通过 `fault_injector` 启动 `gpu_contention`（`-m 90% --intensity 90`，duration 600s）。
- 同步把注入事件标在 Grafana 时间轴上（annotation），观众一眼能看到因果。
- **可选加戏**：同时把 GPU0 `nvidia-smi -pl` 降到 250W，制造 thermal 假阳性。

### Phase 3 — 劣化 + 告警（2:30–4:00）
- 约 30–60 秒内 P90 e2e 从 ~250 ms 飙到 >1s，ITL P90 飙升更剧烈。
- Prometheus Alertmanager 触发 `VLLMRequestLatencyP90High`（阈值 500ms 持续 30s），进入 SRE Agent 队列。
- **前端展示**：告警卡片进入 Incident 列表，状态 `diagnosing`。

### Phase 4 — ReAct 诊断（4:00–6:00）⭐ 演示核心
前端 WebSocket 流式渲染 `thinking_trace`，按真实顺序展示 Agent 多步推理：

1. **Thought 1**："P90 劣化可能是 GPU 争用 / 热降频 / 网络 / KV cache 压力，先看 GPU 侧。"
2. **Action 1**：`get_gpu_metrics(node=worker-03, gpu=0)` → SM util 98%，temp 72°C（正常）。
3. **Thought 2**："SM 打满但温度正常，排除 thermal；看 SM 被谁占。"
4. **Action 2**：`get_gpu_processes(node=worker-03, gpu=0)` → 返回 2 个 PID：vllm worker + `gpu_burn`（非 vLLM 进程占用 70% SM）。
5. **Action 3**：`get_pods(node=worker-03, namespace=service)` → 确认只有 1 个 vLLM pod 合法持有 GPU0。
6. **Action 4**：`search_incidents("gpu_burn co-tenant")` → 命中历史事件 + runbook `vllm-p90-high.yaml`。
7. **Conclude**：输出 `DiagnosisResult`，按置信度排序候选：
   - **C1**（confirmed, 0.92）：非 vLLM 进程 `gpu_burn` PID=xxxxx 占用 GPU0 SM，导致 ITL 升高 → 修复 = 终止进程。
   - **C2**（probable, 0.35）：GPU 热降频（已基于温度观测排除，保留为备选）。
   - **C3**（possible, 0.10）：网络抖动（未观测到证据）。

> 这 2 分钟是演示"智能"的高光时刻。

### Phase 5 — 受控修复（6:00–7:30）
严格按照 **"受控修复"** 不变量演示：

1. **审批门控**：前端弹出 RemediationPlan 卡片：操作 = `kill PID xxxxx on worker-03`，爆炸半径 = 1 GPU/1 Pod，风险 = low。Demo 里配置为 `auto_approve`（live demo 阶段）或手动点 "Approve"。
2. **WAL 写入**：展示回滚日志条目已 fsync（展示 `wal.jsonl` 一行）。
3. **灰度执行**：即使目前只有 1 个 Pod，也显式走 canary 路径——"先终止 1/1 目标进程，等待 60 秒验证"。
4. **SSH Channel 执行**：`pkill -f fi_gpu_burn_*`（复用 `GPUContentionScenario.recover()` 的路径，保证注入/修复同一条通道）。
5. **验证**：轮询 P90 和 SM util，60 秒后 SM util 回到 75%，P90 回落到 ~280 ms。
6. **状态流转**：`remediating → verifying → resolved`。

### Phase 6 — 指标恢复 + 知识沉淀（7:30–8:30）
- Grafana 上 P90 曲线完整的 V 字恢复，annotation 标记 "Remediation applied"。
- 展示写入 Memory 的内容：
  - `IncidentRecord`（完整 thinking trace + 修复结果）
  - `PatternMemory` 更新："worker-03 GPU0 近 30 天第 3 次 gpu_burn 争用 → 升级建议：接入 Admission Webhook 阻止非 service/ 命名空间调度到 serving GPU"。
- 收尾："越用越懂这个 AIDC。"

---

## 四、可信度加分项（必须做到）

1. **所有指标来自真实 Prometheus，不加 mock**。演示前 10 分钟预热负载，保证基线曲线真实抖动。
2. **注入和修复走同一条 Channel 路径**（SSH + sudo），杜绝"注入用 A、修复用 B"的作弊感。
3. **Thinking trace 是流式的**，不能等 Agent 跑完一次性吐。观众看到 Agent 真的在"边想边做"。
4. **（可选）保留一次"误诊后回滚"彩蛋**：先让 Agent 按 C2 thermal 修复（调高 fan PWM），验证失败 → WAL 回滚 → Loop Orchestrator 再走 C1 kill 进程。这一把能彻底证明 "Loop Orchestrator + WAL" 不是 PPT。

---

## 五、演示前准备清单

- [ ] 新建 `demo-vllm-p90.yaml`（本设计配套产物，见仓库根目录）。
- [ ] PromQL 统一到 `vllm:e2e_request_latency_seconds_bucket` 的 P90，阈值 500 ms。
- [ ] 起草 `runbooks/vllm-p90-high.yaml`（参考 §8.3 格式），`ingest` 到 Knowledge Store 以便 RAG 命中。
- [ ] 向 Memory 预置 1–2 条同类历史 incident，证明"越用越懂"。
- [ ] Grafana 面板：6 图一屏（e2e P90、TTFT P90、ITL P90、SM util、GPU temp、queue depth），顶部加告警/注入/修复时间轴 annotation。
- [ ] 前端 `sre_agent/frontend` 打开 Thinking Trace + Ontology + Incident 三栏布局。

---

## 六、下一步 / 配套产物

本文档配套的可执行配置位于仓库根目录：

- **`demo-vllm-p90.yaml`** — 合并了 load_simulator、fault_injector、agent 的单一 demo 配置，可通过
  ```bash
  python sre_agent/scripts/vllm_latency_p95_live_demo.py \
    --demo-config demo-vllm-p90.yaml \
    --demo-key vllm_request_latency_p90_alert_remediation
  ```
  一键跑通 Phase 1–6。

后续任务（独立 PR）：
- 编写 `runbooks/vllm-p90-high.yaml` 正式版本；
- 起草 presenter timeline 脚本（每 30 秒 presenter 说什么 + UI 高亮什么）；
- 在 Grafana 中保存 Demo 专用 dashboard。
