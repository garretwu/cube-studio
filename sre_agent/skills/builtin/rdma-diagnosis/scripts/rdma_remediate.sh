#!/usr/bin/env bash
# rdma_remediate.sh -- plan and optionally execute low-risk RDMA host remediations

set -euo pipefail

NODE="${SRE_RDMA_NODE:-}"
HOST="${SRE_RDMA_HOST:-}"
SSH_USER="${SRE_RDMA_SSH_USER:-}"
SSH_PORT="${SRE_RDMA_SSH_PORT:-22}"
IFACE="${SRE_RDMA_IFACE:-}"
ACTION="plan"
SYMPTOM=""
MTU="${SRE_RDMA_EXPECTED_MTU:-4200}"
EXECUTE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node)
      NODE="${2:-}"
      shift 2
      ;;
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --ssh-user)
      SSH_USER="${2:-}"
      shift 2
      ;;
    --ssh-port)
      SSH_PORT="${2:-22}"
      shift 2
      ;;
    --iface)
      IFACE="${2:-}"
      shift 2
      ;;
    --action)
      ACTION="${2:-plan}"
      shift 2
      ;;
    --symptom)
      SYMPTOM="${2:-}"
      shift 2
      ;;
    --mtu)
      MTU="${2:-4200}"
      shift 2
      ;;
    --execute)
      EXECUTE=1
      shift
      ;;
    --help|-h)
      cat <<'EOF'
Usage: bash scripts/rdma_remediate.sh [options]

Options:
  --node NODE           Logical node name for reporting.
  --host HOST           Remote SSH host. Empty means local execution.
  --ssh-user USER       Optional SSH user.
  --ssh-port PORT       SSH port, default 22.
  --iface IFACE         NIC device name. Required for executable actions.
  --action ACTION       plan | bounce-link | restore-mtu | clear-qdisc
  --symptom SYMPTOM     link-flap | mtu-mismatch | qdisc-residue | rdma-stall | switch-suspect
  --mtu MTU             Target MTU for restore-mtu, default 4200.
  --execute             Actually run the host action.
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

remote_prefix=()
if [[ -n "${HOST}" ]]; then
  remote_target="${HOST}"
  if [[ -n "${SSH_USER}" ]]; then
    remote_target="${SSH_USER}@${HOST}"
  fi
  remote_prefix=(ssh -p "${SSH_PORT}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "${remote_target}")
fi

run_cmd() {
  local cmd="$1"
  if [[ ${#remote_prefix[@]} -gt 0 ]]; then
    "${remote_prefix[@]}" "${cmd}"
  else
    bash -lc "${cmd}"
  fi
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

if [[ -z "${ACTION}" ]]; then
  ACTION="plan"
fi

case "${SYMPTOM}" in
  ""|link-flap|mtu-mismatch|qdisc-residue|rdma-stall|switch-suspect)
    ;;
  *)
    echo "unsupported symptom: ${SYMPTOM}" >&2
    exit 1
    ;;
esac

case "${ACTION}" in
  plan|bounce-link|restore-mtu|clear-qdisc)
    ;;
  *)
    echo "unsupported action: ${ACTION}" >&2
    exit 1
    ;;
esac

if [[ "${ACTION}" != "plan" && -z "${IFACE}" ]]; then
  echo "--iface is required for action ${ACTION}" >&2
  exit 1
fi

commands=()
case "${ACTION}" in
  plan)
    ;;
  bounce-link)
    commands+=("sudo ip link set dev ${IFACE} down")
    commands+=("sleep 2")
    commands+=("sudo ip link set dev ${IFACE} up")
    ;;
  restore-mtu)
    commands+=("sudo ip link set dev ${IFACE} mtu ${MTU}")
    ;;
  clear-qdisc)
    commands+=("sudo tc qdisc del dev ${IFACE} root || true")
    ;;
esac

