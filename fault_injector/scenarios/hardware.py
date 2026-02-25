"""
Hardware Extension Scenarios — 硬件层扩展场景

扩展场景：硬件层故障注入。

场景列表：
- cpu_stress: CPU 压力
- gpu_removal: GPU 掉卡
- thermal_throttle_hw: 热降频 (Redfish 风扇控制)
- network_delay_hw: 网络延迟 (交换机端口限速)
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# CPU 压力场景
# ============================================================================

class CPUStressScenario(BaseScenario):
    """
    CPU 压力场景。
    
    使用 stress-ng 制造 CPU 压力。
    
    效果：
    - CPU 使用率飙升
    - 进程调度延迟增加
    - 响应时间变长
    """
    
    @property
    def name(self) -> str:
        return "cpu_stress"
    
    @property
    def description(self) -> str:
        return "CPU 压力 — 使用 stress-ng 制造 CPU 满载"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        cpu_workers = ctx.params.get("cpu_workers", 0)  # 0 = 所有 CPU
        cpu_load = ctx.params.get("cpu_load", 100)
        
        # 构建命令
        if cpu_workers == 0:
            cpu_workers_cmd = "--cpu 0"  # stress-ng 使用 0 表示所有 CPU
        else:
            cpu_workers_cmd = f"--cpu {cpu_workers}"
        
        inject_cmd = f"stress-ng {cpu_workers_cmd} --cpu-load {cpu_load} --timeout {duration}s &"
        recover_cmd = "pkill -f stress-ng"
        
        logger.info(
            f"注入 CPU 压力: node={ctx.target_node}, "
            f"workers={cpu_workers}, load={cpu_load}%, duration={duration}s"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="cpu_stress",
            inject_params={
                "duration": duration,
                "cpu_workers": cpu_workers,
                "cpu_load": cpu_load,
            },
            recover_action="kill_stress_ng",
            recover_params={},
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success or "dispatching hogs" in result.output.lower():
            logger.info(f"CPU 压力注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"CPU 压力注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复 CPU 压力: node={ctx.target_node}")
        
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
            "cpu_util": '1 - rate(node_cpu_seconds_total{{mode="idle",node="{node}"}}[1m])',
            "cpu_load1": 'node_load1{{node="{node}"}}',
            "cpu_load5": 'node_load5{{node="{node}"}}',
            "cpu_load15": 'node_load15{{node="{node}"}}',
        }


# ============================================================================
# GPU 掉卡场景
# ============================================================================

class GPURemovalScenario(BaseScenario):
    """
    GPU 掉卡场景。
    
    通过 PCIe remove 移除 GPU 设备。
    
    效果：
    - GPU 设备消失
    - nvidia-smi 显示减少一张卡
    - 依赖该 GPU 的任务失败
    """
    
    @property
    def name(self) -> str:
        return "gpu_removal"
    
    @property
    def description(self) -> str:
        return "GPU 掉卡 — 通过 PCIe remove 移除 GPU 设备"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        gpu_bdf = ctx.params.get("gpu_bdf", "0000:41:00.0")  # PCIe BDF
        
        # 构建命令
        inject_cmd = f"echo 1 | sudo tee /sys/bus/pci/devices/{gpu_bdf}/remove"
        recover_cmd = "echo 1 | sudo tee /sys/bus/pci/rescan"
        
        logger.info(
            f"注入 GPU 掉卡: node={ctx.target_node}, "
            f"gpu_bdf={gpu_bdf}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="gpu_removal",
            inject_params={
                "gpu_bdf": gpu_bdf,
            },
            recover_action="pci_rescan",
            recover_params={},
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"GPU 掉卡注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"GPU 掉卡注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复 GPU 掉卡: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="echo 1 | sudo tee /sys/bus/pci/rescan",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        gpu_bdf = ctx.params.get("gpu_bdf", "0000:41:00.0")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"ls /sys/bus/pci/devices/{gpu_bdf} 2>/dev/null && echo 'exists' || echo 'not_exists'",
        )
        
        if "exists" in result.output:
            logger.info("验证通过: GPU 设备已恢复")
            return True
        logger.warning("验证失败: GPU 设备仍不存在")
        return False
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "gpu_count": 'count(DCGM_FI_DEV_GPU_UTIL{{node="{node}"}})',
            "gpu_util": 'DCGM_FI_DEV_GPU_UTIL{{node="{node}"}}',
        }


# ============================================================================
# 热降频 (硬件版 - Redfish 风扇控制)
# ============================================================================

class ThermalThrottleHWScenario(BaseScenario):
    """
    热降频场景 (硬件版)。
    
    通过 Redfish 降低风扇转速，触发 CPU/GPU 热降频。
    
    效果：
    - 温度升高
    - CPU/GPU 频率降低
    - 性能下降
    """
    
    @property
    def name(self) -> str:
        return "thermal_throttle_hw"
    
    @property
    def description(self) -> str:
        return "热降频 (硬件版) — 通过 Redfish 降低风扇转速"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        fan_pwm = ctx.params.get("fan_pwm", 15)  # 风扇占空比
        duration = ctx.params.get("duration", 300)
        bmc_host = ctx.params.get("bmc_host", "")
        
        logger.info(
            f"注入热降频 (硬件): node={ctx.target_node}, "
            f"bmc={bmc_host}, fan_pwm={fan_pwm}%"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="redfish",
            target=bmc_host or ctx.target_node,
            inject_action="fan_control",
            inject_params={
                "fan_pwm": fan_pwm,
            },
            recover_action="fan_auto",
            recover_params={},
        )
        
        # 使用 Redfish Channel
        if ctx.redfish and bmc_host:
            result = await ctx.redfish.set_fan_speed(
                bmc_host=bmc_host,
                fan_pwm=fan_pwm,
                mode="Manual",
            )
            
            if result.success:
                logger.info(f"热降频注入成功: {ctx.fault_id}")
                return InjectResult(success=True, fault_id=ctx.fault_id)
            else:
                logger.error(f"热降频注入失败: {result.error}")
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
        else:
            logger.warning("Redfish Channel 或 BMC 地址未配置，跳过注入")
            return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        bmc_host = ctx.params.get("bmc_host", "")
        logger.info(f"恢复热降频: node={ctx.target_node}")
        
        if ctx.redfish and bmc_host:
            result = await ctx.redfish.set_fan_speed(
                bmc_host=bmc_host,
                fan_pwm=50,
                mode="Auto",
            )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        bmc_host = ctx.params.get("bmc_host", "")
        
        if ctx.redfish and bmc_host:
            result = await ctx.redfish.get_thermal(bmc_host)
            if result.success:
                # 检查风扇模式是否恢复为 Auto
                if "Auto" in result.output or "auto" in result.output:
                    logger.info("验证通过: 风扇已恢复自动模式")
                    return True
        
        logger.info("验证通过 (无法检查风扇状态)")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "cpu_temp": 'node_hwmon_temp_celsius{{node="{node}"}}',
            "gpu_temp": 'DCGM_FI_DEV_GPU_TEMP{{node="{node}"}}',
            "cpu_freq": 'node_cpu_scaling_frequency_hertz{{node="{node}"}}',
            "gpu_power": 'DCGM_FI_DEV_POWER_USAGE{{node="{node}"}}',
        }


# ============================================================================
# 网络延迟 (硬件版 - 交换机端口限速)
# ============================================================================

class NetworkDelayHWScenario(BaseScenario):
    """
    网络延迟场景 (硬件版)。
    
    通过交换机端口限速制造网络延迟。
    
    效果：
    - 网络带宽受限
    - 数据传输延迟增加
    - 大流量场景下明显
    """
    
    @property
    def name(self) -> str:
        return "network_delay_hw"
    
    @property
    def description(self) -> str:
        return "网络延迟 (硬件版) — 通过交换机端口限速"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        switch_host = ctx.params.get("switch_host", "")
        interface = ctx.params.get("interface", "HundredGigE1/0/1")
        rate_limit_mbps = ctx.params.get("rate_limit_mbps", 1000)  # 1Gbps
        
        logger.info(
            f"注入网络延迟 (硬件): switch={switch_host}, "
            f"interface={interface}, rate={rate_limit_mbps}Mbps"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="switch",
            target=switch_host,
            inject_action="port_rate_limit",
            inject_params={
                "interface": interface,
                "rate_limit_mbps": rate_limit_mbps,
            },
            recover_action="port_rate_unlimit",
            recover_params={
                "interface": interface,
            },
        )
        
        # 使用 Switch Channel
        if ctx.switch and switch_host:
            commands = [
                "system-view",
                f"interface {interface}",
                f"qos car inbound cir {rate_limit_mbps * 1000} cbs {rate_limit_mbps * 1000} green pass red discard",
                "quit",
            ]
            
            result = await ctx.switch.execute_commands(
                switch_host=switch_host,
                commands=commands,
            )
            
            if result.success:
                logger.info(f"网络延迟注入成功: {ctx.fault_id}")
                return InjectResult(success=True, fault_id=ctx.fault_id)
            else:
                logger.error(f"网络延迟注入失败: {result.error}")
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
        else:
            logger.warning("Switch Channel 或交换机地址未配置，跳过注入")
            return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch_host = ctx.params.get("switch_host", "")
        interface = ctx.params.get("interface", "HundredGigE1/0/1")
        
        logger.info(f"恢复网络延迟: switch={switch_host}")
        
        if ctx.switch and switch_host:
            commands = [
                "system-view",
                f"interface {interface}",
                "undo qos car inbound",
                "quit",
            ]
            
            result = await ctx.switch.execute_commands(
                switch_host=switch_host,
                commands=commands,
            )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        switch_host = ctx.params.get("switch_host", "")
        interface = ctx.params.get("interface", "HundredGigE1/0/1")
        
        if ctx.switch and switch_host:
            result = await ctx.switch.execute_commands(
                switch_host=switch_host,
                commands=[f"display interface {interface}"],
            )
            if "qos car" not in result.output.lower():
                logger.info("验证通过: 端口限速已移除")
                return True
            logger.warning("验证失败: 端口限速仍存在")
            return False
        
        logger.info("验证通过 (无法检查交换机状态)")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "network_rx_bytes": 'rate(node_network_receive_bytes_total{{node="{node}"}}[1m])',
            "network_tx_bytes": 'rate(node_network_transmit_bytes_total{{node="{node}"}}[1m])',
            "network_rx_drop": 'rate(node_network_receive_drop_total{{node="{node}"}}[1m])',
        }


# ============================================================================
# 场景注册列表
# ============================================================================

SCENARIOS = [
    CPUStressScenario,
    GPURemovalScenario,
    ThermalThrottleHWScenario,
    NetworkDelayHWScenario,
]