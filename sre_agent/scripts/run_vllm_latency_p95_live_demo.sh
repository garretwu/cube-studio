#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-/root/workspace/.cube/bin/python}"
DEMO_CONFIG="fault_injector/vllm-latency-p95-live-demo.yaml"
DEMO_KEY="vllm_inter_token_latency_p95_alert_remediation"
SCENARIO="gpu_contention"
MODEL=""
BASE_URL=""
OUTPUT="data/demo/vllm-latency-p95-live-demo.txt"
OUTPUT_FORMAT="txt"
LOAD_CONFIG=""
LOAD_WARMUP_SECONDS=30
ALERT_WAIT_SECONDS=360
LOAD_PID=""
INJECT_PID=""

usage() {
  cat <<'EOF'
Usage: run_vllm_latency_p95_live_demo.sh [options]

Run the vLLM inter-token latency P95 demo:
1. Start load_simulator inference pressure
2. Wait for warmup
3. Start gpu_contention via fault_injector
4. Wait for alert window
5. Run live diagnosis

Options:
  --demo-config PATH         Demo YAML path
  --demo-key NAME            Top-level demo key under --demo-config
  --scenario NAME            Fault injector scenario to run
  --load-config PATH         Override load simulator config path
  --load-warmup-seconds N    Wait after starting load before injection
  --alert-wait-seconds N     Wait after injection before diagnosis
  --model NAME               Optional SRE_LLM_MODEL override
  --base-url URL             Optional SRE_OPENAI_BASE_URL override
  --output PATH              Demo output path
  --output-format json|txt   Demo output format
  -h, --help                 Show this help message
EOF
}

cleanup() {
  if [[ -n "${INJECT_PID}" ]] && kill -0 "${INJECT_PID}" 2>/dev/null; then
    echo "Stopping fault injector session..."
    kill -INT "${INJECT_PID}" 2>/dev/null || true
    wait "${INJECT_PID}" || true
  fi
  if [[ -n "${LOAD_PID}" ]] && kill -0 "${LOAD_PID}" 2>/dev/null; then
    echo "Stopping load simulator..."
    kill -INT "${LOAD_PID}" 2>/dev/null || true
    wait "${LOAD_PID}" || true
  fi
}

trap cleanup INT TERM

while [[ $# -gt 0 ]]; do
  case "$1" in
    --demo-config) DEMO_CONFIG="$2"; shift 2 ;;
    --demo-key) DEMO_KEY="$2"; shift 2 ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
    --load-config) LOAD_CONFIG="$2"; shift 2 ;;
    --load-warmup-seconds) LOAD_WARMUP_SECONDS="$2"; shift 2 ;;
    --alert-wait-seconds) ALERT_WAIT_SECONDS="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --output-format) OUTPUT_FORMAT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$LOAD_CONFIG" ]]; then
  LOAD_CONFIG="$(
    DEMO_CONFIG_PATH="$DEMO_CONFIG" "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path
import yaml
p = Path(os.environ["DEMO_CONFIG_PATH"])
raw = yaml.safe_load(p.read_text()) or {}
demo = raw.get("demo", {}).get("vllm_inter_token_latency_p95_alert_remediation", {})
print(str(demo.get("load_simulator_config") or ""))
PY
  )"
fi

if [[ -z "$LOAD_CONFIG" ]]; then
  echo "Missing load simulator config." >&2
  exit 1
fi

export PYTHONPATH="$REPO_ROOT"
if [[ -n "$MODEL" ]]; then
  export SRE_LLM_MODEL="$MODEL"
fi
if [[ -n "$BASE_URL" ]]; then
  export SRE_OPENAI_BASE_URL="$BASE_URL"
fi

echo "Starting load simulator..."
"$PYTHON_BIN" -m load_simulator run \
  --config "$LOAD_CONFIG" \
  --only inference \
  --output-format json &
LOAD_PID=$!

echo "Waiting for load warmup..."
sleep "$LOAD_WARMUP_SECONDS"

echo "Starting fault injection..."
"$PYTHON_BIN" -m fault_injector.cli run \
  --config "$DEMO_CONFIG" \
  --scenario "$SCENARIO" \
  --no-monitor \
  --yes &
INJECT_PID=$!

echo "Waiting for alert window..."
sleep "$ALERT_WAIT_SECONDS"

echo "Running live demo diagnosis..."
demo_cmd=(
  "$PYTHON_BIN"
  "sre_agent/scripts/vllm_latency_p95_live_demo.py"
  "--demo-config" "$DEMO_CONFIG"
  "--demo-key" "$DEMO_KEY"
  "--output" "$OUTPUT"
  "--output-format" "$OUTPUT_FORMAT"
)
if [[ -n "$MODEL" ]]; then
  demo_cmd+=("--model" "$MODEL")
fi
if [[ -n "$BASE_URL" ]]; then
  demo_cmd+=("--base-url" "$BASE_URL")
fi
"${demo_cmd[@]}"

echo "Waiting for fault injector cleanup..."
wait "$INJECT_PID"
INJECT_PID=""

echo "Stopping load simulator..."
if [[ -n "${LOAD_PID}" ]] && kill -0 "${LOAD_PID}" 2>/dev/null; then
  kill -INT "${LOAD_PID}" 2>/dev/null || true
  wait "${LOAD_PID}" || true
fi
LOAD_PID=""

echo "Demo completed. Output written to $OUTPUT"
