#!/bin/bash
# gpu_benchmark.sh — RTX 5090集群性能基线/对比脚本
# 用法: bash gpu_benchmark.sh [baseline|compare] [baseline_file]
#
# baseline模式: 生成当前GPU性能基线
# compare模式: 与已有基线对比，输出偏差
#
# 此脚本在每张GPU上运行标准化计算测试，记录TFLOPS和延迟，
# 用于检测性能退化（替代ECC监控作为健康指标）。

set -euo pipefail

MODE="${1:-baseline}"
BASELINE_FILE="${2:-}"
OUTPUT_DIR="/tmp/gpu_bench_$(hostname)_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

RESULT_FILE="$OUTPUT_DIR/benchmark_result.json"

echo "=== RTX 5090 GPU Benchmark ==="
echo "Mode: $MODE | Host: $(hostname) | Time: $(date -Iseconds)"

# Run benchmark on each GPU
python3 << 'PYEOF' "$MODE" "$BASELINE_FILE" "$RESULT_FILE"
import torch
import torch.cuda
import time
import json
import sys
import os

mode = sys.argv[1]
baseline_path = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
result_path = sys.argv[3]

num_gpus = torch.cuda.device_count()
print(f"Detected {num_gpus} GPUs")

results = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "hostname": os.uname().nodename,
    "gpu_count": num_gpus,
    "gpus": []
}

for gpu_idx in range(num_gpus):
    dev = torch.device(f'cuda:{gpu_idx}')
    name = torch.cuda.get_device_name(gpu_idx)
    props = torch.cuda.get_device_properties(gpu_idx)
    total_mem_gb = props.total_mem / 1e9

    print(f"\n--- GPU {gpu_idx}: {name} ({total_mem_gb:.1f}GB) ---")

    gpu_result = {
        "index": gpu_idx,
        "name": name,
        "total_memory_gb": round(total_mem_gb, 1),
        "tests": {}
    }

    # Warm up
    torch.cuda.set_device(gpu_idx)
    warmup = torch.randn(1024, 1024, device=dev)
    _ = warmup @ warmup.t()
    torch.cuda.synchronize()

    # Test 1: FP32 GEMM (4096x4096, 200 iterations)
    print("  [1/4] FP32 GEMM...", end=" ", flush=True)
    M = 4096
    iters = 200
    a = torch.randn(M, M, device=dev)
    torch.cuda.synchronize()
    start = time.time()
    for _ in range(iters):
        c = torch.mm(a, a)
    torch.cuda.synchronize()
    elapsed = time.time() - start
    tflops_fp32 = (2 * M**3 * iters) / elapsed / 1e12
    print(f"{tflops_fp32:.2f} TFLOPS ({elapsed:.2f}s)")
    gpu_result["tests"]["fp32_gemm"] = {
        "tflops": round(tflops_fp32, 2),
        "elapsed_s": round(elapsed, 3),
        "matrix_size": M,
        "iterations": iters
    }
    del a, c

    # Test 2: FP16 GEMM (Tensor Core, 4096x4096, 500 iterations)
    print("  [2/4] FP16 GEMM (Tensor Core)...", end=" ", flush=True)
    iters = 500
    a = torch.randn(M, M, dtype=torch.float16, device=dev)
    torch.cuda.synchronize()
    start = time.time()
    for _ in range(iters):
        c = torch.mm(a, a)
    torch.cuda.synchronize()
    elapsed = time.time() - start
    tflops_fp16 = (2 * M**3 * iters) / elapsed / 1e12
    print(f"{tflops_fp16:.2f} TFLOPS ({elapsed:.2f}s)")
    gpu_result["tests"]["fp16_gemm"] = {
        "tflops": round(tflops_fp16, 2),
        "elapsed_s": round(elapsed, 3),
        "matrix_size": M,
        "iterations": iters
    }
    del a, c

    # Test 3: Memory bandwidth (large copy, 8GB)
    print("  [3/4] Memory bandwidth...", end=" ", flush=True)
    size_gb = 4.0  # 4GB per copy to stay safe
    numel = int(size_gb * 1e9 / 4)  # float32
    src = torch.randn(numel, device=dev)
    torch.cuda.synchronize()
    copy_iters = 20
    start = time.time()
    for _ in range(copy_iters):
        dst = src.clone()
    torch.cuda.synchronize()
    elapsed = time.time() - start
    bw_gbs = (size_gb * 2 * copy_iters) / elapsed  # read + write
    print(f"{bw_gbs:.1f} GB/s ({elapsed:.2f}s)")
    gpu_result["tests"]["mem_bandwidth"] = {
        "bandwidth_gbs": round(bw_gbs, 1),
        "elapsed_s": round(elapsed, 3),
        "data_size_gb": size_gb
    }
    del src, dst

    # Test 4: Inference latency simulation (small batch matmul)
    print("  [4/4] Inference latency...", end=" ", flush=True)
    batch = 1
    seq_len = 2048
    hidden = 4096
    x = torch.randn(batch, seq_len, hidden, dtype=torch.float16, device=dev)
    w = torch.randn(hidden, hidden, dtype=torch.float16, device=dev)
    torch.cuda.synchronize()

    latencies = []
    for _ in range(100):
        torch.cuda.synchronize()
        t0 = time.time()
        y = torch.matmul(x, w)
        torch.cuda.synchronize()
        latencies.append((time.time() - t0) * 1000)  # ms

    p50 = sorted(latencies)[50]
    p99 = sorted(latencies)[99]
    print(f"P50={p50:.2f}ms, P99={p99:.2f}ms")
    gpu_result["tests"]["inference_latency"] = {
        "p50_ms": round(p50, 3),
        "p99_ms": round(p99, 3),
        "batch": batch,
        "seq_len": seq_len,
        "hidden": hidden
    }
    del x, w, y

    torch.cuda.empty_cache()
    results["gpus"].append(gpu_result)

