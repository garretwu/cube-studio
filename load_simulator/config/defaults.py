"""Default YAML configuration as a string constant."""

DEFAULT_CONFIG_YAML = """\
# Load Simulator Default Configuration
session_id: ""
agents:
  - inference

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
"""
