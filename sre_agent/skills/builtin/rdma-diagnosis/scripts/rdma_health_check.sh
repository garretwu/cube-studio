#!/usr/bin/env bash
# rdma_health_check.sh -- collect RDMA/RoCE host evidence and emit JSON findings

set -euo pipefail

NODE="${SRE_RDMA_NODE:-}"
HOST="${SRE_RDMA_HOST:-}"
SSH_USER="${SRE_RDMA_SSH_USER:-}"
SSH_PORT="${SRE_RDMA_SSH_PORT:-22}"
IFACE="${SRE_RDMA_IFACE:-}"
HCA="${SRE_RDMA_HCA:-mlx5_0}"
EXPECTED_MTU="${SRE_RDMA_EXPECTED_MTU:-4200}"
OUTPUT_DIR=""

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
    --hca)
      HCA="${2:-mlx5_0}"
      shift 2
      ;;
    --expected-mtu)
      EXPECTED_MTU="${2:-4200}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --help|-h)
      cat <<'EOF'
Usage: bash scripts/rdma_health_check.sh [options]

Options:
  --node NODE           Logical node name for reporting.
  --host HOST           Remote SSH host. Empty means local execution.
  --ssh-user USER       Optional SSH user.
  --ssh-port PORT       SSH port, default 22.
  --iface IFACE         NIC device name, e.g. roce0 or eth1.
  --hca HCA             HCA name, default mlx5_0.
  --expected-mtu MTU    Expected RDMA MTU, default 4200.
  --output-dir DIR      Optional output directory.
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "${OUTPUT_DIR}" ]]; then
  OUTPUT_DIR="/tmp/rdma_health_$(hostname)_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "${OUTPUT_DIR}"

REPORT="${OUTPUT_DIR}/rdma_health_report.json"
RAW="${OUTPUT_DIR}/raw_logs.txt"

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
    "${remote_prefix[@]}" "${cmd}" 2>&1 || true
  else
    bash -lc "${cmd}" 2>&1 || true
  fi
}

capture() {
  local title="$1"
  local cmd="$2"
  {
    echo "### ${title}"
    echo "\$ ${cmd}"
    run_cmd "${cmd}"
    echo
  } >> "${RAW}"
}

{
  echo "# rdma health check"
  echo "# node=${NODE:-unknown}"
  echo "# host=${HOST:-local}"
  echo "# iface=${IFACE:-auto}"
  echo "# hca=${HCA}"
  echo "# generated_at=$(date -Iseconds)"
  echo
} > "${RAW}"

capture "hostname" "hostname"
capture "rdma_link_show" "command -v rdma >/dev/null 2>&1 && rdma link show || echo 'rdma command not found'"
capture "ibdev2netdev" "command -v ibdev2netdev >/dev/null 2>&1 && ibdev2netdev || echo 'ibdev2netdev not found'"
capture "ibstat" "command -v ibstat >/dev/null 2>&1 && ibstat || echo 'ibstat not found'"
capture "hca_counters" "for f in /sys/class/infiniband/${HCA}/ports/1/counters/*; do [ -f \"\$f\" ] && echo \"\$(basename \"\$f\")=\$(cat \"\$f\")\"; done"
capture "ip_link" "ip -s link show ${IFACE:+dev ${IFACE}}"
capture "iface_mtu_operstate" "[ -n '${IFACE}' ] && { echo mtu=\$(cat /sys/class/net/${IFACE}/mtu 2>/dev/null || echo unknown); echo operstate=\$(cat /sys/class/net/${IFACE}/operstate 2>/dev/null || echo unknown); } || echo 'iface not provided'"
capture "ethtool" "[ -n '${IFACE}' ] && command -v ethtool >/dev/null 2>&1 && ethtool ${IFACE} || echo 'ethtool unavailable or iface missing'"
capture "ethtool_stats" "[ -n '${IFACE}' ] && command -v ethtool >/dev/null 2>&1 && ethtool -S ${IFACE} || echo 'ethtool stats unavailable or iface missing'"
capture "tc_qdisc" "tc qdisc show ${IFACE:+dev ${IFACE}}"
capture "softnet_stat" "head -n 4 /proc/net/softnet_stat"
capture "dmesg_rdma" "dmesg -T 2>/dev/null | grep -iE 'mlx5|rdma|roce|infiniband|NETDEV WATCHDOG|link down|tx timeout|cq error|completion timeout' | tail -120"

