#!/bin/bash
# gpu_health_check.sh — RTX 5090集群健康检查脚本
# 用法: bash gpu_health_check.sh [output_dir]
# 输出: JSON格式健康报告 + 告警分级
#
# 针对消费级GPU集群优化：
# - 无ECC查询（GDDR7无硬件ECC）
# - 无NVLink检查（RTX 5090不支持）
# - 增加PCIe状态检查（多GPU通信关键路径）
# - 增加温度/功耗重点监控（消费散热限制）
# - 增加GSP固件状态检查（Blackwell已知问题）

set -euo pipefail

EXPECTED_GPUS=4  # 每节点4张RTX 5090，按实际修改
TEMP_WARN=80     # 温度预警阈值(°C)
TEMP_CRIT=85     # 温度严重阈值(°C)
PCIE_EXPECTED_SPEED="32GT/s"   # PCIe 5.0
PCIE_EXPECTED_WIDTH="x16"

NODE_OVERRIDE=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node)
            NODE_OVERRIDE="${2:-}"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="${2:-}"
            shift 2
            ;;
        --help|-h)
            cat <<'EOF'
Usage: bash gpu_health_check.sh [--node NODE] [--output-dir DIR] [DIR]

Options:
  --node NODE        Optional node identifier for logging context.
  --output-dir DIR   Explicit output directory for report files.
  DIR                Legacy positional output directory.
EOF
            exit 0
            ;;
        *)
            if [[ -z "${OUTPUT_DIR}" ]]; then
                OUTPUT_DIR="$1"
            fi
            shift
            ;;
    esac
done

OUTPUT_DIR="${OUTPUT_DIR:-/tmp/gpu_health_$(hostname)_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$OUTPUT_DIR"

REPORT="$OUTPUT_DIR/health_report.json"
LOG="$OUTPUT_DIR/raw_logs.txt"

echo "=== GPU Health Check (RTX 5090 Cluster) ===" | tee "$LOG"
echo "=== Host: $(hostname) | Time: $(date -Iseconds) ===" | tee -a "$LOG"
if [[ -n "$NODE_OVERRIDE" ]]; then
    echo "=== Requested Node Context: ${NODE_OVERRIDE} ===" | tee -a "$LOG"
fi

json_escape() {
    python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo '""'
}

# ---------- 1. Basic GPU Info ----------
echo "[1/9] GPU basic info..." | tee -a "$LOG"
GPU_INFO=$(nvidia-smi --query-gpu=index,name,serial,uuid,driver_version,temperature.gpu,power.draw,power.limit,memory.used,memory.total,pstate,clocks.current.sm,clocks.max.sm,fan.speed --format=csv,noheader 2>&1) || GPU_INFO="ERROR: nvidia-smi failed ($?)"
echo "$GPU_INFO" >> "$LOG"

# ---------- 2. GPU Count ----------
echo "[2/9] GPU count verification..." | tee -a "$LOG"
SMI_COUNT=$(nvidia-smi -L 2>/dev/null | wc -l || echo "0")
PCIE_COUNT=$(lspci -d 10de: 2>/dev/null | grep -ciE "3D|VGA|Display|processing" || echo "0")
echo "nvidia-smi: $SMI_COUNT, PCIe: $PCIE_COUNT, Expected: $EXPECTED_GPUS" >> "$LOG"

# ---------- 3. XID Errors ----------
echo "[3/9] XID errors (last 24h)..." | tee -a "$LOG"
XID_ERRORS=$(dmesg -T 2>/dev/null | grep -iE "xid|nvrm.*error|GSP.*heartbeat|CTX.SWITCH|gpu.*fallen" | tail -80 2>&1) || XID_ERRORS=""
echo "$XID_ERRORS" >> "$LOG"

# ---------- 4. PCIe Link Status ----------
echo "[4/9] PCIe link status..." | tee -a "$LOG"
PCIE_LINK=$(lspci -d 10de: -vvv 2>/dev/null | grep -E "^[0-9a-f]|LnkSta:|LnkCap:" | head -40 2>&1) || PCIE_LINK="ERROR: lspci failed"
echo "$PCIE_LINK" >> "$LOG"

