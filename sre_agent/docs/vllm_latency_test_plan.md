# SRE Agent vLLM延迟测试计划

## 1. 概述

### 1.1 测试目标
验证SRE Agent能够从用户角度正确诊断并修复GPU争用导致的vLLM推理延迟问题。

### 1.2 测试范围
| 维度 | 范围 |
|------|------|
| 场景 | GPU争用（gpu-burn占用GPU导致vLLM P95延迟升高） |
| 诊断 | Agent自动发现根因、给出置信度、推荐修复方案 |
| 修复 | 第一阶段仅诊断，第二阶段完整修复 |
| 观测 | 使用Arize Phoenix追踪Agent执行过程 |

### 1.3 DoD (Definition of Done)
- **第一阶段**: 系统成功发现故障根因并给出可靠修复方案
- **第二阶段**: 系统成功发现故障根因并执行修复，P95延迟恢复正常

---

## 2. 测试环境

### 2.1 环境状态
| 组件 | 状态 | 说明 |
|------|------|------|
| K8s集群 | ✅ 就绪 | GPU节点已配置 |
| vLLM服务 | ✅ 就绪 | namespace: service |
| Prometheus | ✅ 就绪 | 监控vLLM延迟指标 |
| Alertmanager | ✅ 就绪 | 告警触发 |
| Arize Phoenix | 待部署 | 开源版，LangGraph tracing |
| load_simulator | 待启动 | 模拟推理负载 |
| fault-injector | ✅ 可用 | 第二阶段故障注入 |

### 2.2 环境架构

```
┌─────────────────────────────────────────────────────────────┐
│                      K8s Cluster                             │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │   gpu-1-1    │    │   gpu-1-2    │    │ control-plane │  │
│  │              │    │              │    │               │  │
│  │ ┌──────────┐ │    │ ┌──────────┐ │    │ ┌───────────┐ │  │
│  │ │ vLLM Pod │ │    │ │ vLLM Pod │ │    │ │Prometheus │ │  │
│  │ │ GPU-0,1  │ │    │ │ GPU-0,1  │ │    │ │Alertmgr  │ │  │
│  │ └──────────┘ │    │ └──────────┘ │    │ └───────────┘ │  │
│  │              │    │              │    │               │  │
│  │ (故障注入点) │    │              │    │ ┌───────────┐ │  │
│  │ gpu-burn    │ │    │              │    │ │ SRE Agent │ │  │
│  └──────────────┘    └──────────────┘    │ └───────────┘ │  │
│                                            └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │   Phoenix   │
                    │   Server    │
                    │ (observability)
                    └─────────────┘
```

### 2.3 关键配置

#### vLLM服务
```yaml
# vLLM部署配置（假设已存在）
namespace: service
labels:
  app: vllm-deepseek
resources:
  nvidia.com/gpu: 2  # GPU-0, GPU-1
```

#### Prometheus告警规则

**1. TTFT P99高告警 (AIServiceTTFTP99High)**
```yaml
# TTFT P99延迟超标告警
groups:
  - name: vllm-alerts
    rules:
      - alert: AIServiceTTFTP99High
        expr: |
          (histogram_quantile(0.99, sum by(le, namespace, service, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m]))) > 0.5)
          or on(namespace, service, model_name) (
            (ALERTS{alertname="AIServiceTTFTP99High",alertstate="firing"} == 1)
            and on(namespace, service, model_name) (
              histogram_quantile(0.99, sum by(le, namespace, service, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))
              != histogram_quantile(0.99, sum by(le, namespace, service, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))
            )
          )
          or on(namespace, service, model_name) (
            (ALERTS{alertname="AIServiceTTFTP99High",alertstate="firing"} == 1)
            unless on(namespace, service, model_name) (
              histogram_quantile(0.99, sum by(le, namespace, service, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))
            )
          )
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "vLLM TTFT P99延迟超标"
          description: "vLLM服务TTFT P99延迟超过阈值0.5s"
```

**2. GPU利用率高告警 (GPUUtilizationHigh)**
```yaml
      - alert: GPUUtilizationHigh
        expr: DCGM_FI_DEV_GPU_UTIL > 95
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "GPU利用率过高"
          description: "GPU {{ $labels.gpu }} 利用率 {{ $value }}% 超过95%"
```

---

## 3. Arize Phoenix部署

### 3.1 安装Phoenix
```bash
# 方式1: pip安装
pip install arize-phoenix

# 方式2: Docker
docker run -p 6006:6006 arizephoenix/phoenix:latest
```

