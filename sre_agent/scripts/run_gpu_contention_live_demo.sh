#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-/root/workspace/.cube/bin/python}"
DEMO_CONFIG="fault_injector/gpu-utilization-high-live-demo.yaml"
DEMO_KEY="gpu_utilization_high_alert_remediation"
SCENARIO="gpu_contention"
WARMUP_SECONDS=5
MODEL=""
BASE_URL=""
OUTPUT="data/demo/gpu-utilization-high-demo-live-injected.txt"
OUTPUT_FORMAT="txt"
INJECT_PID=""

usage() {
  cat <<'EOF'
Usage: run_gpu_contention_live_demo.sh [options]

Inject gpu_burn with fault_injector and then run the live GPUUtilizationHigh demo.

Options:
  --demo-config PATH         Demo YAML path used by fault_injector and gpu_utilization_high_live_demo.py
  --demo-key NAME            Top-level demo key under --demo-config
  --scenario NAME            Fault injector scenario to run first
  --warmup-seconds N         Wait time after injection starts before diagnosis
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
}

trap cleanup INT TERM

while [[ $# -gt 0 ]]; do
  case "$1" in
    --demo-config)
      DEMO_CONFIG="$2"
      shift 2
      ;;
    --demo-key)
      DEMO_KEY="$2"
      shift 2
      ;;
    --scenario)
      SCENARIO="$2"
      shift 2
      ;;
    --warmup-seconds)
      WARMUP_SECONDS="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    --base-url)
      BASE_URL="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    --output-format)
      OUTPUT_FORMAT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

export PYTHONPATH="$REPO_ROOT"
if [[ -n "$MODEL" ]]; then
  export SRE_LLM_MODEL="$MODEL"
fi
if [[ -n "$BASE_URL" ]]; then
  export SRE_OPENAI_BASE_URL="$BASE_URL"
fi

echo "Starting fault injection..."
"$PYTHON_BIN" -m fault_injector.cli run \
  --config "$DEMO_CONFIG" \
  --scenario "$SCENARIO" \
  --no-monitor \
  --yes &
INJECT_PID=$!

sleep "$WARMUP_SECONDS"

if ! kill -0 "$INJECT_PID" 2>/dev/null; then
  echo "Fault injector exited before demo started." >&2
  wait "$INJECT_PID"
  exit 1
fi

echo "Running live demo diagnosis..."
"$PYTHON_BIN" sre_agent/scripts/gpu_utilization_high_live_demo.py \
  --demo-config "$DEMO_CONFIG" \
  --demo-key "$DEMO_KEY" \
  --output "$OUTPUT" \
  --output-format "$OUTPUT_FORMAT"

echo "Waiting for fault injector cleanup..."
wait "$INJECT_PID"
INJECT_PID=""

echo "Demo completed. Output written to $OUTPUT"