recommended=()
case "${SYMPTOM}" in
  link-flap)
    recommended+=("Check switch counters and optics before host-side bounce")
    recommended+=("Use bounce-link only in a maintenance window because it breaks live RDMA sessions")
    ;;
  mtu-mismatch)
    recommended+=("Validate peer and switch MTU before restore-mtu")
    recommended+=("Roll back if the target workload expects a smaller MTU for testing")
    ;;
  qdisc-residue)
    recommended+=("Clear qdisc only if it came from a test and is not an intentional shaping policy")
    ;;
  rdma-stall)
    recommended+=("Start with health check evidence, then inspect switch-side PFC/ECN/QoS state")
    recommended+=("Prefer plan mode unless the issue is clearly host-side")
    ;;
  switch-suspect)
    recommended+=("Escalate to switch diagnosis for PFC, ECN, QoS, DSCP trust, or CAR policy issues")
    ;;
esac

executed_output=""
if [[ ${EXECUTE} -eq 1 && "${ACTION}" != "plan" ]]; then
  tmp_out="/tmp/rdma_remediate_$$.log"
  : > "${tmp_out}"
  for cmd in "${commands[@]}"; do
    {
      echo "\$ ${cmd}"
      run_cmd "${cmd}"
    } >> "${tmp_out}" 2>&1
  done
  executed_output="$(cat "${tmp_out}")"
  rm -f "${tmp_out}"
fi

python3 - "${NODE}" "${HOST}" "${IFACE}" "${ACTION}" "${SYMPTOM}" "${MTU}" "${EXECUTE}" <<'PY' <<<"${executed_output}"
from __future__ import annotations

import json
import sys

node = sys.argv[1] or ""
host = sys.argv[2] or ""
iface = sys.argv[3] or ""
action = sys.argv[4] or "plan"
symptom = sys.argv[5] or ""
mtu = sys.argv[6] or ""
execute = bool(int(sys.argv[7] or "0"))
stdout = sys.stdin.read()

commands = []
if action == "bounce-link":
    commands = [f"ip link set dev {iface} down", "sleep 2", f"ip link set dev {iface} up"]
elif action == "restore-mtu":
    commands = [f"ip link set dev {iface} mtu {mtu}"]
elif action == "clear-qdisc":
    commands = [f"tc qdisc del dev {iface} root"]

payload = {
    "summary": {
        "node": node or host or "local",
        "host": host or "local",
        "iface": iface or None,
        "action": action,
        "symptom": symptom or None,
        "mode": "execute" if execute and action != "plan" else "plan",
    },
    "commands": commands,
    "recommended_next_steps": [],
    "execution_output": stdout.strip(),
}

if symptom == "link-flap":
    payload["recommended_next_steps"] = [
        "Check switch counters and recent link-down logs",
        "Only bounce the link after confirming no critical job is active",
    ]
elif symptom == "mtu-mismatch":
    payload["recommended_next_steps"] = [
        "Validate peer and switch MTU before applying the host change",
        f"Restore host MTU to {mtu} only if that is the known-good baseline",
    ]
elif symptom == "qdisc-residue":
    payload["recommended_next_steps"] = [
        "Confirm the qdisc was left by a test or fault injection run",
        "Re-check tc qdisc show after cleanup",
    ]
elif symptom == "rdma-stall":
    payload["recommended_next_steps"] = [
        "Correlate host evidence with NCCL latency and switch-side congestion signals",
        "Escalate to switch diagnosis if host evidence is weak",
    ]
elif symptom == "switch-suspect":
    payload["recommended_next_steps"] = [
        "Investigate PFC, ECN, QoS, DSCP trust, or CAR policy on the switch",
        "Use fault_injector or approved switch tooling for the actual network change",
    ]

if action == "plan":
    payload["recommended_next_steps"].insert(0, "This output is plan-only; add --execute only after approval")

print(json.dumps(payload, indent=2, ensure_ascii=False))
PY