### 3.2 SRE Agent集成Phoenix
```python
# 在sre_agent/agent/graph.py中添加
from phoenix.trace.langchain import LangChainInstrumentor
from phoenix.trace import using_project

# 自动追踪LLM调用和工具执行
LangChainInstrumentor().instrument()

# 运行诊断时指定project
async def run_diagnosis_with_tracing(*args, **kwargs):
    with using_project("vllm-latency-diagnosis"):
        return await run_diagnosis(*args, **kwargs)
```

### 3.3 Phoenix观测内容
| 观测项 | 说明 |
|--------|------|
| Skill加载 | `list_skills` → `load_skill(vllm-diagnosis)` |
| 工具调用 | `get_gpu_metrics` → `get_gpu_processes` → `get_thermal_status` |
| LLM推理 | Reason步骤的token消耗、延迟、温度 |
| 状态转换 | Reason → Act → Observe → Decide → Finalize |
| 错误处理 | 超时、异常、重试 |

### 3.4 访问Phoenix UI
```
http://localhost:6006
```

---

## 4. 测试用例设计

### 4.1 测试用例: TC-001 GPU争用

| 属性 | 值 |
|------|-----|
| 用例ID | TC-001 |
| 场景 | GPU资源争用导致vLLM延迟升高 |
| 前置条件 | vLLM服务正常运行，P95 < 200ms |
| 故障注入 | gpu-burn进程占用GPU-0 |
| 预期根因 | GPU资源争用（gpu-burn进程占用GPU-0导致PCIe带宽争用） |
| 预期置信度 | ≥ 0.85 |
| 预期修复 | kill gpu-burn进程 |
| 验证条件 | 修复后P95 < 500ms |

### 4.2 诊断流程预期

```
[Alert] VLLMLatencyP95High, P95 = 680ms

Step 1 [Reason]: 分析告警，列举假设
  假设A: GPU资源争用 (优先级高)
  假设B: 网络问题
  假设C: KV Cache不足
  假设D: 热节流

Step 2 [Act]: get_inference_latency(service="vllm-deepseek")
Step 3 [Observe]: {p50=145ms, p95=680ms, p99=1250ms}
  → P50正常，P95/P99偏高，尾延迟问题

Step 4 [Act]: get_gpu_metrics(node="gpu-1-1")
Step 5 [Observe]: {GPU-0: util=97%, GPU-1: util=68%}
  → GPU-0异常高，假设A升级为主要嫌疑

Step 6 [Act]: get_gpu_processes(node="gpu-1-1")
Step 7 [Observe]: {gpu-burn on GPU-0, python3 on GPU-1}
  → 确认gpu-burn占用GPU-0

Step 8 [Act]: get_thermal_status(node="gpu-1-1")
Step 9 [Observe]: {GPU-0: 78°C, GPU-1: 72°C}
  → 温度正常，排除假设D

Step 10 [Decide]:
  根因: GPU资源争用
  置信度: 0.92
  修复: kill_process(node="gpu-1-1", pid_or_name="gpu-burn")
```

---

## 5. 测试执行步骤

### 5.1 预备步骤

```bash
# 1. 确认vLLM服务状态
kubectl get pods -n service -l app=vllm-deepseek

# 2. 确认Prometheus正常
kubectl port-forward -n monitoring svc/prometheus-k8s 9090:9090
# 访问 http://localhost:9090 确认指标正常

# 3. 启动Phoenix
phoenix serve --port 6006
# 或 docker run -p 6006:6006 arizephoenix/phoenix:latest

# 4. 启动load_simulator（持续模拟负载）
python -m load_simulator run --config load_simulator/configs/vllm_load.yaml
```

### 5.2 第一阶段：手动注入测试

#### Step 1: 记录基线
```bash
# 查询当前P95延迟
curl -s 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=histogram_quantile(0.95, rate(vllm_request_duration_seconds_bucket[5m]))' | jq

# 预期: P95 < 200ms
```

#### Step 2: 注入故障
```bash
# 在GPU节点上运行gpu-burn（通过SSH或kubectl exec）
# 假设vLLM运行在gpu-1-1节点

# 方式1: SSH到节点
ssh gpu-1-1
cd /tmp
git clone https://github.com/wilicc/gpu-burn
cd gpu-burn
make
./gpu_burn 600 > /dev/null 2>&1 &  # 运行10分钟

# 方式2: kubectl运行临时Pod
kubectl run gpu-burn --image=nvidia/cuda:11.8-base --restart=Never \
  --limits=nvidia.com/gpu=1 -- sh -c "git clone https://github.com/wilicc/gpu-burn && cd gpu-burn && make && ./gpu_burn 600"
```

