"""Default YAML configuration as a string constant."""

DEFAULT_CONFIG_YAML = """\
# Load Simulator Default Configuration
session_id: ""
agents:
  - inference
mode: "single"

global_config:
  cube_studio_url: "http://localhost"
  prometheus_url: "http://localhost:9090"
  project_id: 1
  auth_method: "username"
  auth_username: "admin"
  jwt_password: ""

channels:
  cube_studio:
    timeout: 30
    retry_count: 3
    retry_backoff: 1.0
  inference:
    timeout: 60
    retry_count: 1
    retry_backoff: 0.5
  prometheus:
    timeout: 15
    retry_count: 1
    retry_backoff: 0.5
  notebook:
    timeout: 30
    retry_count: 1
    retry_backoff: 0.5

system_capacity:
  max_task_cpu: 50
  max_task_mem_gib: 100
  db_pool_size: 300
  db_max_overflow: 800

inference:
  endpoint: "http://localhost:8000/v1/chat/completions"
  model: "deepseek-v3"
  concurrency: 10
  duration_seconds: 60
  max_tokens: 512
  prompt_pool_size: 100

pipeline:
  cube_studio_url: "http://localhost"
  pipeline_id: ""
  concurrency: 2
  duration_seconds: 120

finetune:
  llama_factory_url: "http://localhost:7860"
  duration_seconds: 60

notebook:
  jupyter_url: "http://localhost:8888"
  token: ""
  duration_seconds: 60

bottleneck_analysis: true
output_dir: "report"

adaptive_rules:
  pause_error_rate: 0.05
  breaking_error_rate: 0.10
  p99_breaking_multiplier: 10.0
  gpu_mem_reduce_pct: 95.0
  cpu_pause_pct: 95.0

prometheus_queries:
  enabled: false
  collection_interval_seconds: 15
  pod_cpu: 'sum(rate(container_cpu_usage_seconds_total{namespace=~"$NS"}[1m]))'
  pod_memory: 'sum(container_memory_working_set_bytes{namespace=~"$NS"})'
  gpu_utilization: "DCGM_FI_DEV_GPU_UTIL"
  gpu_memory: "DCGM_FI_DEV_MEM_COPY_UTIL"
  istio_qps: 'sum(rate(istio_requests_total{namespace=~"$NS"}[1m]))'
"""