python3 - "${REPORT}" "${RAW}" "${NODE}" "${HOST}" "${IFACE}" "${HCA}" "${EXPECTED_MTU}" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
raw_path = Path(sys.argv[2])
node = sys.argv[3] or ""
host = sys.argv[4] or ""
iface = sys.argv[5] or ""
hca = sys.argv[6] or ""
expected_mtu = int(sys.argv[7] or "4200")

sections: dict[str, str] = {}
current = None
buf: list[str] = []
for raw_line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
    if raw_line.startswith("### "):
        if current is not None:
            sections[current] = "\n".join(buf).strip()
        current = raw_line[4:].strip()
        buf = []
        continue
    if current is not None:
        buf.append(raw_line)
if current is not None:
    sections[current] = "\n".join(buf).strip()

findings: list[dict[str, str]] = []
recommended_actions: list[str] = []

rdma_link = sections.get("rdma_link_show", "")
ibdev2netdev = sections.get("ibdev2netdev", "")
iface_info = sections.get("iface_mtu_operstate", "")
qdisc = sections.get("tc_qdisc", "")
dmesg = sections.get("dmesg_rdma", "")
ethtool_stats = sections.get("ethtool_stats", "")

if "rdma command not found" in rdma_link:
    findings.append({"code": "rdma_tool_missing", "severity": "warn", "message": "rdma userspace tools are not installed"})

if iface and "operstate=" in iface_info:
    oper = ""
    mtu = None
    for line in iface_info.splitlines():
        if line.startswith("operstate="):
            oper = line.split("=", 1)[1].strip()
        if line.startswith("mtu="):
            try:
                mtu = int(line.split("=", 1)[1].strip())
            except ValueError:
                mtu = None
    if oper and oper != "up":
        findings.append({"code": "link_down", "severity": "critical", "message": f"interface {iface} operstate={oper}"})
        recommended_actions.append("Validate cable/switch state and consider controlled link bounce")
    if mtu is not None and mtu < expected_mtu:
        findings.append({"code": "mtu_too_small", "severity": "warn", "message": f"interface {iface} mtu={mtu}, expected>={expected_mtu}"})
        recommended_actions.append(f"Verify peer and switch MTU, then restore host MTU to {expected_mtu} if appropriate")

if iface and qdisc and "noqueue" not in qdisc and "fq_codel" not in qdisc:
    findings.append({"code": "qdisc_present", "severity": "warn", "message": f"custom qdisc detected on {iface}"})
    recommended_actions.append("Review whether qdisc residue is intentional before clearing it")

if ibdev2netdev and "Up" not in ibdev2netdev and "not found" not in ibdev2netdev:
    findings.append({"code": "rdma_link_not_active", "severity": "warn", "message": "ibdev2netdev output does not show an active RDMA netdevice"})

if re.search(r"link down|tx timeout|NETDEV WATCHDOG|mlx5.*error|cq error|completion timeout", dmesg, re.IGNORECASE):
    findings.append({"code": "link_flap_log", "severity": "critical", "message": "kernel log contains RDMA/NIC instability signals"})
    recommended_actions.append("Correlate with switch counters and recent NIC resets before retrying workloads")

error_matches = re.findall(r"(rx_errors|tx_errors|rx_dropped|tx_dropped)\s*:\s*([0-9]+)", ethtool_stats)
error_totals: dict[str, int] = {}
for key, value in error_matches:
    error_totals[key] = error_totals.get(key, 0) + int(value)
for key, value in error_totals.items():
    if value > 0:
        findings.append({"code": "nic_errors", "severity": "warn", "message": f"{key}={value}"})
        break

if not findings:
    findings.append({"code": "no_host_side_smoking_gun", "severity": "info", "message": "no clear host-side RDMA issue detected from this snapshot"})
    recommended_actions.append("If symptoms persist, investigate switch-side PFC, ECN, QoS, or DSCP trust configuration")

switch_suspect = any(item["code"] in {"no_host_side_smoking_gun"} for item in findings)
summary = {
    "node": node or host or "local",
    "host": host or "local",
    "iface": iface or None,
    "hca": hca or None,
    "status": "needs_attention" if any(item["severity"] in {"critical", "warn"} for item in findings) else "healthy",
    "switch_suspect": switch_suspect,
}

payload = {
    "summary": summary,
    "findings": findings,
    "recommended_actions": recommended_actions,
    "artifacts": {
        "raw_log": str(raw_path),
    },
    "sections": sections,
}
report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(payload, indent=2, ensure_ascii=False))
PY