#### Step 3: 等待告警触发
```bash
# 监控告警状态
watch -n 5 'curl -s http://localhost:9093/api/v2/alerts | jq ''.[] | select(.labels.alertname=="VLLMLatencyP95High")'''

# 预期: 5分钟内告警触发
```

#### Step 4: 启动SRE Agent诊断
```bash
# 通过CLI触发诊断
sre-agent diagnose --config config.yaml \
  --alert '{"alert_name":"VLLMLatencyP95High","severity":"critical","labels":{"service":"vllm-deepseek","namespace":"service","node":"gpu-1-1"},"annotations":{"summary":"vLLM P95延迟超标","value":"0.68s"}}'

# 或通过API
curl -X POST http://localhost:8080/api/diagnose \
  -H "Content-Type: application/json" \
  -d '{"alert_name":"VLLMLatencyP95High","severity":"critical","labels":{"service":"vllm-deepseek"},"annotations":{"summary":"vLLM P95延迟超标"}}'
```

#### Step 5: Phoenix观测
```
访问 http://localhost:6006
查看 project: vllm-latency-diagnosis
确认:
  - Skill加载正确 (vllm-diagnosis)
  - 工具调用链完整
  - LLM推理过程
  - 最终结论
```

#### Step 6: 验证诊断结果
```bash
# 检查诊断结果
# 预期:
# - root_cause: GPU资源争用
# - confidence: >= 0.85
# - remediation_plan: kill_process

# 记录诊断耗时
# 预期: < 30s
```

#### Step 7: 清理故障
```bash
# 手动终止gpu-burn
ssh gpu-1-1 "pkill -f gpu_burn"

# 或删除临时Pod
kubectl delete pod gpu-burn
```

### 5.3 第二阶段：fault-injector注入测试

#### Step 1: 准备fault-injector配置
```yaml
# fault_injector/fault-injector-vllm-test.yaml
scenario: vllm_rc_1_gpu_contention
description: "vLLM RC-1 GPU资源争用测试"
target:
  node: "gpu-1-1"
  gpu_index: 0
fault:
  type: gpu_contention
  duration: 600
  intensity: high  # 高强度占用
observe:
  prometheus_url: "http://prometheus-k8s.monitoring:9090"
  baseline_queries:
    - name: vllm_p95_latency
      query: 'histogram_quantile(0.95, rate(vllm_request_duration_seconds_bucket[5m]))'
load_simulator:
  enabled: true
  config: "load_simulator/configs/vllm_load.yaml"
```

#### Step 2: 执行故障注入+诊断+修复
```bash
# 运行fault-injector注入故障，同时触发SRE Agent诊断
python -m fault_injector run --config fault_injector/fault-injector-vllm-test.yaml

# 或分步执行:
# 1. 注入故障
python -m fault_injector inject --scenario vllm_rc_1_gpu_contention

# 2. 等待告警，触发诊断
sre-agent diagnose --config config.yaml --alert '{"alert_name":"VLLMLatencyP95High",...}'

# 3. 执行修复（需要审批）
sre-agent remediate --session <session_id> --approve
```

#### Step 3: 验证修复效果
```bash
# 查询P95延迟
curl -s 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=histogram_quantile(0.95, rate(vllm_request_duration_seconds_bucket[5m]))' | jq

# 预期: P95 < 500ms (修复成功)
```

---

## 6. 测试记录模板

### 6.1 测试执行记录