# ---------- 5. PCIe AER Errors ----------
echo "[5/9] PCIe AER errors..." | tee -a "$LOG"
AER_ERRORS=$(dmesg -T 2>/dev/null | grep -iE "aer|pcie.*error|link.*down" | tail -30 2>&1) || AER_ERRORS=""
echo "$AER_ERRORS" >> "$LOG"

# ---------- 6. GPU Topology ----------
echo "[6/9] GPU topology..." | tee -a "$LOG"
TOPO=$(nvidia-smi topo -m 2>&1) || TOPO="ERROR: topo query failed"
echo "$TOPO" >> "$LOG"

# ---------- 7. Temperature & Power Snapshot ----------
echo "[7/9] Temperature & power snapshot..." | tee -a "$LOG"
THERMAL=$(nvidia-smi --query-gpu=index,temperature.gpu,power.draw,power.limit,clocks.current.sm,clocks.max.sm,fan.speed,pstate --format=csv,noheader 2>&1) || THERMAL="ERROR"
echo "$THERMAL" >> "$LOG"

# ---------- 8. GSP Firmware Status ----------
echo "[8/9] GSP firmware check..." | tee -a "$LOG"
GSP_INFO=$(dmesg 2>/dev/null | grep -iE "GSP|firmware|gsp-rm" | tail -20 2>&1) || GSP_INFO=""
echo "$GSP_INFO" >> "$LOG"

# ---------- 9. System Info ----------
echo "[9/9] System info..." | tee -a "$LOG"
SYS_KERNEL=$(uname -r)
SYS_DRIVER=$(cat /proc/driver/nvidia/version 2>/dev/null | head -1 || echo "unknown")
SYS_UPTIME=$(uptime -p 2>/dev/null || uptime)
echo "Kernel: $SYS_KERNEL | Driver: $SYS_DRIVER | Uptime: $SYS_UPTIME" >> "$LOG"

# ---------- Generate JSON + Alerts ----------
python3 - "$REPORT" "$GPU_INFO" "$XID_ERRORS" "$PCIE_LINK" "$AER_ERRORS" "$THERMAL" "$GSP_INFO" "$SMI_COUNT" "$PCIE_COUNT" "$EXPECTED_GPUS" "$TEMP_WARN" "$TEMP_CRIT" "$PCIE_EXPECTED_SPEED" "$PCIE_EXPECTED_WIDTH" "$SYS_KERNEL" "$SYS_DRIVER" << 'PYEOF'
import json, sys, re, os
from datetime import datetime

report_path = sys.argv[1]
gpu_info = sys.argv[2]
xid_errors = sys.argv[3]
pcie_link = sys.argv[4]
aer_errors = sys.argv[5]
thermal = sys.argv[6]
gsp_info = sys.argv[7]
smi_count = int(sys.argv[8])
pcie_count = int(sys.argv[9])
expected = int(sys.argv[10])
temp_warn = int(sys.argv[11])
temp_crit = int(sys.argv[12])
pcie_speed = sys.argv[13]
pcie_width = sys.argv[14]
kernel = sys.argv[15]
driver = sys.argv[16]

alerts = []

# --- GPU count ---
if smi_count < expected:
    if pcie_count < expected:
        alerts.append({
            "level": "FATAL", "type": "GPU_MISSING_PCIE",
            "message": f"Only {pcie_count}/{expected} GPUs visible on PCIe (XID 79 likely)",
            "action": "PHASE_2A", "gpu": "unknown"
        })
    else:
        alerts.append({
            "level": "CRITICAL", "type": "GPU_MISSING_DRIVER",
            "message": f"PCIe sees {pcie_count} GPUs but nvidia-smi sees only {smi_count}",
            "action": "PHASE_3A", "gpu": "unknown"
        })

