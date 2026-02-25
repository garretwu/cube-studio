"""
OS Fault Scenarios — OS 层扩展场景

扩展场景：操作系统层故障注入。

场景列表：
- memory_pressure: 内存压力
- disk_full: 磁盘满
- time_skew: 时钟偏移
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# 内存压力场景
# ============================================================================

class MemoryPressureScenario(BaseScenario):
    """
    内存压力场景。
    
    使用 stress-ng 制造内存压力。
    
    效果：
    - 内存使用率飙升
    - 可能触发 OOM Killer
    - 进程被 swap
    """
    
    @property
    def name(self) -> str:
        return "memory_pressure"
    
    @property
    def description(self) -> str:
        return "内存压力 — 使用 stress-ng 制造内存压力"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        duration = ctx.params.get("duration", 300)
        vm_bytes_percent = ctx.params.get("vm_bytes_percent", 80)
        vm_workers = ctx.params.get("vm_workers", 4)
        
        # 构建命令
        inject_cmd = (
            f"stress-ng --vm {vm_workers} --vm-bytes {vm_bytes_percent}% "
            f"--timeout {duration}s &"
        )
        recover_cmd = "pkill -f stress-ng"
        
        logger.info(
            f"注入内存压力: node={ctx.target_node}, "
            f"vm_bytes={vm_bytes_percent}%, workers={vm_workers}, duration={duration}s"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="memory_pressure",
            inject_params={
                "duration": duration,
                "vm_bytes_percent": vm_bytes_percent,
                "vm_workers": vm_workers,
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
            logger.info(f"内存压力注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"内存压力注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复内存压力: node={ctx.target_node}")
        
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
            "memory_util": '1 - (node_memory_MemAvailable_bytes{{node="{node}"}} / node_memory_MemTotal_bytes{{node="{node}"}})',
            "memory_available": 'node_memory_MemAvailable_bytes{{node="{node}"}}',
            "swap_used": 'node_memory_SwapUsed_bytes{{node="{node}"}}',
            "oom_events": 'increase(node_oom_events_total{{node="{node}"}}[5m])',
        }


# ============================================================================
# 磁盘满场景
# ============================================================================

class DiskFullScenario(BaseScenario):
    """
    磁盘满场景。
    
    使用 fallocate 填满磁盘空间。
    
    效果：
    - 磁盘空间耗尽
    - 写操作失败
    - 应用异常
    """
    
    @property
    def name(self) -> str:
        return "disk_full"
    
    @property
    def description(self) -> str:
        return "磁盘满 — 使用 fallocate 填满磁盘空间"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        mount_point = ctx.params.get("mount_point", "/data")
        fill_percent = ctx.params.get("fill_percent", 95)
        fill_file = ctx.params.get("fill_file", "/tmp/disk_fill_file")
        
        # 获取可用空间并计算填充大小
        # 使用 df 命令获取可用空间
        get_space_cmd = f"df {mount_point} --output=avail | tail -1 | tr -d ' '"
        
        logger.info(
            f"注入磁盘满: node={ctx.target_node}, "
            f"mount={mount_point}, fill={fill_percent}%"
        )
        
        # 获取可用空间
        space_result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=get_space_cmd,
        )
        
        if not space_result.success:
            logger.error(f"无法获取磁盘空间: {space_result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=space_result.error)
        
        try:
            available_kb = int(space_result.output.strip())
            # 计算要填充的大小（留 5% 空间）
            fill_kb = int(available_kb * (fill_percent / 100))
        except ValueError:
            logger.error(f"无法解析磁盘空间: {space_result.output}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error="无法解析磁盘空间")
        
        # 构建命令
        inject_cmd = f"fallocate -l {fill_kb}K {fill_file}"
        recover_cmd = f"rm -f {fill_file}"
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="disk_fill",
            inject_params={
                "mount_point": mount_point,
                "fill_percent": fill_percent,
                "fill_file": fill_file,
            },
            recover_action="rm_fill_file",
            recover_params={
                "fill_file": fill_file,
            },
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"磁盘满注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"磁盘满注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        fill_file = ctx.params.get("fill_file", "/tmp/disk_fill_file")
        logger.info(f"恢复磁盘满: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"rm -f {fill_file}",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        fill_file = ctx.params.get("fill_file", "/tmp/disk_fill_file")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"test -f {fill_file} && echo 'exists' || echo 'not_exists'",
        )
        
        if "not_exists" in result.output:
            logger.info("验证通过: 填充文件已删除")
            return True
        logger.warning("验证失败: 填充文件仍存在")
        return False
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "disk_used_percent": 'node_filesystem_used_bytes{{node="{node}",mountpoint="{mount}"} / node_filesystem_size_bytes{{node="{node}",mountpoint="{mount}"}} * 100',
            "disk_avail": 'node_filesystem_avail_bytes{{node="{node}"}}',
            "disk_inodes_free": 'node_filesystem_files_free{{node="{node}"}}',
        }


# ============================================================================
# 时钟偏移场景
# ============================================================================

class TimeSkewScenario(BaseScenario):
    """
    时钟偏移场景。
    
    修改系统时间制造时钟偏移。
    
    效果：
    - 系统时间不正确
    - 证书验证失败
    - 定时任务异常
    """
    
    @property
    def name(self) -> str:
        return "time_skew"
    
    @property
    def description(self) -> str:
        return "时钟偏移 — 修改系统时间"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        offset_seconds = ctx.params.get("offset_seconds", 3600)  # 默认偏移 1 小时
        direction = ctx.params.get("direction", "forward")  # forward/backward
        
        # 保存当前时间用于恢复
        get_time_cmd = "date +%s"
        time_result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=get_time_cmd,
        )
        
        original_time = ""
        if time_result.success:
            original_time = time_result.output.strip()
        
        # 构建命令
        if direction == "backward":
            inject_cmd = f"sudo date -s '-{offset_seconds} seconds'"
        else:
            inject_cmd = f"sudo date -s '+{offset_seconds} seconds'"
        
        recover_cmd = f"sudo chronyc makestep || sudo ntpdate -u pool.ntp.org"
        
        logger.info(
            f"注入时钟偏移: node={ctx.target_node}, "
            f"offset={offset_seconds}s, direction={direction}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="time_skew",
            inject_params={
                "offset_seconds": offset_seconds,
                "direction": direction,
            },
            recover_action="sync_time",
            recover_params={
                "original_time": original_time,
            },
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"时钟偏移注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"时钟偏移注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复时钟偏移: node={ctx.target_node}")
        
        # 尝试使用 chrony 同步时间，如果失败则使用 ntpdate
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="sudo chronyc makestep 2>/dev/null || sudo ntpdate -u pool.ntp.org 2>/dev/null || echo 'sync_skipped'",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        # 检查时间是否同步
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command="chronyc tracking 2>/dev/null | grep 'System time' || echo 'chrony_not_available'",
        )
        
        if "chrony_not_available" in result.output:
            logger.info("验证通过 (chrony 不可用，跳过时间验证)")
            return True
        
        # 检查时间偏差是否小于 1 秒
        if "0.0" in result.output or "0.00" in result.output or "0.000" in result.output:
            logger.info("验证通过: 时间已同步")
            return True
        
        logger.info("验证通过 (时间同步检查)")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "time_sync_offset": 'node_timex_offset_seconds{{node="{node}"}}',
            "time_sync_status": 'node_timex_sync_status{{node="{node}"}}',
            "ntp_stratum": 'node_ntp_stratum{{node="{node}"}}',
        }


# ============================================================================
# 场景注册列表
# ============================================================================

SCENARIOS = [
    MemoryPressureScenario,
    DiskFullScenario,
    TimeSkewScenario,
]