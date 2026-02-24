"""
NetworkJitterScenario — RC-2 网络延迟场景

使用 tc netem 注入网络延迟和抖动。
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult, NetworkJitterParams

logger = logging.getLogger(__name__)


class NetworkJitterScenario(BaseScenario):
    """
    RC-2: 网络延迟场景。
    
    使用 tc netem 注入网络延迟：
    - 注入命令: tc qdisc add dev eth0 root netem delay 50ms 100ms distribution pareto
    - 恢复命令: tc qdisc del dev eth0 root
    
    效果：
    - 延迟分布从正态变为长尾 (pareto)
    - P95/P50 比值 > 10
    - Stream 模式下 token 输出断断续续
    """
    
    @property
    def name(self) -> str:
        return "network_jitter"
    
    @property
    def description(self) -> str:
        return "网络延迟抖动 — 使用 tc netem 注入不确定延迟"
    
    @property
    def layer(self) -> str:
        return "os"
    
    def _parse_params(self, params: dict[str, Any]) -> NetworkJitterParams:
        """解析参数"""
        return NetworkJitterParams(
            delay_ms=params.get("delay_ms", 50),
            jitter_ms=params.get("jitter_ms", 100),
            distribution=params.get("distribution", "pareto"),
            loss_pct=params.get("loss_pct", 0),
            duration=params.get("duration", 300),
            interface=params.get("interface", "eth0"),
        )
    
    def _build_tc_command(self, params: NetworkJitterParams) -> str:
        """构建 tc netem 命令"""
        cmd = f"tc qdisc add dev {params.interface} root netem delay {params.delay_ms}ms"
        if params.jitter_ms > 0:
            cmd += f" {params.jitter_ms}ms"
        if params.distribution != "normal":
            cmd += f" distribution {params.distribution}"
        if params.loss_pct > 0:
            cmd += f" loss {params.loss_pct}%"
        return cmd
    
    def _build_recovery_command(self, interface: str) -> str:
        """构建恢复命令"""
        return f"tc qdisc del dev {interface} root"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        """
        注入网络延迟。
        
        执行流程：
        1. 解析参数
        2. 构建注入和恢复命令
        3. 先写入 WAL（安全机制）
        4. 执行注入命令
        
        Args:
            ctx: 故障注入上下文
            
        Returns:
            InjectResult: 注入结果
        """
        # 1. 解析参数
        params = self._parse_params(ctx.params)
        interface = params.interface
        
        # 2. 构建命令
        inject_cmd = self._build_tc_command(params)
        recover_cmd = self._build_recovery_command(interface)
        
        logger.info(
            f"注入网络延迟: node={ctx.target_node}, "
            f"delay={params.delay_ms}ms, jitter={params.jitter_ms}ms, "
            f"distribution={params.distribution}"
        )
        
        # 3. 写入 WAL（在执行之前）
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="tc_add_delay",
            inject_params={
                "node": ctx.target_node,
                "interface": interface,
                "delay_ms": params.delay_ms,
                "jitter_ms": params.jitter_ms,
                "distribution": params.distribution,
                "loss_pct": params.loss_pct,
            },
            recover_action="tc_del_qdisc",
            recover_params={
                "node": ctx.target_node,
                "interface": interface,
            },
        )
        
        # 4. 执行注入命令
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"网络延迟注入成功: {ctx.fault_id}")
            return InjectResult(
                success=True,
                fault_id=ctx.fault_id,
            )
        else:
            logger.error(f"网络延迟注入失败: {result.error}")
            return InjectResult(
                success=False,
                fault_id=ctx.fault_id,
                error=result.error,
            )
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        """
        恢复网络延迟。
        
        执行 tc qdisc del 命令删除 netem 规则。
        
        Args:
            ctx: 故障注入上下文
            
        Returns:
            RecoverResult: 恢复结果
        """
        interface = ctx.params.get("interface", "eth0")
        recover_cmd = self._build_recovery_command(interface)
        
        logger.info(f"恢复网络延迟: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=recover_cmd,
        )
        
        if result.success:
            logger.info(f"网络延迟恢复成功: {ctx.fault_id}")
            ctx.rollback.mark_recovered(ctx.fault_id)
            return RecoverResult(
                success=True,
                fault_id=ctx.fault_id,
            )
        else:
            # tc qdisc del 可能因为 qdisc 不存在而失败，这是可接受的
            if "No such file or directory" in result.error or "Cannot delete" in result.error:
                logger.warning(f"qdisc 不存在或已删除: {ctx.fault_id}")
                ctx.rollback.mark_recovered(ctx.fault_id)
                return RecoverResult(
                    success=True,
                    fault_id=ctx.fault_id,
                )
            
            logger.error(f"网络延迟恢复失败: {result.error}")
            ctx.rollback.mark_failed(ctx.fault_id)
            return RecoverResult(
                success=False,
                fault_id=ctx.fault_id,
                error=result.error,
            )
    
    async def verify(self, ctx: FaultContext) -> bool:
        """
        验证恢复是否成功。
        
        检查 tc qdisc 是否已删除（netem 不再存在）。
        
        Args:
            ctx: 故障注入上下文
            
        Returns:
            bool: 恢复是否成功
        """
        interface = ctx.params.get("interface", "eth0")
        check_cmd = f"tc qdisc show dev {interface}"
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=check_cmd,
        )
        
        if not result.success:
            logger.warning(f"无法检查 tc qdisc: {result.error}")
            return True  # 假设成功
        
        # 检查是否还有 netem
        if "netem" in result.output:
            logger.warning(f"netem 仍然存在: {result.output}")
            return False
        
        logger.info(f"验证通过: netem 已删除")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        """返回监控 PromQL"""
        return {
            "inference_p50": 'histogram_quantile(0.5, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p95": 'histogram_quantile(0.95, rate(vllm:request_duration_seconds_bucket[1m]))',
            "inference_p99": 'histogram_quantile(0.99, rate(vllm:request_duration_seconds_bucket[1m]))',
            "network_latency": 'histogram_quantile(0.95, rate(network_latency_seconds_bucket[1m]))',
        }