if gpu_info.startswith("ERROR"):
    alerts.append({
        "level": "FATAL", "type": "NVIDIA_SMI_FAILED",
        "message": gpu_info,
        "action": "PHASE_3A", "gpu": "all"
    })

# --- XID errors ---
if xid_errors.strip():
    xid_matches = re.findall(r'Xid.*?:\s*(\d+)', xid_errors, re.IGNORECASE)
    xid_counts = {}
    for code in xid_matches:
        xid_counts[int(code)] = xid_counts.get(int(code), 0) + 1

    for code, count in xid_counts.items():
        if code == 79:
            alerts.append({"level": "FATAL", "type": "XID_79",
                "message": f"GPU fallen off bus (count: {count})", "action": "PHASE_2A", "gpu": "check_dmesg"})
        elif code in (119,):
            alerts.append({"level": "CRITICAL", "type": "XID_119",
                "message": f"GSP heartbeat timeout (count: {count}) — Blackwell known issue",
                "action": "PHASE_2B_GSP", "gpu": "check_dmesg"})
        elif code in (109,):
            alerts.append({"level": "CRITICAL", "type": "XID_109",
                "message": f"CTX SWITCH TIMEOUT (count: {count}) — likely GSP cascade",
                "action": "PHASE_2B_GSP", "gpu": "check_dmesg"})
        elif code in (43, 45):
            alerts.append({"level": "CRITICAL", "type": f"XID_{code}",
                "message": f"GPU watchdog timeout (count: {count})",
                "action": "PHASE_2C_WATCHDOG", "gpu": "check_dmesg"})
        elif code == 62:
            alerts.append({"level": "CRITICAL", "type": "XID_62",
                "message": f"Thermal violation (count: {count})",
                "action": "PHASE_2E_THERMAL", "gpu": "check_dmesg"})
        elif code in (31, 13):
            severity = "CRITICAL" if count > 3 else "WARNING"
            alerts.append({"level": severity, "type": f"XID_{code}",
                "message": f"CUDA error XID {code} (count: {count})",
                "action": "PHASE_2D_CUDA" if count > 3 else "MONITOR", "gpu": "check_dmesg"})

# --- GSP issues ---
if gsp_info.strip():
    if re.search(r'heartbeat.*timeout|GSP.*error|bootstrap.*fail', gsp_info, re.IGNORECASE):
        # Only add if not already captured via XID
        if not any(a['type'].startswith('XID_119') or a['type'].startswith('XID_109') for a in alerts):
            alerts.append({"level": "WARNING", "type": "GSP_ISSUE",
                "message": "GSP firmware issues detected in dmesg",
                "action": "PHASE_2B_GSP", "gpu": "check_dmesg"})

# --- Temperature ---
for line in thermal.strip().split('\n'):
    if line.startswith('ERROR') or not line.strip():
        continue
    parts = [p.strip() for p in line.split(',')]
    if len(parts) >= 2:
        try:
            gpu_idx = parts[0]
            temp = int(parts[1].replace(' C','').replace('°','').strip())
            if temp >= temp_crit:
                alerts.append({"level": "CRITICAL", "type": "TEMP_CRITICAL",
                    "message": f"GPU {gpu_idx}: {temp}°C >= {temp_crit}°C critical threshold",
                    "action": "PHASE_2E_THERMAL", "gpu": gpu_idx})
            elif temp >= temp_warn:
                alerts.append({"level": "WARNING", "type": "TEMP_WARNING",
                    "message": f"GPU {gpu_idx}: {temp}°C approaching thermal limit",
                    "action": "MONITOR", "gpu": gpu_idx})
        except (ValueError, IndexError):
            pass

    # Check fan speed (if available, index 6)
    if len(parts) >= 7:
        try:
            fan = parts[6].replace('%','').replace(' ','').strip()
            if fan.isdigit() and int(fan) == 0:
                alerts.append({"level": "CRITICAL", "type": "FAN_STOPPED",
                    "message": f"GPU {parts[0]}: Fan speed is 0% — fan failure",
                    "action": "PHASE_2E_THERMAL", "gpu": parts[0]})
        except (ValueError, IndexError):
            pass

    # Check clock degradation (SM current vs max, indices 4 and 5)
    if len(parts) >= 6:
        try:
            sm_cur = int(parts[4].replace('MHz','').replace(' ','').strip())
            sm_max = int(parts[5].replace('MHz','').replace(' ','').strip())
            if sm_max > 0 and sm_cur < sm_max * 0.7:
                alerts.append({"level": "WARNING", "type": "CLOCK_DEGRADED",
                    "message": f"GPU {parts[0]}: SM clock {sm_cur}/{sm_max} MHz ({sm_cur*100//sm_max}%)",
                    "action": "PHASE_2F_PERF", "gpu": parts[0]})
        except (ValueError, IndexError):
            pass

