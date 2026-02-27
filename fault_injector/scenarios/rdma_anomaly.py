"""
RDMA Anomaly Scenarios 鈥?F-1~F-6 RDMA anomaly scenarios

蹇呴€夊満鏅細鍒堕€?RDMA 缃戠粶寮傚父鍜岀綉缁滃欢杩熸姈鍔ㄣ€?

鍦烘櫙鍒楄〃锛?
- F-1: pfc_deadlock - PFC 姝婚攣
- F-2: ecn_misconfiguration - ECN 鏍囪闃堝€奸敊閰?
- F-3: rdma_load_imbalance - 涓嶅潎琛?RDMA 璐熻浇
- F-4: rdma_link_flap - RDMA 閾捐矾闂存瓏鎬т腑鏂?
- F-5: roce_mtu_mismatch - RoCE 缃戠粶 MTU 涓嶄竴鑷?
- F-6: rdma_qos_downgrade - RDMA QoS 闄嶇骇
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)



# ============================================================================
# F-1: PFC 姝婚攣
# ============================================================================

class PFCDeadlockScenario(BaseScenario):
    """
    F-1: PFC 姝婚攣銆?
    
    閫氳繃 H3C 浜ゆ崲鏈?CLI 閰嶇疆 PFC 浼樺厛绾ф槧灏勶紝鍒堕€?Head-of-Line Blocking銆?
    
    鏁堟灉锛?
    - RDMA 娴侀噺瀹屽叏闃诲
    - NCCL AllReduce 瓒呮椂
    - 璁粌浠诲姟 hang 浣忔棤娉曟帹杩?
    """
    
    @property
    def name(self) -> str:
        return "pfc_deadlock"
    
    @property
    def description(self) -> str:
        return "PFC 姝婚攣 鈥?鍒堕€?Head-of-Line Blocking"
    
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
        
        logger.info(f"娉ㄥ叆 PFC 姝婚攣: switch={switch}, interface={interface}, priority={priority}")
        
        # 鍐欏叆 WAL
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
        
        # TODO: 瀹為檯鎵ц闇€瑕?SwitchChannel
        logger.warning("SwitchChannel not implemented, PFC deadlock injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        
        logger.info(f"鎭㈠ PFC 閰嶇疆: switch={switch}, interface={interface}")
        
        # TODO: 瀹為檯鎵ц闇€瑕?SwitchChannel
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "nccl_allreduce_latency": 'nccl_allreduce_latency_seconds',
            "pfc_pause_frames": 'pfc_pause_frames_total',
        }


# ============================================================================
# F-2: ECN 鏍囪闃堝€奸敊閰?
# ============================================================================

class ECNMisconfigurationScenario(BaseScenario):
    """
    F-2: ECN 鏍囪闃堝€奸敊閰嶃€?
    
    閫氳繃 H3C 浜ゆ崲鏈?CLI 淇敼 ECN 闃堝€艰缃€?
    
    鏁堟灉锛?
    - DCQCN 棰戠箒瑙﹀彂閫熺巼闄嶄綆
    - RDMA 鍚炲悙涓嶇ǔ瀹氾紝鍑虹幇鍛ㄦ湡鎬ф尝鍔?
    """
    
    @property
    def name(self) -> str:
        return "ecn_misconfiguration"
    
    @property
    def description(self) -> str:
        return "ECN 鏍囪闃堝€奸敊閰?鈥?淇敼浜ゆ崲鏈?ECN 閰嶇疆"
    
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
            f"娉ㄥ叆 ECN 閿欓厤: switch={switch}, interface={interface}, "
            f"min={min_threshold}, max={max_threshold}"
        )
        
        # 鍐欏叆 WAL
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
        
        # TODO: 瀹為檯鎵ц闇€瑕?SwitchChannel
        logger.warning("SwitchChannel not implemented, ECN injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        
        logger.info(f"鎭㈠ ECN 閰嶇疆: switch={switch}, interface={interface}")
        
        # TODO: 瀹為檯鎵ц闇€瑕?SwitchChannel
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "rdma_retrans": 'rdma_retransmissions_total',
            "ecn_marked_packets": 'ecn_marked_packets_total',
        }


# ============================================================================
# F-3: 涓嶅潎琛?RDMA 璐熻浇
# ============================================================================

class RDMALoadImbalanceScenario(BaseScenario):
    """
    F-3: 涓嶅潎琛?RDMA 璐熻浇銆?
    
    閫氳繃淇敼浜ゆ崲鏈?ECMP 鍝堝笇閰嶇疆锛屼娇澶氭潯閾捐矾璐熻浇涓嶅潎銆?
    
    鏁堟灉锛?
    - 閮ㄥ垎閾捐矾鎷ュ锛岄儴鍒嗛摼璺┖闂?
    - 鏁翠綋鍚炲悙涓嬮檷
    - NCCL AllReduce 鎬ц兘娉㈠姩
    """
    
    @property
    def name(self) -> str:
        return "rdma_load_imbalance"
    
    @property
    def description(self) -> str:
        return "涓嶅潎琛?RDMA 璐熻浇 鈥?淇敼 ECMP 鍝堝笇閰嶇疆"
    
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
            f"娉ㄥ叆 RDMA 璐熻浇涓嶅潎琛? switch={switch}, hash_algorithm={hash_algorithm}"
        )
        
        # 鍐欏叆 WAL
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
        
        # TODO: 瀹為檯鎵ц闇€瑕?SwitchChannel
        logger.warning("SwitchChannel not implemented, ECMP injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        
        logger.info(f"鎭㈠ ECMP 閰嶇疆: switch={switch}")
        
        # TODO: 瀹為檯鎵ц闇€瑕?SwitchChannel
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput_per_port": 'rdma_throughput_bytes_total{port=~"$port"}',
            "nccl_allreduce_latency": 'nccl_allreduce_latency_seconds',
            "link_utilization": 'link_utilization_ratio',
        }


# ============================================================================
# F-4: RDMA 閾捐矾闂存瓏鎬т腑鏂?
# ============================================================================

class RDMALinkFlapScenario(BaseScenario):
    """
    F-4: RDMA 閾捐矾闂存瓏鎬т腑鏂€?
    
    閫氳繃浜ゆ崲鏈?CLI 闂存瓏鎬?shutdown/undo shutdown 绔彛銆?
    
    鏁堟灉锛?
    - RDMA 杩炴帴鍛ㄦ湡鎬ф柇寮€閲嶅缓
    - NCCL 閫氫俊瓒呮椂閲嶈瘯
    - 璁粌浠诲姟鍙兘 hang 鎴?crash
    """
    
    @property
    def name(self) -> str:
        return "rdma_link_flap"
    
    @property
    def description(self) -> str:
        return "RDMA 閾捐矾闂存瓏鎬т腑鏂?鈥?浜ゆ崲鏈虹鍙?flap"
    
    @property
    def layer(self) -> str:
        return "hardware"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        flap_interval = ctx.params.get("flap_interval", 30)
        flap_duration = ctx.params.get("flap_duration", 5)
        
        logger.info(
            f"娉ㄥ叆 RDMA 閾捐矾 flap: switch={switch}, interface={interface}, "
            f"interval={flap_interval}s, duration={flap_duration}s"
        )
        
        # 鍐欏叆 WAL
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
        
        # TODO: 瀹為檯鎵ц闇€瑕?SwitchChannel
        logger.warning("SwitchChannel not implemented, link flap injection skipped")
        
        return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        switch = ctx.params.get("switch", "sw-200g")
        interface = ctx.params.get("interface", "HundredGigE 1/0/1")
        
        logger.info(f"鎭㈠ RDMA 閾捐矾: switch={switch}, interface={interface}")
        
        # 鍋滄 flap 鑴氭湰锛岀‘淇濈鍙?up
        # TODO: 瀹為檯鎵ц闇€瑕?SwitchChannel
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_link_status": 'rdma_link_status',
            "nccl_allreduce_latency": 'nccl_allreduce_latency_seconds',
            "link_flap_count": 'link_flap_events_total',
        }


# ============================================================================
# F-5: RoCE 缃戠粶 MTU 涓嶄竴鑷?
# ============================================================================

class RoCEMTUMismatchScenario(BaseScenario):
    """
    F-5: RoCE 缃戠粶 MTU 涓嶄竴鑷淬€?
    
    閫氳繃淇敼缃戝崱 MTU 閰嶇疆鍒堕€?MTU 涓嶅尮閰嶃€?
    
    鏁堟灉锛?
    - 澶у寘涓㈠け锛屽悶鍚愰闄?
    - RDMA 杩炴帴棰戠箒閲嶈瘯
    - NCCL 鎬ц兘涓ラ噸涓嬮檷
    """
    
    @property
    def name(self) -> str:
        return "roce_mtu_mismatch"
    
    @property
    def description(self) -> str:
        return "RoCE 缃戠粶 MTU 涓嶄竴鑷?鈥?淇敼缃戝崱 MTU"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        interface = ctx.params.get("interface", "eth0")
        mtu = ctx.params.get("mtu", 1500)  # 鏁呮剰璁惧皬鍒堕€犱笉鍖归厤
        
        inject_cmd = f"sudo ip link set dev {interface} mtu {mtu}"
        recover_cmd = f"sudo ip link set dev {interface} mtu 9000"  # 鎭㈠鍒?jumbo frame
        
        logger.info(
            f"娉ㄥ叆 MTU 涓嶅尮閰? node={ctx.target_node}, interface={interface}, mtu={mtu}"
        )
        
        # 鍐欏叆 WAL
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
        
        # 鎵ц娉ㄥ叆
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"MTU 涓嶅尮閰嶆敞鍏ユ垚鍔? {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"MTU 涓嶅尮閰嶆敞鍏ュけ璐? {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        interface = ctx.params.get("interface", "eth0")
        original_mtu = ctx.params.get("original_mtu", 9000)
        recover_cmd = f"sudo ip link set dev {interface} mtu {original_mtu}"
        
        logger.info(f"鎭㈠ MTU: node={ctx.target_node}, interface={interface}")
        
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
        
        # dry_run 妯″紡涓嬬洿鎺ヨ繑鍥?True
        if result.dry_run:
            logger.info(f"[DRY-RUN] 璺宠繃 MTU 楠岃瘉")
            return True
        
        if result.success:
            current_mtu = int(result.output.strip())
            if current_mtu == original_mtu:
                logger.info(f"楠岃瘉閫氳繃: MTU 宸叉仮澶嶅埌 {original_mtu}")
                return True
            else:
                logger.warning(f"楠岃瘉澶辫触: MTU 涓?{current_mtu}锛屾湡鏈?{original_mtu}")
                return False
        
        logger.warning(f"鏃犳硶妫€鏌?MTU: {result.error}")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "rdma_retrans": 'rdma_retransmissions_total',
            "mtu_errors": 'node_network_mtu_errors_total',
        }


# ============================================================================
# F-6: RDMA QoS 闄嶇骇
# ============================================================================

class RDMAQoSDowngradeScenario(BaseScenario):
    """
    F-6: RDMA QoS 闄嶇骇銆?
    
    閫氳繃淇敼缃戝崱鎴栦氦鎹㈡満鐨?DSCP/CoS 鏄犲皠锛岄檷浣?RDMA 娴侀噺浼樺厛绾с€?
    
    鏁堟灉锛?
    - RDMA 娴侀噺涓庢櫘閫?TCP 娴侀噺绔炰簤甯﹀
    - 鍚炲悙涓嶇ǔ瀹氾紝寤惰繜鍗囬珮
    - 璁粌鏃堕棿寤堕暱
    """
    
    @property
    def name(self) -> str:
        return "rdma_qos_downgrade"
    
    @property
    def description(self) -> str:
        return "RDMA QoS 降级 - 降低 RDMA 流量优先级"
    
    @property
    def layer(self) -> str:
        return "os"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        interface = ctx.params.get("interface", "eth0")
        # 灏?RDMA 娴侀噺鐨?DSCP 浠?26 (楂樹紭鍏堢骇) 闄嶄负 0 (best effort)
        dscp_value = ctx.params.get("dscp_value", 0)
        
        # 浣跨敤 tc 璁剧疆 DSCP 閲嶆爣璁?
        inject_cmd = (
            f"sudo tc qdisc add dev {interface} root handle 1: mqprio "
            f"num_tc 4 map 0 1 2 3 queues 4@0 4@4 4@8 4@12 hw 0"
        )
        recover_cmd = f"sudo tc qdisc del dev {interface} root"
        
        logger.info(
            f"娉ㄥ叆 RDMA QoS 闄嶇骇: node={ctx.target_node}, interface={interface}"
        )
        
        # 鍐欏叆 WAL
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
        
        # 鎵ц娉ㄥ叆
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"RDMA QoS 闄嶇骇娉ㄥ叆鎴愬姛: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"RDMA QoS 闄嶇骇娉ㄥ叆澶辫触: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        interface = ctx.params.get("interface", "eth0")
        recover_cmd = f"sudo tc qdisc del dev {interface} root"
        
        logger.info(f"鎭㈠ RDMA QoS: node={ctx.target_node}, interface={interface}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=recover_cmd,
        )
        
        # tc qdisc del 鍙兘鍥犱负 qdisc 涓嶅瓨鍦ㄨ€屽け璐ワ紝杩欐槸鍙帴鍙楃殑
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "rdma_throughput": 'rdma_throughput_bytes_total',
            "rdma_latency": 'rdma_latency_seconds',
            "qos_dropped_packets": 'qos_dropped_packets_total',
        }


# ============================================================================
# 鍦烘櫙娉ㄥ唽鍒楄〃
# ============================================================================

SCENARIOS = [
    PFCDeadlockScenario,
    ECNMisconfigurationScenario,
    RDMALoadImbalanceScenario,
    RDMALinkFlapScenario,
    RoCEMTUMismatchScenario,
    RDMAQoSDowngradeScenario,
]

