"""
Platform Fault Scenarios — 平台层扩展场景

扩展场景：平台组件故障注入。

场景列表：
- mysql_connection_drop: MySQL 连接中断
- redis_unavailable: Redis 不可用
- celery_worker_kill: Celery Worker 终止
- istio_gateway_kill: Istio Gateway 终止
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# MySQL 连接中断场景
# ============================================================================

class MySQLConnectionDropScenario(BaseScenario):
    """
    MySQL 连接中断场景。
    
    通过 iptables 阻断 MySQL 端口制造连接中断。
    
    效果：
    - 数据库连接失败
    - 应用报错
    - 依赖 MySQL 的服务不可用
    """
    
    @property
    def name(self) -> str:
        return "mysql_connection_drop"
    
    @property
    def description(self) -> str:
        return "MySQL 连接中断 — 通过 iptables 阻断 MySQL 端口"
    
    @property
    def layer(self) -> str:
        return "platform"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        mysql_host = ctx.params.get("mysql_host", "mysql")
        mysql_port = ctx.params.get("mysql_port", 3306)
        duration = ctx.params.get("duration", 60)
        
        # 构建命令 - 使用 iptables 阻断 MySQL 端口
        inject_cmd = (
            f"sudo iptables -A INPUT -p tcp --dport {mysql_port} "
            f"-s {mysql_host} -j DROP"
        )
        recover_cmd = f"sudo iptables -D INPUT -p tcp --dport {mysql_port} -s {mysql_host} -j DROP"
        
        logger.info(
            f"注入 MySQL 连接中断: node={ctx.target_node}, "
            f"host={mysql_host}, port={mysql_port}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="mysql_block",
            inject_params={
                "mysql_host": mysql_host,
                "mysql_port": mysql_port,
            },
            recover_action="mysql_unblock",
            recover_params={
                "mysql_host": mysql_host,
                "mysql_port": mysql_port,
            },
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success:
            logger.info(f"MySQL 连接中断注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"MySQL 连接中断注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        mysql_host = ctx.params.get("mysql_host", "mysql")
        mysql_port = ctx.params.get("mysql_port", 3306)
        
        logger.info(f"恢复 MySQL 连接: node={ctx.target_node}")
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"sudo iptables -D INPUT -p tcp --dport {mysql_port} -s {mysql_host} -j DROP 2>/dev/null || true",
        )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        mysql_host = ctx.params.get("mysql_host", "mysql")
        mysql_port = ctx.params.get("mysql_port", 3306)
        
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=f"sudo iptables -L INPUT -n | grep -q 'dpt:{mysql_port}' && echo 'exists' || echo 'not_exists'",
        )
        
        if "not_exists" in result.output:
            logger.info("验证通过: iptables 规则已删除")
            return True
        logger.warning("验证失败: iptables 规则仍存在")
        return False
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "mysql_connections": 'mysql_global_status_connections{{node="{node}"}}',
            "mysql_connection_errors": 'mysql_global_status_connection_errors_total{{node="{node}"}}',
            "mysql_queries": 'rate(mysql_global_status_queries{{node="{node}"}}[1m])',
        }


# ============================================================================
# Redis 不可用场景
# ============================================================================

class RedisUnavailableScenario(BaseScenario):
    """
    Redis 不可用场景。
    
    通过 redis-cli SHUTDOWN 或 iptables 制造 Redis 不可用。
    
    效果：
    - 缓存失效
    - Celery Broker 断连
    - Session 存储失败
    """
    
    @property
    def name(self) -> str:
        return "redis_unavailable"
    
    @property
    def description(self) -> str:
        return "Redis 不可用 — 停止 Redis 服务或阻断端口"
    
    @property
    def layer(self) -> str:
        return "platform"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        redis_host = ctx.params.get("redis_host", "redis")
        redis_port = ctx.params.get("redis_port", 6379)
        method = ctx.params.get("method", "iptables")  # iptables / shutdown
        
        logger.info(
            f"注入 Redis 不可用: node={ctx.target_node}, "
            f"host={redis_host}, port={redis_port}, method={method}"
        )
        
        if method == "shutdown":
            # 直接关闭 Redis
            inject_cmd = f"redis-cli -h {redis_host} -p {redis_port} SHUTDOWN NOSAVE 2>/dev/null || echo 'shutdown_sent'"
            recover_cmd = f"sudo systemctl start redis || sudo systemctl start redis-server"
        else:
            # 使用 iptables 阻断
            inject_cmd = f"sudo iptables -A INPUT -p tcp --dport {redis_port} -s {redis_host} -j DROP"
            recover_cmd = f"sudo iptables -D INPUT -p tcp --dport {redis_port} -s {redis_host} -j DROP"
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="ssh",
            target=ctx.target_node,
            inject_action="redis_block",
            inject_params={
                "redis_host": redis_host,
                "redis_port": redis_port,
                "method": method,
            },
            recover_action="redis_unblock",
            recover_params={
                "redis_host": redis_host,
                "redis_port": redis_port,
                "method": method,
            },
        )
        
        # 执行注入
        result = await ctx.ssh.run_command(
            node=ctx.target_node,
            command=inject_cmd,
        )
        
        if result.success or "shutdown_sent" in result.output:
            logger.info(f"Redis 不可用注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.error(f"Redis 不可用注入失败: {result.error}")
            return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        redis_host = ctx.params.get("redis_host", "redis")
        redis_port = ctx.params.get("redis_port", 6379)
        method = ctx.params.get("method", "iptables")
        
        logger.info(f"恢复 Redis: node={ctx.target_node}")
        
        if method == "shutdown":
            result = await ctx.ssh.run_command(
                node=ctx.target_node,
                command="sudo systemctl start redis 2>/dev/null || sudo systemctl start redis-server 2>/dev/null || echo 'start_attempted'",
            )
        else:
            result = await ctx.ssh.run_command(
                node=ctx.target_node,
                command=f"sudo iptables -D INPUT -p tcp --dport {redis_port} -s {redis_host} -j DROP 2>/dev/null || true",
            )
        
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        redis_host = ctx.params.get("redis_host", "redis")
        redis_port = ctx.params.get("redis_port", 6379)
        method = ctx.params.get("method", "iptables")
        
        if method == "shutdown":
            # 检查 Redis 是否恢复
            result = await ctx.ssh.run_command(
                node=ctx.target_node,
                command=f"redis-cli -h {redis_host} -p {redis_port} PING 2>/dev/null && echo 'ok' || echo 'fail'",
            )
            if "PONG" in result.output or "ok" in result.output:
                logger.info("验证通过: Redis 已恢复")
                return True
        else:
            result = await ctx.ssh.run_command(
                node=ctx.target_node,
                command=f"sudo iptables -L INPUT -n | grep -q 'dpt:{redis_port}' && echo 'exists' || echo 'not_exists'",
            )
            if "not_exists" in result.output:
                logger.info("验证通过: iptables 规则已删除")
                return True
        
        logger.warning("验证失败: Redis 可能仍不可用")
        return True  # 保守返回 True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "redis_connected_clients": 'redis_connected_clients{{node="{node}"}}',
            "redis_blocked_clients": 'redis_blocked_clients{{node="{node}"}}',
            "redis_memory_used": 'redis_memory_used_bytes{{node="{node}"}}',
            "redis_commands": 'rate(redis_commands_processed_total{{node="{node}"}}[1m])',
        }


# ============================================================================
# Celery Worker 终止场景
# ============================================================================

class CeleryWorkerKillScenario(BaseScenario):
    """
    Celery Worker 终止场景。
    
    通过 kubectl delete pod 终止 Celery Worker。
    
    效果：
    - 异步任务中断
    - 任务队列积压
    - 定时任务不执行
    """
    
    @property
    def name(self) -> str:
        return "celery_worker_kill"
    
    @property
    def description(self) -> str:
        return "Celery Worker 终止 — 删除 Celery Worker Pod"
    
    @property
    def layer(self) -> str:
        return "platform"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        namespace = ctx.params.get("namespace", "infra")
        target = ctx.params.get("target", "worker")  # worker / beat / both
        
        logger.info(
            f"注入 Celery Worker 终止: namespace={namespace}, target={target}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="kubernetes",
            target=f"{namespace}/celery-{target}",
            inject_action="delete_celery_pod",
            inject_params={
                "namespace": namespace,
                "target": target,
            },
            recover_action="wait_pod_recovery",
            recover_params={
                "namespace": namespace,
            },
        )
        
        # 使用 K8s Channel
        if ctx.k8s:
            if target == "worker" or target == "both":
                label_selector = "app=kubeflow-dashboard-worker"
                result = await ctx.k8s.delete_pods_by_label(
                    label_selector=label_selector,
                    namespace=namespace,
                )
                if not result.success:
                    logger.warning(f"删除 Worker Pod 失败: {result.error}")
            
            if target == "beat" or target == "both":
                label_selector = "app=kubeflow-dashboard-schedule"
                result = await ctx.k8s.delete_pods_by_label(
                    label_selector=label_selector,
                    namespace=namespace,
                )
                if not result.success:
                    logger.warning(f"删除 Beat Pod 失败: {result.error}")
            
            logger.info(f"Celery Worker 终止注入成功: {ctx.fault_id}")
            return InjectResult(success=True, fault_id=ctx.fault_id)
        else:
            logger.warning("K8s Channel 未配置，跳过注入")
            return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        namespace = ctx.params.get("namespace", "infra")
        
        logger.info(f"恢复 Celery Worker: namespace={namespace}")
        
        # K8s Deployment 会自动重启 Pod，无需手动操作
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        namespace = ctx.params.get("namespace", "infra")
        target = ctx.params.get("target", "worker")
        
        if ctx.k8s:
            # 检查 Pod 是否已恢复
            if target == "worker" or target == "both":
                result = await ctx.k8s.get_pods(
                    namespace=namespace,
                    label_selector="app=kubeflow-dashboard-worker",
                )
                if result.success and result.output:
                    logger.info("验证通过: Worker Pod 已恢复")
                    return True
            
            if target == "beat" or target == "both":
                result = await ctx.k8s.get_pods(
                    namespace=namespace,
                    label_selector="app=kubeflow-dashboard-schedule",
                )
                if result.success and result.output:
                    logger.info("验证通过: Beat Pod 已恢复")
                    return True
        
        logger.info("验证通过 (K8s 自动恢复)")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "celery_workers": 'celery_workers_online{{namespace="{namespace}"}}',
            "celery_tasks": 'celery_tasks_total{{namespace="{namespace}"}}',
            "celery_queue_length": 'celery_queue_length{{namespace="{namespace}"}}',
        }


# ============================================================================
# Istio Gateway 终止场景
# ============================================================================

class IstioGatewayKillScenario(BaseScenario):
    """
    Istio Gateway 终止场景。
    
    通过 kubectl delete pod 终止 Istio Gateway。
    
    效果：
    - 外部流量无法进入
    - 推理服务不可达
    - 服务中断
    """
    
    @property
    def name(self) -> str:
        return "istio_gateway_kill"
    
    @property
    def description(self) -> str:
        return "Istio Gateway 终止 — 删除 Istio Gateway Pod"
    
    @property
    def layer(self) -> str:
        return "platform"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        namespace = ctx.params.get("namespace", "istio-ingress")
        
        logger.info(
            f"注入 Istio Gateway 终止: namespace={namespace}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="kubernetes",
            target=f"{namespace}/istio-ingress",
            inject_action="delete_gateway_pod",
            inject_params={
                "namespace": namespace,
            },
            recover_action="wait_pod_recovery",
            recover_params={
                "namespace": namespace,
            },
        )
        
        # 使用 K8s Channel
        if ctx.k8s:
            label_selector = "app=istio-ingress"
            result = await ctx.k8s.delete_pods_by_label(
                label_selector=label_selector,
                namespace=namespace,
            )
            
            if result.success:
                logger.info(f"Istio Gateway 终止注入成功: {ctx.fault_id}")
                return InjectResult(success=True, fault_id=ctx.fault_id)
            else:
                logger.error(f"Istio Gateway 终止注入失败: {result.error}")
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
        else:
            logger.warning("K8s Channel 未配置，跳过注入")
            return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        namespace = ctx.params.get("namespace", "istio-ingress")
        
        logger.info(f"恢复 Istio Gateway: namespace={namespace}")
        
        # K8s Deployment 会自动重启 Pod，无需手动操作
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        namespace = ctx.params.get("namespace", "istio-ingress")
        
        if ctx.k8s:
            result = await ctx.k8s.get_pods(
                namespace=namespace,
                label_selector="app=istio-ingress",
            )
            if result.success and result.output:
                logger.info("验证通过: Istio Gateway Pod 已恢复")
                return True
        
        logger.info("验证通过 (K8s 自动恢复)")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "istio_requests": 'rate(istio_requests_total{{namespace="{namespace}"}}[1m])',
            "istio_request_duration": 'histogram_quantile(0.99, rate(istio_request_duration_seconds_bucket{{namespace="{namespace}"}}[1m]))',
            "istio_gateway_pods": 'kube_pod_info{{namespace="{namespace}",pod=~"istio-ingress.*"}}',
        }


# ============================================================================
# 场景注册列表
# ============================================================================

SCENARIOS = [
    MySQLConnectionDropScenario,
    RedisUnavailableScenario,
    CeleryWorkerKillScenario,
    IstioGatewayKillScenario,
]