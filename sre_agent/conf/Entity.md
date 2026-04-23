# Lab 环境清单

> 自动生成自 `conf/live_inventory.lab.yaml`，更新日期：2026-04-15

后续需要将明文密码改为密钥仓库

---

## 1. GPU 服务器

| 名称 | 角色 | SSH IP | SSH 端口 | SSH 用户 | SSH 密码 | BMC IP | BMC 用户 | BMC 密码 |
|------|------|--------|---------|---------|---------|--------|---------|---------|
| wj-lab-cpt-01 | gpu | 10.11.4.10 | 22 | yuyonghao | Yuyonghao@123 | 10.11.8.10 | admin | Admin@9000 |
| wj-lab-cpt-02 | gpu | 10.11.4.11 | 22 | yuyonghao | Yuyonghao@123 | 10.11.8.11 | admin | Admin@9000 |
| wj-lab-cpt-03 | gpu | 10.11.4.12 | 22 | yuyonghao | Yuyonghao@123 | 10.11.8.12 | admin | Admin@9000 |
| wj-lab-cpt-04 | gpu | 10.11.4.13 | 22 | yuyonghao | Yuyonghao@123 | 10.11.8.13 | admin | Admin@9000 |

## 2. CPU 服务器

| 名称 | 角色 | SSH IP | SSH 端口 | SSH 用户 | SSH 密码 | BMC IP | BMC 用户 | BMC 密码 |
|------|------|--------|---------|---------|---------|--------|---------|---------|
| wj-lab-ctl-01 | cpu | 10.11.4.2 | 22 | yuyonghao | Yuyonghao@123 | 10.11.8.2 | admin | Admin@9000 |
| wj-lab-ctl-02 | cpu | 10.11.4.3 | 22 | yuyonghao | Yuyonghao@123 | 10.11.8.3 | admin | Admin@9000 |
| wj-lab-ctl-03 | cpu | 10.11.4.4 | 22 | yuyonghao | Yuyonghao@123 | 10.11.8.4 | admin | Admin@9000 |

## 3. 存储服务器

| 名称 | 角色 | SSH IP | SSH 端口 | SSH 用户 | SSH 密码 | BMC IP | BMC 用户 | BMC 密码 |
|------|------|--------|---------|---------|---------|--------|---------|---------|
| wj-lab-stor-01 | storage | 10.11.4.20 | 22 | yuyonghao | a | 10.11.8.20 | admin | Admin@9000 |
| wj-lab-stor-02 | storage | 10.11.4.21 | 22 | yuyonghao | a | 10.11.8.21 | admin | Admin@9000 |
| wj-lab-stor-03 | storage | 10.11.4.22 | 22 | yuyonghao | a | 10.11.8.22 | admin | Admin@9000 |

| 名称 | 型号 | 管理IP | 端口 | 用户名 | 密码 | 说明 |
|------|------|--------|------|--------|------|------|
| sw-25g | H3C S9855-24B8D | 10.11.8.51 | 830 | apitest | ApiTest@2026 | 25G 数据网络交换机 |
| sw-200g | H3C S9855-24B8D | 10.11.8.52 | 830 | apitest | ApiTest@2026 | 200G 数据网络交换机 |

## 4. 网络拓扑

### 交换机与节点连接关系

| 交换机 | 端口 | 连接节点 | 网卡 | 速率 |
|--------|------|---------|------|------|
| sw-200g | 200GE1/0/1 | wj-lab-cpt-01 | roce200 | 200G |
| sw-200g | 200GE1/0/2 | wj-lab-cpt-02 | roce200 | 200G |
| sw-200g | 200GE1/0/3 | wj-lab-cpt-03 | roce200 | 200G |
| sw-200g | 200GE1/0/4 | wj-lab-cpt-04 | roce200 | 200G |
| sw-200g | 200GE1/0/13 | wj-lab-stor-01 | roce200 | 200G |
| sw-200g | 200GE1/0/14 | wj-lab-stor-02 | roce200 | 200G |
| sw-200g | 200GE1/0/15 | wj-lab-stor-03 | roce200 | 200G |
| sw-25g | WGE1/0/1 | wj-lab-cpt-01 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/2 | wj-lab-cpt-02 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/3 | wj-lab-cpt-03 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/4 | wj-lab-cpt-04 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/5 | wj-lab-ctl-01 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/6 | wj-lab-ctl-02 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/7 | wj-lab-ctl-03 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/8 | wj-lab-stor-01 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/9 | wj-lab-stor-02 | ens2f0np0 | 25G |
| sw-25g | WGE1/0/10 | wj-lab-stor-03 | ens2f0np0 | 25G |

> - **sw-200g**: 连接所有 GPU 节点 + 存储节点，通过 `roce200` 网卡
> - **sw-25g**: 连接所有节点（GPU/CPU/Storage），通过 `ens2f0np0` 网卡