# --- Comparison ---
if mode == "compare" and baseline_path and os.path.exists(baseline_path):
    print("\n=== Performance Comparison vs Baseline ===")
    with open(baseline_path) as f:
        baseline = json.load(f)

    deviations = []
    for curr_gpu in results["gpus"]:
        idx = curr_gpu["index"]
        base_gpu = next((g for g in baseline["gpus"] if g["index"] == idx), None)
        if not base_gpu:
            print(f"  GPU {idx}: No baseline data")
            continue

        for test_name in curr_gpu["tests"]:
            if test_name not in base_gpu.get("tests", {}):
                continue

            curr_test = curr_gpu["tests"][test_name]
            base_test = base_gpu["tests"][test_name]

            # Compare the primary metric
            if "tflops" in curr_test:
                curr_val = curr_test["tflops"]
                base_val = base_test["tflops"]
                metric = "TFLOPS"
            elif "bandwidth_gbs" in curr_test:
                curr_val = curr_test["bandwidth_gbs"]
                base_val = base_test["bandwidth_gbs"]
                metric = "GB/s"
            elif "p50_ms" in curr_test:
                curr_val = curr_test["p50_ms"]
                base_val = base_test["p50_ms"]
                metric = "ms (lower=better)"
            else:
                continue

            if base_val > 0:
                if "ms" in metric:
                    pct = (curr_val - base_val) / base_val * 100
                    status = "DEGRADED" if pct > 20 else "OK"
                else:
                    pct = (curr_val - base_val) / base_val * 100
                    status = "DEGRADED" if pct < -10 else "OK"
            else:
                pct = 0
                status = "UNKNOWN"

            dev_info = {
                "gpu": idx, "test": test_name,
                "baseline": base_val, "current": curr_val,
                "deviation_pct": round(pct, 1), "status": status
            }
            deviations.append(dev_info)
            flag = " ⚠️" if status == "DEGRADED" else ""
            print(f"  GPU {idx} | {test_name}: {base_val} → {curr_val} {metric} ({pct:+.1f}%){flag}")

    results["comparison"] = {
        "baseline_file": baseline_path,
        "deviations": deviations,
        "degraded_count": sum(1 for d in deviations if d["status"] == "DEGRADED")
    }

# Save
with open(result_path, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nResults saved to: {result_path}")
PYEOF

echo ""
echo "Benchmark complete. Result: $RESULT_FILE"
if [ "$MODE" = "baseline" ]; then
    echo "To compare later: bash $0 compare $RESULT_FILE"
fi