```yaml
# test_records/TC-001-<timestamp>.yaml
test_case: TC-001
test_phase: phase1  # phase1 | phase2
timestamp: "2024-03-24T10:30:00+08:00"
environment:
  k8s_cluster: "production"
  vllm_service: "vllm-deepseek"
  gpu_node: "gpu-1-1"

execution:
  fault_injection:
    method: manual  # manual | fault_injector
    start_time: "2024-03-24T10:31:00+08:00"
    end_time: "2024-03-24T10:41:00+08:00"
  
  alert_triggered:
    time: "2024-03-24T10:36:00+08:00"
    alert_name: "VLLMLatencyP95High"
    value: "0.68s"
  
  diagnosis:
    start_time: "2024-03-24T10:36:05+08:00"
    end_time: "2024-03-24T10:36:15+08:00"
    duration_ms: 10000
    steps: 10
    tools_called:
      - get_inference_latency
      - get_gpu_metrics
      - get_gpu_processes
      - get_thermal_status
    llm_tokens_used: 12500
    phoenix_trace_id: "trace-xxx"
  
  result:
    root_cause: "GPU资源争用"
    root_cause_layer: "hardware"
    confidence: 0.92
    diagnosis_certainty: "confirmed"
    remediation_plan:
      tool: "kill_process"
      params:
        node: "gpu-1-1"
        pid_or_name: "gpu-burn"
  
  remediation:  # 仅第二阶段
    start_time: null
    end_time: null
    approval_required: true
    approval_granted: false
    executed: false
    verification_passed: null
  
metrics:
  p95_before: "680ms"
  p95_after: null  # 第一阶段不修复
  p95_threshold: "500ms"

outcome: "diagnosed"  # diagnosed | resolved | failed | escalated
notes: ""
```

### 6.2 Phoenix观测记录

```
Phoenix Trace Summary:
- Project: vllm-latency-diagnosis
- Trace ID: trace-xxx
- Duration: 10.5s
- Spans: 15

Span Breakdown:
1. reason_node (LLM): 2.3s, 4500 tokens
2. act_node (get_inference_latency): 0.5s
3. observe_node: 0.1s
4. reason_node (LLM): 1.8s, 3200 tokens
5. act_node (get_gpu_metrics): 0.4s
6. observe_node: 0.1s
7. reason_node (LLM): 1.5s, 2800 tokens
8. act_node (get_gpu_processes): 0.3s
9. observe_node: 0.1s
10. reason_node (LLM): 1.2s, 2000 tokens
11. act_node (get_thermal_status): 0.4s
12. observe_node: 0.1s
13. decide_node (LLM): 1.5s, 2500 tokens
14. finalize_node: 0.1s

Total LLM Time: 8.3s
Total Tool Time: 1.6s
Total Tokens: 15000
```

---

## 7. 验收标准

### 7.1 第一阶段验收标准

| 检查项 | 标准 | 验证方式 |
|--------|------|---------|
| 告警触发 | 5分钟内触发VLLMLatencyP95High | Prometheus Alertmanager |
| 诊断启动 | 告警后30s内开始诊断 | 日志时间戳 |
| 根因识别 | root_cause = "GPU资源争用" | 诊断结果 |
| 置信度 | confidence >= 0.85 | 诊断结果 |
| 修复方案 | remediation_plan.tool = "kill_process" | 诊断结果 |
| Phoenix追踪 | 完整trace记录，无缺失span | Phoenix UI |
| 诊断耗时 | < 30s | 时间戳差值 |

### 7.2 第二阶段验收标准

| 检查项 | 标准 | 验证方式 |
|--------|------|---------|
| 第一阶段所有标准 | 全部满足 | - |
| 审批流程 | 高风险操作触发human_confirm | 审批日志 |
| 修复执行 | kill_process成功执行 | 执行日志 |
| 修复验证 | P95 < 500ms | Prometheus查询 |
| WAL记录 | 修复前WAL条目已写入 | WAL文件 |
| 回滚能力 | 可通过WAL回滚（如需） | 手动测试 |

---

## 8. 问题排查指南

### 8.1 常见问题

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| 告警未触发 | Prometheus规则未配置 | 检查alerting_rules.yml |
| 诊断超时 | LLM API超时 | 检查LLM连接，增加timeout |
| 工具调用失败 | Channel连接问题 | 检查SSH/K8s连接 |
| 置信度过低 | 证据不足 | 检查指标采集是否正常 |
| Phoenix无数据 | 集成未生效 | 检查LangChainInstrumentor |

### 8.2 日志查看

```bash
# SRE Agent日志
kubectl logs -n service deployment/sre-agent -f

# Phoenix日志
docker logs phoenix-container -f

# Prometheus日志
kubectl logs -n monitoring prometheus-k8s-0 -f
```

---

## 9. 附录

### 9.1 相关文档
- AIDC-auto-SRE.md - SRE Agent设计文档
- fault_injector/docs/PRD.md - fault-injector文档
- load_simulator/README.md - load-simulator使用说明

### 9.2 测试数据
- 测试记录存放目录: `sre_agent/test_records/`
- Phoenix导出: `sre_agent/test_records/phoenix_traces/`

### 9.3 联系人
- SRE Agent开发: [待填写]
- 测试环境负责人: [待填写]