### 节点网络接口汇总

| 节点 | 角色 | 200G (roce200) | 25G (ens2f0np0) |
|------|------|---------------|-----------------|
| wj-lab-cpt-01 | gpu | sw-200g:200GE1/0/1 | sw-25g:WGE1/0/1 |
| wj-lab-cpt-02 | gpu | sw-200g:200GE1/0/2 | sw-25g:WGE1/0/2 |
| wj-lab-cpt-03 | gpu | sw-200g:200GE1/0/3 | sw-25g:WGE1/0/3 |
| wj-lab-cpt-04 | gpu | sw-200g:200GE1/0/4 | sw-25g:WGE1/0/4 |
| wj-lab-ctl-01 | cpu | — | sw-25g:WGE1/0/5 |
| wj-lab-ctl-02 | cpu | — | sw-25g:WGE1/0/6 |
| wj-lab-ctl-03 | cpu | — | sw-25g:WGE1/0/7 |
| wj-lab-stor-01 | storage | sw-200g:200GE1/0/13 | sw-25g:WGE1/0/8 |
| wj-lab-stor-02 | storage | sw-200g:200GE1/0/14 | sw-25g:WGE1/0/9 |
| wj-lab-stor-03 | storage | sw-200g:200GE1/0/15 | sw-25g:WGE1/0/10 |

## 5. 监控服务

| 服务 | 地址 | 说明 |
|------|------|------|
| Prometheus | http://10.11.4.3:31260 | 指标采集与查询 |

### 基线监控指标

| 指标名 | PromQL |
|--------|--------|
| CPU 利用率 | `avg(1 - rate(node_cpu_seconds_total{mode="idle"}[5m]))` |
| 内存利用率 | `avg(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))` |
| GPU 利用率 | `avg(DCGM_FI_DEV_GPU_UTIL)` |
| GPU 显存使用(MB) | `avg(DCGM_FI_DEV_FB_USED)` |
| GPU 功耗(W) | `avg(DCGM_FI_DEV_POWER_USAGE)` |

### LLM 服务指标（vLLM）

| 分类 | 指标名 | 说明 |
|------|--------|------|
| **延迟** | `vllm:time_to_first_token_seconds` | 首 Token 延迟 (TTFT)，含 bucket/count/sum |
| | `vllm:e2e_request_latency_seconds` | 端到端请求延迟 |
| | `vllm:inter_token_latency_seconds` | Token 间延迟 (TPOT) |
| | `vllm:request_prefill_time_seconds` | Prefill 阶段耗时 |
| | `vllm:request_decode_time_seconds` | Decode 阶段耗时 |
| | `vllm:request_queue_time_seconds` | 请求排队等待时间 |
| | `vllm:request_inference_time_seconds` | 推理总耗时 |
| **吞吐** | `vllm:prompt_tokens_total` | 处理的 prompt token 总数 (counter) |
| | `vllm:generation_tokens_total` | 生成的 token 总数 (counter) |
| | `vllm:prompt_tokens_cached_total` | 命中缓存的 prompt token 数 |
| | `vllm:request_success_total` | 成功请求数 (counter) |
| **并发** | `vllm:num_requests_running` | 当前运行中的请求数 (gauge) |
| | `vllm:num_requests_waiting` | 当前排队等待的请求数 (gauge) |
| | `vllm:num_preemptions_total` | 请求被抢占次数 (counter) |
| **缓存** | `vllm:kv_cache_usage_perc` | KV Cache 使用率百分比 |
| | `vllm:prefix_cache_hits_total` / `queries_total` | 前缀缓存命中数 / 查询数 |
| | `vllm:external_prefix_cache_hits_total` / `queries_total` | 外部前缀缓存命中 / 查询数 |
| **GPU 资源** | `vllm:estimated_flops_per_gpu_total` | 每 GPU 估算 FLOPS |
| | `vllm:estimated_read_bytes_per_gpu_total` | 每 GPU 估算读取字节数 |
| | `vllm:estimated_write_bytes_per_gpu_total` | 每 GPU 估算写入字节数 |
| **跨节点** | `vllm:nixl_bytes_transferred` | NIXL 跨节点传输字节数 |
| | `vllm:nixl_num_failed_transfers_total` | NIXL 传输失败次数 |

### GPU 硬件指标（DCGM）

