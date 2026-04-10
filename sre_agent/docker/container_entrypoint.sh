#!/usr/bin/env bash
set -euo pipefail

cd /app

export PYTHONPATH="${PYTHONPATH:-/app}"
export BACKEND_PORT="${BACKEND_PORT:-8000}"
export FRONTEND_PORT="${FRONTEND_PORT:-8080}"
export STARTUP_CONFIG_PATH="${STARTUP_CONFIG_PATH:-/app/sre_agent/conf/config.yaml}"
export STARTUP_BACKEND_HOST="${STARTUP_BACKEND_HOST:-0.0.0.0}"
export STARTUP_BACKEND_URL_HOST="${STARTUP_BACKEND_URL_HOST:-127.0.0.1}"
export SRE_KUBECONFIG="${SRE_KUBECONFIG:-/app/sre_agent/conf/kube.conf}"
export SRE_OPENAI_API_KEY="${SRE_OPENAI_API_KEY:-container-dev-placeholder}"

mkdir -p \
  /app/data/checkpoints \
  /app/data/knowledge_db \
  /app/data/llm_logs \
  /app/data/memory \
  /app/data/wal

if [ "${SRE_OPENAI_API_KEY}" = "container-dev-placeholder" ]; then
  echo "[warn] SRE_OPENAI_API_KEY not set, using a placeholder key so the web stack can start"
fi

exec python /app/sre_agent/docker/run_web_stack.py
