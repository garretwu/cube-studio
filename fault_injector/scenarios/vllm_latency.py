"""
VLLM Latency Scenarios 鈥?RC-1, RC-3~RC-6 浜斾釜 vLLM 寤惰繜鍦烘櫙

蹇呴€夊満鏅細鍒堕€?vLLM 鎺ㄧ悊寤惰繜涓嶇ǔ瀹氥€?

鍦烘櫙鍒楄〃锛?
- RC-1: gpu_contention - GPU 璧勬簮浜夋姠
- RC-3: storage_io_interference - 瀛樺偍 I/O 骞叉壈
- RC-4: platform_cascade - 骞冲彴缁勪欢绾ц仈寤惰繜
- RC-5: os_resource_pressure - OS 璧勬簮鍘嬪姏
- RC-6: thermal_throttling - 鐑檷棰?
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# RC-2: Network Jitter
# ============================================================================

class NetworkJitterScenario(BaseScenario):
    """RC-2: network_jitter - 网络延迟抖动 (tc netem)."""

    @property
    def name(self) -> str:
        return "network_jitter"

    @property
    def description(self) -> str:
        return "网络延迟抖动 - 使用 tc netem 注入不确定延迟"

    @property
    def layer(self) -> str:
        return "os"

    def _build_tc_command(self, params: dict[str, Any]) -> str:
        interface = params.get("interface", "eth0")
        delay_ms = params.get("delay_ms", 50)
        jitter_ms = params.get("jitter_ms", 100)
        distribution = params.get("distribution", "pareto")
        loss_pct = params.get("loss_pct", 0)

        cmd = f"sudo tc qdisc add dev {interface} root netem delay {delay_ms}ms"
        if jitter_ms > 0:
            cmd += f" {jitter_ms}ms"
        if distribution != "normal":
            cmd += f" distribution {distribution}"
        if loss_pct > 0:
            cmd += f" loss {loss_pct}%"
        return cmd

    def _build_recovery_command(self, interface: str) -> str:
        return f"sudo tc qdisc del dev {interface} root"

    async def inject(self, ctx: FaultContext) -> InjectResult:
        interface = ctx.params.get("interface", "eth0")
        delay_ms = ctx.params.get("delay_ms", 50)
        jitter_ms = ctx.params.get("jitter_ms", 100)
        distribution = ctx.params.get("distribution", "pareto")
        loss_pct = ctx.params.get("loss_pct", 0)

        inject_cmd = self._build_tc_command(ctx.params)

        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="tc_add_delay",
            inject_params={
                "node": ctx.target_node,
                "interface": interface,
                "delay_ms": delay_ms,
                "jitter_ms": jitter_ms,
                "distribution": distribution,
                "loss_pct": loss_pct,
            },
            recover_action="tc_del_qdisc",
            recover_params={"node": ctx.target_node, "interface": interface},
        )

        result = await ctx.ssh.run_command(node=ctx.target_node, command=inject_cmd)
        if result.success:
            return InjectResult(success=True, fault_id=ctx.fault_id)
        return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        interface = ctx.params.get("interface", "eth0")
        recover_cmd = self._build_recovery_command(interface)
        result = await ctx.ssh.run_command(node=ctx.target_node, command=recover_cmd)

        if result.success or "No such file or directory" in result.error or "Cannot delete" in result.error:
            ctx.rollback.mark_recovered(ctx.fault_id)
            return RecoverResult(success=True, fault_id=ctx.fault_id)

        ctx.rollback.mark_failed(ctx.fault_id)
        return RecoverResult(success=False, fault_id=ctx.fault_id, error=result.error)

    async def verify(self, ctx: FaultContext) -> bool:
        interface = ctx.params.get("interface", "eth0")
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"sudo tc qdisc show dev {interface}",
        )
        if not result.success:
            return True
        return "netem" not in result.output

    def monitor_queries(self) -> dict[str, str]:
        return {
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p95": 'histogram_quantile(0.95, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "network_latency": 'histogram_quantile(0.95, rate(network_latency_seconds_bucket[1m]))',
        }

# ============================================================================
# RC-1: GPU 璧勬簮浜夋姠
# ============================================================================

class GPUContentionScenario(BaseScenario):
    """
    RC-1: GPU 璧勬簮浜夋姠銆?
    
    浣跨敤 gpu-burn 鍦ㄥ悓涓€ GPU 涓婅繍琛屽涓绠椾换鍔′簤鎶?SM 鍜屾樉瀛樺甫瀹姐€?
    
    鏁堟灉锛?
    - 鎺ㄧ悊寤惰繜 P50 澧炲姞 3-5x
    - P99 灏惧欢杩熷嚭鐜板懆鏈熸€ф尝鍔?
    - TTFT (Time To First Token) 涓嶇ǔ瀹?
    """
    
    @property
    def name(self) -> str:
        return "gpu_contention"
    
    @property
    def description(self) -> str:
        return "GPU 璧勬簮浜夋姠 鈥?浣跨敤 gpu-burn 鍒堕€?GPU 婊¤浇"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        gpu_id = ctx.params.get("gpu_id", 0)
        intensity = ctx.params.get("intensity", 100)  # GPU 浣跨敤鐜囩櫨鍒嗘瘮
        
        # 鏋勫缓鍛戒护 - 浣跨敤 gpu-burn 鎴栧鐢ㄦ柟妗?
        # gpu-burn 闇€瑕侀鍏堝畨瑁咃細git clone https://github.com/wilicc/gpu-burn
        inject_cmd = f"cd /tmp/gpu-burn && CUDA_VISIBLE_DEVICES={gpu_id} ./gpu_burn {duration} &"
        recover_cmd = "pkill -f gpu_burn"
        
        logger.info(
            f"娉ㄥ叆 GPU 浜夋姠: node={ctx.target_node}, "
            f"gpu_id={gpu_id}, duration={duration}s, intensity={intensity}%"
        )
        
        # 鍐欏叆 WAL
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
        
        # 鎵ц娉ㄥ叆
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        # gpu-burn 鍦ㄥ悗鍙拌繍琛岋紝鍛戒护浼氱珛鍗宠繑鍥?
        if result.success or "GPU burn" in result.output:
            logger.info(f"GPU 浜夋姠娉ㄥ叆鎴愬姛: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"GPU 浜夋姠娉ㄥ叆澶辫触: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"鎭㈠ GPU 浜夋姠: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pkill -f gpu_burn || pkill -f gpu-burn",
        )
        
        # pkill 澶辫触鍙兘鏄洜涓鸿繘绋嬩笉瀛樺湪锛岃繖鏄彲鎺ュ彈鐨?
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        # 妫€鏌?gpu-burn 鏄惁杩樺湪杩愯
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="pgrep -f gpu_burn || pgrep -f gpu-burn || echo 'not_running'",
        )
        if "not_running" in result.output:
            logger.info("验证通过: gpu-burn 已停止")
            return True
        logger.warning("楠岃瘉澶辫触: gpu-burn 浠嶅湪杩愯")
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
# RC-3: 瀛樺偍 I/O 骞叉壈
# ============================================================================

class StorageIOInterferenceScenario(BaseScenario):
    """
    RC-3: 瀛樺偍 I/O 骞叉壈銆?
    
    浣跨敤 fio 鍦ㄦ帹鐞嗚妭鐐圭殑鏁版嵁鐩樺彂璧峰ぇ閲忛殢鏈?I/O銆?
    
    鏁堟灉锛?
    - 妯″瀷鍐峰惎鍔ㄦ椂闂村鍔?3-6x
    - KV Cache 钀界洏鏃跺欢杩熷嚭鐜伴棿姝囨€у皷宄?
    - 妯″瀷鍔犺浇鏃堕棿寤堕暱
    """
    
    @property
    def name(self) -> str:
        return "storage_io_interference"
    
    @property
    def description(self) -> str:
        return "瀛樺偍 I/O 骞叉壈 鈥?浣跨敤 fio 鍒堕€犵鐩?I/O 鍘嬪姏"
    
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
        
        # 鏋勫缓鍛戒护
        inject_cmd = (
            f"fio --name=disturb --filename={filename} "
            f"--rw={rw_mode} --bs={bs} --iodepth={iodepth} --numjobs={numjobs} "
            f"--size=10G --time_based --runtime={duration} &"
        )
        recover_cmd = "pkill -f 'fio.*disturb'"
        
        logger.info(
            f"娉ㄥ叆瀛樺偍 I/O 骞叉壈: node={ctx.target_node}, "
            f"filename={filename}, rw={rw_mode}, duration={duration}s"
        )
        
        # 鍐欏叆 WAL
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
        
        # 鎵ц娉ㄥ叆
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"瀛樺偍 I/O 骞叉壈娉ㄥ叆鎴愬姛: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"瀛樺偍 I/O 骞叉壈娉ㄥ叆澶辫触: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"鎭㈠瀛樺偍 I/O 骞叉壈: node={ctx.target_node}")
        
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
        logger.warning("楠岃瘉澶辫触: fio 浠嶅湪杩愯")
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
# RC-4: 骞冲彴缁勪欢绾ц仈寤惰繜
# ============================================================================

class PlatformCascadeScenario(BaseScenario):
    """
    RC-4: 骞冲彴缁勪欢绾ц仈寤惰繜銆?
    
    閫氳繃瀵瑰钩鍙板叧閿粍浠讹紙MySQL銆丷edis銆並8s API锛夋敞鍏ュ欢杩燂紝
    鍒堕€犵骇鑱旀晥搴斿奖鍝嶆帹鐞嗘湇鍔°€?
    
    鏁堟灉锛?
    - 璇锋眰鎺掗槦锛屽欢杩熺疮绉?
    - 鎺ㄧ悊鏈嶅姟瓒呮椂閲嶈瘯
    - 鏁翠綋鍚炲悙涓嬮檷
    """
    
    @property
    def name(self) -> str:
        return "platform_cascade"
    
    @property
    def description(self) -> str:
        return "骞冲彴缁勪欢绾ц仈寤惰繜 鈥?瀵?MySQL/Redis 娉ㄥ叆寤惰繜"
    
    @property
    def layer(self) -> str:
        return "platform"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        target_component = ctx.params.get("target_component", "mysql")  # mysql/redis/k8s
        delay_ms = ctx.params.get("delay_ms", 100)
        duration = ctx.params.get("duration", 300)
        port = ctx.params.get("port", 3306)  # MySQL 榛樿绔彛
        
        # 浣跨敤 tc netem 瀵圭壒瀹氱鍙ｆ敞鍏ュ欢杩?
        inject_cmd = (
            f"sudo tc qdisc add dev eth0 root handle 1: prio bands 4 "
            f"&& sudo tc filter add dev eth0 protocol ip parent 1:0 prio 1 u32 "
            f"match ip dport {port} 0xffff flowid 1:1 "
            f"&& sudo tc qdisc add dev eth0 parent 1:1 handle 10: netem delay {delay_ms}ms"
        )
        recover_cmd = "sudo tc qdisc del dev eth0 root"
        
        logger.info(
            f"娉ㄥ叆骞冲彴绾ц仈寤惰繜: node={ctx.target_node}, "
            f"component={target_component}, port={port}, delay={delay_ms}ms"
        )
        
        # 鍐欏叆 WAL
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
        
        # 鎵ц娉ㄥ叆
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"骞冲彴绾ц仈寤惰繜娉ㄥ叆鎴愬姛: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"骞冲彴绾ц仈寤惰繜娉ㄥ叆澶辫触: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"鎭㈠骞冲彴绾ц仈寤惰繜: node={ctx.target_node}")
        
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
# RC-5: OS 璧勬簮鍘嬪姏
# ============================================================================

class OSResourcePressureScenario(BaseScenario):
    """
    RC-5: OS 璧勬簮鍘嬪姏銆?
    
    浣跨敤 stress-ng 鍒堕€犲唴瀛樺拰 CPU 鍘嬪姏銆?
    
    鏁堟灉锛?
    - 寤惰繜鏁翠綋鍗囬珮涓旀尝鍔ㄥぇ
    - 鍑虹幇闂存瓏鎬ц秴鏃?
    - OOM Killer 鍙兘鏉€姝绘帹鐞嗚繘绋?
    """
    
    @property
    def name(self) -> str:
        return "os_resource_pressure"
    
    @property
    def description(self) -> str:
        return "OS 璧勬簮鍘嬪姏 鈥?浣跨敤 stress-ng 鍒堕€?CPU/鍐呭瓨鍘嬪姏"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        vm_bytes_percent = ctx.params.get("vm_bytes_percent", 80)
        cpu_workers = ctx.params.get("cpu_workers", 64)
        cpu_load = ctx.params.get("cpu_load", 90)
        io_workers = ctx.params.get("io_workers", 4)
        
        # 鏋勫缓鍛戒护
        inject_cmd = (
            f"stress-ng "
            f"--vm 4 --vm-bytes {vm_bytes_percent}% "
            f"--cpu {cpu_workers} --cpu-load {cpu_load} "
            f"--io {io_workers} "
            f"--timeout {duration}s &"
        )
        recover_cmd = "pkill -f stress-ng"
        
        logger.info(
            f"娉ㄥ叆 OS 璧勬簮鍘嬪姏: node={ctx.target_node}, "
            f"vm={vm_bytes_percent}%, cpu_workers={cpu_workers}, cpu_load={cpu_load}%"
        )
        
        # 鍐欏叆 WAL
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
        
        # 鎵ц娉ㄥ叆
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        # stress-ng 鍦ㄥ悗鍙拌繍琛岋紝鍓嶅彴鍛戒护浼氱珛鍗宠繑鍥?
        if result.success or "dispatching hogs" in result.output.lower():
            logger.info(f"OS 璧勬簮鍘嬪姏娉ㄥ叆鎴愬姛: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"OS 璧勬簮鍘嬪姏娉ㄥ叆澶辫触: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"鎭㈠ OS 璧勬簮鍘嬪姏: node={ctx.target_node}")
        
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
        logger.warning("楠岃瘉澶辫触: stress-ng 浠嶅湪杩愯")
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
# RC-6: 鐑檷棰?
# ============================================================================

class ThermalThrottlingScenario(BaseScenario):
    """
    RC-6: 鐑檷棰戙€?
    
    閫氳繃闄愬埗 GPU 椋庢墖杞€熸垨浣跨敤 nvidia-smi 璁剧疆鍔熺巼闄愬埗锛?
    妯℃嫙 GPU 鐑檷棰戝満鏅€?
    
    鏁堟灉锛?
    - GPU 鏍稿績棰戠巼闄嶄綆
    - 鎺ㄧ悊寤惰繜澧炲姞
    - 鍚炲悙涓嬮檷
    """
    
    @property
    def name(self) -> str:
        return "thermal_throttling"
    
    @property
    def description(self) -> str:
        return "热降频 - 限制 GPU 功率模拟热降频"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        gpu_id = ctx.params.get("gpu_id", 0)
        power_limit = ctx.params.get("power_limit", 150)  # 鐡︾壒锛岄粯璁ら檷浣庡埌 150W
        duration = ctx.params.get("duration", 300)
        
        # 鍏堣幏鍙栧綋鍓嶅姛鐜囬檺鍒剁敤浜庢仮澶?
        get_power_cmd = f"nvidia-smi -i {gpu_id} --query-gpu=power.limit --format=csv,noheader,nounits"
        get_result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=get_power_cmd,
        )
        
        original_power = 300  # 榛樿鍊?
        if get_result.success:
            try:
                original_power = int(float(get_result.output.strip()))
            except ValueError:
                logger.warning(f"鏃犳硶瑙ｆ瀽鍘熷鍔熺巼闄愬埗: {get_result.output}")
        
        # 璁剧疆鍔熺巼闄愬埗
        inject_cmd = f"sudo nvidia-smi -i {gpu_id} -pl {power_limit}"
        recover_cmd = f"sudo nvidia-smi -i {gpu_id} -pl {original_power}"
        
        logger.info(
            f"娉ㄥ叆鐑檷棰? node={ctx.target_node}, "
            f"gpu_id={gpu_id}, power_limit={power_limit}W (鍘? {original_power}W)"
        )
        
        # 鍐欏叆 WAL
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
        
        # 鎵ц娉ㄥ叆
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"鐑檷棰戞敞鍏ユ垚鍔? {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"鐑檷棰戞敞鍏ュけ璐? {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        gpu_id = ctx.params.get("gpu_id", 0)
        original_power = ctx.params.get("original_power", 300)
        
        logger.info(f"鎭㈠鐑檷棰? node={ctx.target_node}, gpu_id={gpu_id}")
        
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
                if current_power >= original_power - 10:  # 鍏佽 10W 璇樊
                    logger.info(f"楠岃瘉閫氳繃: 鍔熺巼闄愬埗宸叉仮澶嶅埌 {current_power}W")
                    return True
                else:
                    logger.warning(f"楠岃瘉澶辫触: 鍔熺巼闄愬埗涓?{current_power}W锛屾湡鏈?>= {original_power}W")
                    return False
            except ValueError:
                logger.warning(f"鏃犳硶瑙ｆ瀽鍔熺巼闄愬埗: {result.output}")
                return True
        
        logger.warning(f"鏃犳硶妫€鏌ュ姛鐜囬檺鍒? {result.error}")
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
# 鍦烘櫙娉ㄥ唽鍒楄〃
# ============================================================================

SCENARIOS = [
    GPUContentionScenario,
    NetworkJitterScenario,
    StorageIOInterferenceScenario,
    PlatformCascadeScenario,
    OSResourcePressureScenario,
    ThermalThrottlingScenario,
]