| 指标名 | 说明 |
|--------|------|
| `DCGM_FI_DEV_GPU_UTIL` | GPU 计算利用率 |
| `DCGM_FI_DEV_GPU_TEMP` | GPU 核心温度 |
| `DCGM_FI_DEV_MEMORY_TEMP` | GPU 显存温度 |
| `DCGM_FI_DEV_FB_USED` / `FB_FREE` | 显存已用 / 空闲 (MB) |
| `DCGM_FI_DEV_POWER_USAGE` | GPU 功耗 (W) |
| `DCGM_FI_DEV_SM_CLOCK` | SM 时钟频率 |
| `DCGM_FI_DEV_MEM_CLOCK` | 显存时钟频率 |
| `DCGM_FI_DEV_MEM_COPY_UTIL` | 显存拷贝引擎利用率 |
| `DCGM_FI_DEV_PCIE_REPLAY_COUNTER` | PCIe 重传计数 |
| `DCGM_FI_PROF_GR_ENGINE_ACTIVE` | GPU 引擎活跃比率 |
| `DCGM_FI_PROF_PCIE_RX_BYTES` / `TX_BYTES` | PCIe RX/TX 字节数 |
| `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` | Tensor Pipe 活跃比率 |
| `DCGM_FI_PROF_DRAM_ACTIVE` | DRAM 活跃比率 |
| `gpu_inventory_present` | GPU 卡在位状态 (1=在位, 0=缺失) |
| `gpu_inventory_collect_success` | GPU 信息采集是否成功 (1=成功) |

## 6. 重点告警规则

### AIServiceTTFTP99High

- **严重级别:** warning
- **关联指标:** `vllm:time_to_first_token_seconds_bucket`
- **触发条件:** TTFT P99 > 500ms 持续 5 分钟
- **表达式:**
  ```promql
  histogram_quantile(0.99, sum by(le, namespace, service, model_name)
    (rate(vllm:time_to_first_token_seconds_bucket[5m]))) > 0.5
  ```
- **当前状态:** FIRING — Qwen3-32B-FP8 TTFT P99 = **79.22s**

### GPUCardMissing

- **严重级别:** critical
- **关联指标:** `gpu_inventory_present`, `gpu_inventory_collect_success`
- **触发条件:** GPU 卡在 30 分钟内从在位变为缺失，且采集仍然成功
- **表达式:**
  ```promql
  (max by(instance, gpu_uuid) (max_over_time(gpu_inventory_present{job="node-exporter"}[30m]) > 0))
  unless on(instance, gpu_uuid) (max by(instance, gpu_uuid) (gpu_inventory_present{job="node-exporter"} > 0))
  and on(instance) (gpu_inventory_collect_success{job="node-exporter"} == 1)
  and on(instance) (node_textfile_scrape_error{job="node-exporter"} == 0)
  ```

### GPUTemperatureHigh

- **严重级别:** critical
- **关联指标:** `DCGM_FI_DEV_GPU_TEMP`
- **触发条件:** GPU 核心温度 > 85°C
- **表达式:**
  ```promql
  DCGM_FI_DEV_GPU_TEMP > 85
  ```

### GPUTemperatureHighWjLabCpt04

- **严重级别:** warning
- **关联指标:** `DCGM_FI_DEV_GPU_TEMP`
- **触发条件:** wj-lab-cpt-04 节点 GPU 温度 > 50°C（低阈值预警）
- **表达式:**
  ```promql
  DCGM_FI_DEV_GPU_TEMP{Hostname="wj-lab-cpt-04"} > 50
  ```

## 7. 当前活跃告警（2026-04-16 快照）

| 告警名 | 严重级别 | 状态 | 描述 |
|--------|---------|------|------|
| AIServiceTTFTP99High | warning | firing | Qwen3-32B-FP8 TTFT P99=79.22s (阈值 500ms) |
| KubeClientErrors | warning | firing | kubelet/10.11.4.12 API 错误率 ~3% |
| KubeClientErrors | warning | firing | kubernetes-nodes/wj-lab-cpt-03 API 错误率 ~3% |
| AlertmanagerDown | critical | firing | Alertmanager 从服务发现中消失 |
| KubeControllerManagerDown | critical | firing | KubeControllerManager 从服务发现中消失 |
| KubeSchedulerDown | critical | firing | KubeScheduler 从服务发现中消失 |
| PrometheusOperatorDown | critical | firing | PrometheusOperator 从服务发现中消失 |
| TargetDown (kube-scheduler) | warning | firing | kube-scheduler 目标 100% down |
| TargetDown (kube-controller-manager) | warning | firing | kube-controller-manager 目标 100% down |
| TargetDown (external-node-exporter) | warning | firing | 50% external-node-exporter 目标 down |
| TargetDown (external-model) | warning | firing | 100% external-model 目标 down |
| TargetDown (telemetry-exporter) | warning | pending | 100% telemetry-exporter 目标 down |

## 8. IP 网段汇总

| 网段 | 用途 |
|------|------|
| 10.11.4.x | 服务器 SSH / 业务网络 |
| 10.11.0.x | 服务器    / RDMA网络 |
| 10.11.8.x | BMC 管理 / 交换机管理网络 |
