"""
VLLM Latency Scenarios — RC-1, RC-3~RC-6 五个 vLLM 延迟场景

必选场景：制造 vLLM 推理延迟不稳定。

场景列表：
- RC-1: gpu_contention - GPU 资源争抢
- RC-3: storage_io_interference - 存储 I/O 干扰
- RC-4: platform_cascade - 平台组件级联延迟
- RC-5: os_resource_pressure - OS 资源压力
- RC-6: thermal_throttling - 热降频
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# RC-1: GPU 资源争抢
# ============================================================================

class GPUContentionScenario(BaseScenario):
    """
    RC-1: GPU 资源争抢。
    
    使用 gpu-burn 在同一 GPU 上运行多个计算任务争抢 SM 和显存带宽。
    
    效果：
    - 推理延迟 P50 增加 3-5x
    - P99 尾延迟出现周期性波动
    - TTFT (Time To First Token) 不稳定
    """
    
    @property
    def name(self) -> str:
        return "gpu_contention"
    
    @property
    def description(self) -> str:
        return "GPU 资源争抢 — 使用 gpu-burn 制造 GPU 满载"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        gpu_id = ctx.params.get("gpu_id", 0)
        intensity = ctx.params.get("intensity", 100)  # GPU 使用率百分比
        
        # 构建命令 - 使用 gpu-burn 或备用方案
        # gpu-burn 需要预先安装：git clone https://github.com/wilicc/gpu-burn
        inject_cmd = f"cd /tmp/gpu-burn && CUDA_VISIBLE_DEVICES={gpu_id} ./gpu_burn {duration} &"
        recover_cmd = "pkill -f gpu_burn"
        
        logger.info(
            f"注入 GPU 争抢: node={ctx.target_node}, "
            f"gpu_id={gpu_id}, duration={duration}s, intensity={intensity}%"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="gpu_burn",
            inject_params={
                "duration": duration,
                "gpu_id": gpu_id,
                "intensity": intensity,
            },
            recover_action="kill_gpu_burn",
            recover_params={},
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        # gpu-burn 在后台运行，命令会立即返回
        if result.success or "GPU burn" in result.output:
            logger.info(f"GPU 争抢注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"GPU 争抢注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复 GPU 争抢: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pkill -f gpu_burn || pkill -f gpu-burn",
        )
        
        # pkill 失败可能是因为进程不存在，这是可接受的
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        # 检查 gpu-burn 是否还在运行
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pgrep -f gpu_burn || pgrep -f gpu-burn || echo 'not_running'",
        )
        if "not_running" in result.output:
            logger.info("验证通过: gpu-burn 已停止")
            return True
        logger.warning("验证失败: gpu-burn 仍在运行")
        return False
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "gpu_util": 'DCGM_FI_DEV_GPU_UTIL{{node="{node}"}}',
            "gpu_mem_used": 'DCGM_FI_DEV_FB_USED{{node="{node}"}}',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "ttft": 'histogram_quantile(0.95, rate(vllm:time_to_first_token_seconds_bucket[1m]))',
        }


# ============================================================================
# RC-3: 存储 I/O 干扰
# ============================================================================

class StorageIOInterferenceScenario(BaseScenario):
    """
    RC-3: 存储 I/O 干扰。
    
    使用 fio 在推理节点的数据盘发起大量随机 I/O。
    
    效果：
    - 模型冷启动时间增加 3-6x
    - KV Cache 落盘时延迟出现间歇性尖峰
    - 模型加载时间延长
    """
    
    @property
    def name(self) -> str:
        return "storage_io_interference"
    
    @property
    def description(self) -> str:
        return "存储 I/O 干扰 — 使用 fio 制造磁盘 I/O 压力"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        filename = ctx.params.get("filename", "/data/testfile")
        rw_mode = ctx.params.get("rw_mode", "randwrite")  # randwrite/randread/rw
        bs = ctx.params.get("bs", "4k")
        iodepth = ctx.params.get("iodepth", 128)
        numjobs = ctx.params.get("numjobs", 8)
        
        # 构建命令
        inject_cmd = (
            f"fio --name=disturb --filename={filename} "
            f"--rw={rw_mode} --bs={bs} --iodepth={iodepth} --numjobs={numjobs} "
            f"--size=10G --time_based --runtime={duration} &"
        )
        recover_cmd = "pkill -f 'fio.*disturb'"
        
        logger.info(
            f"注入存储 I/O 干扰: node={ctx.target_node}, "
            f"filename={filename}, rw={rw_mode}, duration={duration}s"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="fio_stress",
            inject_params={
                "duration": duration,
                "filename": filename,
                "rw_mode": rw_mode,
                "bs": bs,
                "iodepth": iodepth,
                "numjobs": numjobs,
            },
            recover_action="kill_fio",
            recover_params={},
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"存储 I/O 干扰注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"存储 I/O 干扰注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复存储 I/O 干扰: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pkill -f 'fio.*disturb' || pkill -f 'fio'",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pgrep -f 'fio.*disturb' || echo 'not_running'",
        )
        if "not_running" in result.output:
            logger.info("验证通过: fio 已停止")
            return True
        logger.warning("验证失败: fio 仍在运行")
        return False
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "disk_io_util": 'node_disk_io_utilization_seconds{{device="{device}"}}',
            "disk_io_wait": 'node_disk_io_time_weighted_seconds{{device="{device}"}}',
            "disk_read_bytes": 'node_disk_read_bytes_total{{device="{device}"}}',
            "disk_write_bytes": 'node_disk_written_bytes_total{{device="{device}"}}',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
        }


# ============================================================================
# RC-4: 平台组件级联延迟
# ============================================================================

class PlatformCascadeScenario(BaseScenario):
    """
    RC-4: 平台组件级联延迟。
    
    通过对平台关键组件（MySQL、Redis、K8s API）注入延迟，
    制造级联效应影响推理服务。
    
    效果：
    - 请求排队，延迟累积
    - 推理服务超时重试
    - 整体吞吐下降
    """
    
    @property
    def name(self) -> str:
        return "platform_cascade"
    
    @property
    def description(self) -> str:
        return "平台组件级联延迟 — 对 MySQL/Redis 注入延迟"
    
    @property
    def layer(self) -> str:
        return "platform"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        target_component = ctx.params.get("target_component", "mysql")  # mysql/redis/k8s
        delay_ms = ctx.params.get("delay_ms", 100)
        duration = ctx.params.get("duration", 300)
        port = ctx.params.get("port", 3306)  # MySQL 默认端口
        
        # 使用 tc netem 对特定端口注入延迟
        inject_cmd = (
            f"sudo tc qdisc add dev eth0 root handle 1: prio bands 4 "
            f"&& sudo tc filter add dev eth0 protocol ip parent 1:0 prio 1 u32 "
            f"match ip dport {port} 0xffff flowid 1:1 "
            f"&& sudo tc qdisc add dev eth0 parent 1:1 handle 10: netem delay {delay_ms}ms"
        )
        recover_cmd = "sudo tc qdisc del dev eth0 root"
        
        logger.info(
            f"注入平台级联延迟: node={ctx.target_node}, "
            f"component={target_component}, port={port}, delay={delay_ms}ms"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="platform_delay",
            inject_params={
                "target_component": target_component,
                "delay_ms": delay_ms,
                "port": port,
            },
            recover_action="tc_del_qdisc",
            recover_params={},
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"平台级联延迟注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"平台级联延迟注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复平台级联延迟: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="sudo tc qdisc del dev eth0 root 2>/dev/null || true",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="sudo tc qdisc show dev eth0 | grep -q netem && echo 'exists' || echo 'not_exists'",
        )
        if "not_exists" in result.output:
            logger.info("验证通过: tc netem 已删除")
            return True
        logger.warning("验证失败: tc netem 仍存在")
        return False
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "mysql_latency": 'mysql_query_duration_seconds',
            "redis_latency": 'redis_command_duration_seconds',
            "k8s_api_latency": 'kubernetes_api_request_duration_seconds',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "request_queue_depth": 'vllm:request_queue_depth',
        }


# ============================================================================
# RC-5: OS 资源压力
# ============================================================================

class OSResourcePressureScenario(BaseScenario):
    """
    RC-5: OS 资源压力。
    
    使用 stress-ng 制造内存和 CPU 压力。
    
    效果：
    - 延迟整体升高且波动大
    - 出现间歇性超时
    - OOM Killer 可能杀死推理进程
    """
    
    @property
    def name(self) -> str:
        return "os_resource_pressure"
    
    @property
    def description(self) -> str:
        return "OS 资源压力 — 使用 stress-ng 制造 CPU/内存压力"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        vm_bytes_percent = ctx.params.get("vm_bytes_percent", 80)
        cpu_workers = ctx.params.get("cpu_workers", 64)
        cpu_load = ctx.params.get("cpu_load", 90)
        io_workers = ctx.params.get("io_workers", 4)
        
        # 构建命令
        inject_cmd = (
            f"stress-ng "
            f"--vm 4 --vm-bytes {vm_bytes_percent}% "
            f"--cpu {cpu_workers} --cpu-load {cpu_load} "
            f"--io {io_workers} "
            f"--timeout {duration}s &"
        )
        recover_cmd = "pkill -f stress-ng"
        
        logger.info(
            f"注入 OS 资源压力: node={ctx.target_node}, "
            f"vm={vm_bytes_percent}%, cpu_workers={cpu_workers}, cpu_load={cpu_load}%"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="stress_ng",
            inject_params={
                "duration": duration,
                "vm_bytes_percent": vm_bytes_percent,
                "cpu_workers": cpu_workers,
                "cpu_load": cpu_load,
                "io_workers": io_workers,
            },
            recover_action="kill_stress_ng",
            recover_params={},
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        # stress-ng 在后台运行，前台命令会立即返回
        if result.success or "dispatching hogs" in result.output.lower():
            logger.info(f"OS 资源压力注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"OS 资源压力注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复 OS 资源压力: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pkill -f stress-ng || pkill -f stress",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pgrep -f stress-ng || pgrep -f stress || echo 'not_running'",
        )
        if "not_running" in result.output:
            logger.info("验证通过: stress-ng 已停止")
            return True
        logger.warning("验证失败: stress-ng 仍在运行")
        return False
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "cpu_util": '1 - rate(node_cpu_seconds_total{{mode="idle"}}[1m])',
            "memory_util": '1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
            "io_util": 'rate(node_disk_io_time_seconds_total[1m])',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "oom_events": 'increase(node_oom_events_total[5m])',
        }


# ============================================================================
# RC-6: 热降频
# ============================================================================

class ThermalThrottlingScenario(BaseScenario):
    """
    RC-6: 热降频。
    
    通过限制 GPU 风扇转速或使用 nvidia-smi 设置功率限制，
    模拟 GPU 热降频场景。
    
    效果：
    - GPU 核心频率降低
    - 推理延迟增加
    - 吞吐下降
    """
    
    @property
    def name(self) -> str:
        return "thermal_throttling"
    
    @property
    def description(self) -> str:
        return "热降频 — 限制 GPU 功率模拟热降频"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        gpu_id = ctx.params.get("gpu_id", 0)
        power_limit = ctx.params.get("power_limit", 150)  # 瓦特，默认降低到 150W
        duration = ctx.params.get("duration", 300)
        
        # 先获取当前功率限制用于恢复
        get_power_cmd = f"nvidia-smi -i {gpu_id} --query-gpu=power.limit --format=csv,noheader,nounits"
        get_result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=get_power_cmd,
        )
        
        original_power = 300  # 默认值
        if get_result.success:
            try:
                original_power = int(float(get_result.output.strip()))
            except ValueError:
                logger.warning(f"无法解析原始功率限制: {get_result.output}")
        
        # 设置功率限制
        inject_cmd = f"sudo nvidia-smi -i {gpu_id} -pl {power_limit}"
        recover_cmd = f"sudo nvidia-smi -i {gpu_id} -pl {original_power}"
        
        logger.info(
            f"注入热降频: node={ctx.target_node}, "
            f"gpu_id={gpu_id}, power_limit={power_limit}W (原: {original_power}W)"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="gpu_power_limit",
            inject_params={
                "gpu_id": gpu_id,
                "power_limit": power_limit,
            },
            recover_action="gpu_power_restore",
            recover_params={
                "gpu_id": gpu_id,
                "original_power": original_power,
            },
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"热降频注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"热降频注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        gpu_id = ctx.params.get("gpu_id", 0)
        original_power = ctx.params.get("original_power", 300)
        
        logger.info(f"恢复热降频: node={ctx.target_node}, gpu_id={gpu_id}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"sudo nvidia-smi -i {gpu_id} -pl {original_power}",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        gpu_id = ctx.params.get("gpu_id", 0)
        original_power = ctx.params.get("original_power", 300)
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"nvidia-smi -i {gpu_id} --query-gpu=power.limit --format=csv,noheader,nounits",
        )
        
        if result.success:
            try:
                current_power = int(float(result.output.strip()))
                if current_power >= original_power - 10:  # 允许 10W 误差
                    logger.info(f"验证通过: 功率限制已恢复到 {current_power}W")
                    return True
                else:
                    logger.warning(f"验证失败: 功率限制为 {current_power}W，期望 >= {original_power}W")
                    return False
            except ValueError:
                logger.warning(f"无法解析功率限制: {result.output}")
                return True
        
        logger.warning(f"无法检查功率限制: {result.error}")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "gpu_temp": 'DCGM_FI_DEV_GPU_TEMP{{node="{node}"}}',
            "gpu_power": 'DCGM_FI_DEV_POWER_USAGE{{node="{node}"}}',
            "gpu_sm_clock": 'DCGM_FI_DEV_SM_CLOCK{{node="{node}"}}',
            "gpu_throttle_reason": 'DCGM_FI_DEV_GPU_UTIL{{node="{node}"}}',
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_throughput": 'rate(vllm:request_duration_seconds_count[1m])',
        }


# ============================================================================
# 场景注册列表
# ============================================================================

SCENARIOS = [
    GPUContentionScenario,
    StorageIOInterferenceScenario,
    PlatformCascadeScenario,
    OSResourcePressureScenario,
    ThermalThrottlingScenario,
]