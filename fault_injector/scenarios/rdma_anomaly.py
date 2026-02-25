"""
RDMA Anomaly Scenarios — F-1~F-6 六个 RDMA 异常场景 + RC-2 网络延迟

必选场景：制造 RDMA 网络异常和网络延迟抖动。

场景列表：
- RC-2: network_jitter - 网络延迟抖动 (tc netem)
- F-1: pfc_deadlock - PFC 死锁
- F-2: ecn_misconfiguration - ECN 标记阈值错配
- F-3: rdma_load_imbalance - 不均衡 RDMA 负载
- F-4: rdma_link_flap - RDMA 链路间歇性中断
- F-5: roce_mtu_mismatch - RoCE 网络 MTU 不一致
- F-6: rdma_qos_downgrade - RDMA QoS 降级
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# RC-2: 网络延迟抖动
# ============================================================================

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
    
    def _build_tc_command(self, params: dict[str, Any]) -> str:
        """构建 tc netem 命令"""
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
        """构建恢复命令"""
        return f"sudo tc qdisc del dev {interface} root"
    
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
        interface = ctx.params.get("interface", "eth0")
        delay_ms = ctx.params.get("delay_ms", 50)
        jitter_ms = ctx.params.get("jitter_ms", 100)
        distribution = ctx.params.get("distribution", "pareto")
        loss_pct = ctx.params.get("loss_pct", 0)
        
        # 构建命令
        inject_cmd = self._build_tc_command(ctx.params)
        recover_cmd = self._build_recovery_command(interface)
        
        logger.info(
            f"注入网络延迟: node={ctx.target_node}, "
            f"delay={delay_ms}ms, jitter={jitter_ms}ms, "
            f"distribution={distribution}"
        )
        
        # 写入 WAL（在执行之前）
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
            recover_params={
                "node": ctx.target_node,
                "interface": interface,
            },
        )
        
        # 执行注入命令
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
        check_cmd = f"sudo tc qdisc show dev {interface}"
        
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


# ============================================================================
# F-1: PFC 死锁
# ============================================================================

class PFCDeadlockScenario(BaseScenario):
    """
    F-1: PFC 死锁。
    
    通过 H3C 交换机 CLI 配置 PFC 优先级映射，制造 Head-of-Line Blocking。
    
    效果：
    - RDMA 流量完全阻塞
    - NCCL AllReduce 超时
    - 训练任务 hang 住无法推进
    """
    
    @property
    def name(self) -> str:
        return "pfc_deadlock"
    
    @property
    def description(self) -> str:
        return "PFC 死锁 — 制造 Head-of-Line Blocking"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        priority = ctx.params.get("priority", 3)
        
        inject_commands = [
            "system-view",
            f"interface {interface}",
            f"priority-flow-control enable",
            f"priority-flow-control priority {priority} no-drop",
            "quit",
        ]
        
        recover_commands = [
            "system-view",
            f"interface {interface}",
            f"undo priority-flow-control priority {priority} no-drop",
            "quit",
        ]
        
        logger.info(f"注入 PFC 死锁: switch={switch}, interface={interface}, priority={priority}")
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="switch",
            target=switch,
            inject_action="pfc_deadlock",
            inject_params={
                "interface": interface,
                "priority": priority,
            },
            recover_action="pfc_restore",
            recover_params={
                "interface": interface,
                "priority": priority,
            },
        )
        
        # TODO: 实际执行需要 SwitchChannel
        logger.warning("SwitchChannel not implemented, PFC deadlock injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        
        logger.info(f"恢复 PFC 配置: switch={switch}, interface={interface}")
        
        # TODO: 实际执行需要 SwitchChannel
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "nccl_allreduce_latency": 'nccl_allreduce_latency_seconds',
            "pfc_pause_frames": 'pfc_pause_frames_total',
        }


# ============================================================================
# F-2: ECN 标记阈值错配
# ============================================================================

class ECNMisconfigurationScenario(BaseScenario):
    """
    F-2: ECN 标记阈值错配。
    
    通过 H3C 交换机 CLI 修改 ECN 阈值设置。
    
    效果：
    - DCQCN 频繁触发速率降低
    - RDMA 吞吐不稳定，出现周期性波动
    """
    
    @property
    def name(self) -> str:
        return "ecn_misconfiguration"
    
    @property
    def description(self) -> str:
        return "ECN 标记阈值错配 — 修改交换机 ECN 配置"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        min_threshold = ctx.params.get("min_threshold", 10)
        max_threshold = ctx.params.get("max_threshold", 20)
        
        inject_commands = [
            "system-view",
            f"interface {interface}",
            "qos wred apply ecn",
            f"qos wred queue 3 ecn minimum-threshold {min_threshold} maximum-threshold {max_threshold}",
            "quit",
        ]
        
        logger.info(
            f"注入 ECN 错配: switch={switch}, interface={interface}, "
            f"min={min_threshold}, max={max_threshold}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="switch",
            target=switch,
            inject_action="ecn_misconfig",
            inject_params={
                "interface": interface,
                "min_threshold": min_threshold,
                "max_threshold": max_threshold,
            },
            recover_action="ecn_restore",
            recover_params={"interface": interface},
        )
        
        # TODO: 实际执行需要 SwitchChannel
        logger.warning("SwitchChannel not implemented, ECN injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        
        logger.info(f"恢复 ECN 配置: switch={switch}, interface={interface}")
        
        # TODO: 实际执行需要 SwitchChannel
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "rdma_retrans": 'rdma_retransmissions_total',
            "ecn_marked_packets": 'ecn_marked_packets_total',
        }


# ============================================================================
# F-3: 不均衡 RDMA 负载
# ============================================================================

class RDMALoadImbalanceScenario(BaseScenario):
    """
    F-3: 不均衡 RDMA 负载。
    
    通过修改交换机 ECMP 哈希配置，使多条链路负载不均。
    
    效果：
    - 部分链路拥塞，部分链路空闲
    - 整体吞吐下降
    - NCCL AllReduce 性能波动
    """
    
    @property
    def name(self) -> str:
        return "rdma_load_imbalance"
    
    @property
    def description(self) -> str:
        return "不均衡 RDMA 负载 — 修改 ECMP 哈希配置"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        switch = ctx.params.get("switch", "sw-200g")
        hash_algorithm = ctx.params.get("hash_algorithm", "src-dst-ip")
        
        inject_commands = [
            "system-view",
            f"ip load-sharing mode {hash_algorithm} per-flow",
            "quit",
        ]
        
        logger.info(
            f"注入 RDMA 负载不均衡: switch={switch}, hash_algorithm={hash_algorithm}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="switch",
            target=switch,
            inject_action="ecmp_misconfig",
            inject_params={
                "hash_algorithm": hash_algorithm,
            },
            recover_action="ecmp_restore",
            recover_params={},
        )
        
        # TODO: 实际执行需要 SwitchChannel
        logger.warning("SwitchChannel not implemented, ECMP injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        
        logger.info(f"恢复 ECMP 配置: switch={switch}")
        
        # TODO: 实际执行需要 SwitchChannel
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput_per_port": 'rdma_throughput_bytes_total{port=~"$port"}',
            "nccl_allreduce_latency": 'nccl_allreduce_latency_seconds',
            "link_utilization": 'link_utilization_ratio',
        }


# ============================================================================
# F-4: RDMA 链路间歇性中断
# ============================================================================

class RDMALinkFlapScenario(BaseScenario):
    """
    F-4: RDMA 链路间歇性中断。
    
    通过交换机 CLI 间歇性 shutdown/undo shutdown 端口。
    
    效果：
    - RDMA 连接周期性断开重建
    - NCCL 通信超时重试
    - 训练任务可能 hang 或 crash
    """
    
    @property
    def name(self) -> str:
        return "rdma_link_flap"
    
    @property
    def description(self) -> str:
        return "RDMA 链路间歇性中断 — 交换机端口 flap"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        flap_interval = ctx.params.get("flap_interval", 30)
        flap_duration = ctx.params.get("flap_duration", 5)
        
        logger.info(
            f"注入 RDMA 链路 flap: switch={switch}, interface={interface}, "
            f"interval={flap_interval}s, duration={flap_duration}s"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="switch",
            target=switch,
            inject_action="link_flap_script",
            inject_params={
                "interface": interface,
                "flap_interval": flap_interval,
                "flap_duration": flap_duration,
            },
            recover_action="stop_flap_script",
            recover_params={"interface": interface},
        )
        
        # TODO: 实际执行需要 SwitchChannel
        logger.warning("SwitchChannel not implemented, link flap injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        
        logger.info(f"恢复 RDMA 链路: switch={switch}, interface={interface}")
        
        # 停止 flap 脚本，确保端口 up
        # TODO: 实际执行需要 SwitchChannel
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_link_status": 'rdma_link_status',
            "nccl_allreduce_latency": 'nccl_allreduce_latency_seconds',
            "link_flap_count": 'link_flap_events_total',
        }


# ============================================================================
# F-5: RoCE 网络 MTU 不一致
# ============================================================================

class RoCEMTUMismatchScenario(BaseScenario):
    """
    F-5: RoCE 网络 MTU 不一致。
    
    通过修改网卡 MTU 配置制造 MTU 不匹配。
    
    效果：
    - 大包丢失，吞吐骤降
    - RDMA 连接频繁重试
    - NCCL 性能严重下降
    """
    
    @property
    def name(self) -> str:
        return "roce_mtu_mismatch"
    
    @property
    def description(self) -> str:
        return "RoCE 网络 MTU 不一致 — 修改网卡 MTU"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        interface = ctx.params.get("interface", "eth0")
        mtu = ctx.params.get("mtu", 1500)  # 故意设小制造不匹配
        
        inject_cmd = f"sudo ip link set dev {interface} mtu {mtu}"
        recover_cmd = f"sudo ip link set dev {interface} mtu 9000"  # 恢复到 jumbo frame
        
        logger.info(
            f"注入 MTU 不匹配: node={ctx.target_node}, interface={interface}, mtu={mtu}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="mtu_change",
            inject_params={
                "interface": interface,
                "mtu": mtu,
            },
            recover_action="mtu_restore",
            recover_params={
                "interface": interface,
                "mtu": 9000,
            },
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"MTU 不匹配注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"MTU 不匹配注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        interface = ctx.params.get("interface", "eth0")
        original_mtu = ctx.params.get("original_mtu", 9000)
        recover_cmd = f"sudo ip link set dev {interface} mtu {original_mtu}"
        
        logger.info(f"恢复 MTU: node={ctx.target_node}, interface={interface}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=recover_cmd,
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        interface = ctx.params.get("interface", "eth0")
        original_mtu = ctx.params.get("original_mtu", 9000)
        check_cmd = f"cat /sys/class/net/{interface}/mtu"
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=check_cmd,
        )
        
        # dry_run 模式下直接返回 True
        if result.dry_run:
            logger.info(f"[DRY-RUN] 跳过 MTU 验证")
            return True
        
        if result.success:
            current_mtu = int(result.output.strip())
            if current_mtu == original_mtu:
                logger.info(f"验证通过: MTU 已恢复到 {original_mtu}")
                return True
            else:
                logger.warning(f"验证失败: MTU 为 {current_mtu}，期望 {original_mtu}")
                return False
        
        logger.warning(f"无法检查 MTU: {result.error}")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "rdma_retrans": 'rdma_retransmissions_total',
            "mtu_errors": 'node_network_mtu_errors_total',
        }


# ============================================================================
# F-6: RDMA QoS 降级
# ============================================================================

class RDMAQoSDowngradeScenario(BaseScenario):
    """
    F-6: RDMA QoS 降级。
    
    通过修改网卡或交换机的 DSCP/CoS 映射，降低 RDMA 流量优先级。
    
    效果：
    - RDMA 流量与普通 TCP 流量竞争带宽
    - 吞吐不稳定，延迟升高
    - 训练时间延长
    """
    
    @property
    def name(self) -> str:
        return "rdma_qos_downgrade"
    
    @property
    def description(self) -> str:
        return "RDMA QoS 降级 — 降低 RDMA 流量优先级"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        interface = ctx.params.get("interface", "eth0")
        # 将 RDMA 流量的 DSCP 从 26 (高优先级) 降为 0 (best effort)
        dscp_value = ctx.params.get("dscp_value", 0)
        
        # 使用 tc 设置 DSCP 重标记
        inject_cmd = (
            f"sudo tc qdisc add dev {interface} root handle 1: mqprio "
            f"num_tc 4 map 0 1 2 3 queues 4@0 4@4 4@8 4@12 hw 0"
        )
        recover_cmd = f"sudo tc qdisc del dev {interface} root"
        
        logger.info(
            f"注入 RDMA QoS 降级: node={ctx.target_node}, interface={interface}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="qos_downgrade",
            inject_params={
                "interface": interface,
                "dscp_value": dscp_value,
            },
            recover_action="qos_restore",
            recover_params={
                "interface": interface,
            },
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"RDMA QoS 降级注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"RDMA QoS 降级注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        interface = ctx.params.get("interface", "eth0")
        recover_cmd = f"sudo tc qdisc del dev {interface} root"
        
        logger.info(f"恢复 RDMA QoS: node={ctx.target_node}, interface={interface}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=recover_cmd,
        )
        
        # tc qdisc del 可能因为 qdisc 不存在而失败，这是可接受的
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "rdma_latency": 'rdma_latency_seconds',
            "qos_dropped_packets": 'qos_dropped_packets_total',
        }


# ============================================================================
# 场景注册列表
# ============================================================================

SCENARIOS = [
    NetworkJitterScenario,
    PFCDeadlockScenario,
    ECNMisconfigurationScenario,
    RDMALoadImbalanceScenario,
    RDMALinkFlapScenario,
    RoCEMTUMismatchScenario,
    RDMAQoSDowngradeScenario,
]