# AIServiceTTFTP99High Demo（进程级灰度 50% -> 100%）

## 目标

演示同一节点（`10.11.4.13`）两个负载进程导致 `AIServiceTTFTP99High`，并在 Diagnosis 页面完成：

1. 诊断识别异常进程  
2. 生成两步 `kill_process` 修复方案  
3. 审批后按 canary 两批执行（50% -> 100%）  
4. 观察告警恢复

## 前置条件

- 前端使用 `Diagnosis` 页面（不是 `DiagnosisModified`）。
- `10.11.4.13` 可 SSH。
- Prometheus / Alertmanager 可访问。
- `load_simulator` 可运行。

## 演示步骤

### 1) 在 10.11.4.13 启动两个负载进程

```bash
ssh yuyonghao@10.11.4.13
cd /path/to/cube-studio

nohup bash -lc 'exec -a ls_demo_a python -m load_simulator run --config load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml --output-format json' > /tmp/ls_demo_a.log 2>&1 &
echo $! > /tmp/ls_demo_a.pid

nohup bash -lc 'exec -a ls_demo_b python -m load_simulator run --config load_simulator/config/inference-qwen3-32b-fp8-10.11.4.3-20060.yaml --output-format json' > /tmp/ls_demo_b.log 2>&1 &
echo $! > /tmp/ls_demo_b.pid
```

验证：

```bash
ps -ef | grep -E 'ls_demo_a|ls_demo_b|load_simulator' | grep -v grep
```

### 2) 等待告警触发

- 在告警页确认 `AIServiceTTFTP99High` 为 `firing`。

### 3) 进入 Diagnosis 执行诊断

- 从 Alerts 点击进入 `Diagnosis`。
- 发送诊断请求并等待流式结果。
- 关注证据链是否出现：
  - `service -> pod -> node`
  - `gpu.get_processes`
  - 识别到两个可疑负载进程

### 4) 审批执行修复（灰度）

- 审批通过后，时间线应出现：
  - `canary_batch_started`（批次 1）
  - `canary_batch_completed`（批次 1）
  - `canary_batch_started`（批次 2）
  - `canary_batch_completed`（批次 2）

对应含义：

- 批次 1：终止第一进程（50%）
- 批次 2：终止第二进程（100%）

### 5) 观察阶段验收

- 时间线出现 `observation_result`。
- 验收口径：
  - `alert_cleared=true`
  - 会话状态收敛到 `resolved`

## 失败回收

若演示中断，手动清理：

```bash
ssh yuyonghao@10.11.4.13
if [ -f /tmp/ls_demo_a.pid ]; then kill -TERM "$(cat /tmp/ls_demo_a.pid)" || true; fi
if [ -f /tmp/ls_demo_b.pid ]; then kill -TERM "$(cat /tmp/ls_demo_b.pid)" || true; fi
pkill -f ls_demo_a || true
pkill -f ls_demo_b || true
pkill -f load_simulator || true
```