# --- PCIe link ---
if pcie_link and not pcie_link.startswith("ERROR"):
    link_entries = re.findall(r'LnkSta:.*?Speed\s+(\S+).*?Width\s+(\S+)', pcie_link)
    for i, (speed, width) in enumerate(link_entries):
        if speed != pcie_speed:
            alerts.append({"level": "WARNING", "type": "PCIE_SPEED_DEGRADED",
                "message": f"GPU {i}: PCIe speed {speed} (expected {pcie_speed})",
                "action": "PHASE_2G_PCIE", "gpu": str(i)})
        if width != pcie_width:
            alerts.append({"level": "WARNING", "type": "PCIE_WIDTH_DEGRADED",
                "message": f"GPU {i}: PCIe width {width} (expected {pcie_width})",
                "action": "PHASE_2G_PCIE", "gpu": str(i)})

# --- AER errors ---
if aer_errors.strip():
    if re.search(r'fatal|uncorrectable', aer_errors, re.IGNORECASE):
        alerts.append({"level": "CRITICAL", "type": "PCIE_AER_FATAL",
            "message": "Fatal/Uncorrectable PCIe AER errors detected",
            "action": "PHASE_2G_PCIE", "gpu": "check_dmesg"})
    elif re.search(r'correctable', aer_errors, re.IGNORECASE):
        alerts.append({"level": "WARNING", "type": "PCIE_AER_CORRECTABLE",
            "message": "Correctable PCIe AER errors detected — monitor trend",
            "action": "MONITOR", "gpu": "check_dmesg"})

# --- Deduplicate ---
seen = set()
unique_alerts = []
for a in alerts:
    key = (a['type'], a.get('gpu',''))
    if key not in seen:
        seen.add(key)
        unique_alerts.append(a)

# --- Build report ---
max_sev = 'OK'
for level in ['FATAL', 'CRITICAL', 'WARNING']:
    if any(a['level'] == level for a in unique_alerts):
        max_sev = level
        break

report = {
    "timestamp": datetime.now().isoformat(),
    "hostname": os.uname().nodename,
    "cluster_role": "rtx5090_inference_node",
    "kernel": kernel,
    "driver": driver,
    "expected_gpu_count": expected,
    "smi_gpu_count": smi_count,
    "pcie_gpu_count": pcie_count,
    "gpu_count_match": smi_count == expected == pcie_count,
    "max_severity": max_sev,
    "alert_count": len(unique_alerts),
    "alerts": unique_alerts,
    "raw": {
        "gpu_info": gpu_info,
        "xid_errors": xid_errors[:2000],
        "pcie_link": pcie_link[:2000],
        "aer_errors": aer_errors[:1000],
        "thermal": thermal,
        "gsp_info": gsp_info[:1000]
    }
}

with open(report_path, 'w') as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print(f"\n{'='*60}")
print(f"Health Check Result: {max_sev}")
print(f"GPUs: {smi_count}/{expected} (PCIe: {pcie_count})")
print(f"Alerts: {len(unique_alerts)}")
for a in unique_alerts:
    print(f"  [{a['level']}] {a['type']} (GPU:{a.get('gpu','?')}): {a['message']}")
    print(f"         -> {a['action']}")
print(f"Report: {report_path}")
print(f"{'='*60}")
PYEOF
