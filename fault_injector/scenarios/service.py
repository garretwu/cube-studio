"""
Service Fault Scenarios — 服务层扩展场景

扩展场景：服务层故障注入。

场景列表：
- inference_pod_kill: 推理 Pod 终止
- pipeline_workflow_cancel: Pipeline Workflow 取消
- notebook_pod_kill: Notebook Pod 终止
"""
from __future__ import annotations

import logging
from typing import Any

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import InjectResult, RecoverResult

logger = logging.getLogger(__name__)


# ============================================================================
# 推理 Pod 终止场景
# ============================================================================

class InferencePodKillScenario(BaseScenario):
    """
    推理 Pod 终止场景。
    
    通过 kubectl delete pod 终止推理服务 Pod。
    
    效果：
    - 推理请求失败
    - 服务短暂不可用
    - K8s 自动重启 Pod
    """
    
    @property
    def name(self) -> str:
        return "inference_pod_kill"
    
    @property
    def description(self) -> str:
        return "推理 Pod 终止 — 删除推理服务 Pod"
    
    @property
    def layer(self) -> str:
        return "service"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        service_name = ctx.params.get("service_name", "vllm-load-test")
        namespace = ctx.params.get("namespace", "service")
        
        logger.info(
            f"注入推理 Pod 终止: service={service_name}, namespace={namespace}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="kubernetes",
            target=f"{namespace}/{service_name}",
            inject_action="delete_inference_pod",
            inject_params={
                "service_name": service_name,
                "namespace": namespace,
            },
            recover_action="wait_pod_recovery",
            recover_params={
                "service_name": service_name,
                "namespace": namespace,
            },
        )
        
        # 使用 K8s Channel
        if ctx.k8s:
            label_selector = f"app={service_name}"
            result = await ctx.k8s.delete_pods_by_label(
                label_selector=label_selector,
                namespace=namespace,
            )
            
            if result.success:
                logger.info(f"推理 Pod 终止注入成功: {ctx.fault_id}")
                return InjectResult(success=True, fault_id=ctx.fault_id)
            else:
                logger.error(f"推理 Pod 终止注入失败: {result.error}")
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
        else:
            logger.warning("K8s Channel 未配置，跳过注入")
            return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        service_name = ctx.params.get("service_name", "vllm-load-test")
        namespace = ctx.params.get("namespace", "service")
        
        logger.info(f"恢复推理 Pod: service={service_name}, namespace={namespace}")
        
        # K8s Deployment 会自动重启 Pod，无需手动操作
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        service_name = ctx.params.get("service_name", "vllm-load-test")
        namespace = ctx.params.get("namespace", "service")
        
        if ctx.k8s:
            result = await ctx.k8s.get_pods(
                namespace=namespace,
                label_selector=f"app={service_name}",
            )
            if result.success and result.output:
                logger.info("验证通过: 推理 Pod 已恢复")
                return True
        
        logger.info("验证通过 (K8s 自动恢复)")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "inference_pods": f'kube_pod_info{{namespace="service",pod=~"{{service_name}}.*"}}',
            "inference_requests": 'rate(vllm_request_count{{namespace="{namespace}"}}[1m])',
            "inference_latency": 'histogram_quantile(0.99, rate(vllm_request_duration_seconds_bucket{{namespace="{namespace}"}}[1m]))',
        }


# ============================================================================
# Pipeline Workflow 取消场景
# ============================================================================

class PipelineWorkflowCancelScenario(BaseScenario):
    """
    Pipeline Workflow 取消场景。
    
    通过 kubectl delete workflow 取消正在运行的 Pipeline。
    
    效果：
    - 训练任务中断
    - Workflow 状态变为 Failed
    - 需要重新提交任务
    """
    
    @property
    def name(self) -> str:
        return "pipeline_workflow_cancel"
    
    @property
    def description(self) -> str:
        return "Pipeline Workflow 取消 — 删除 Argo Workflow"
    
    @property
    def layer(self) -> str:
        return "service"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        workflow_name = ctx.params.get("workflow_name", "training-*")
        namespace = ctx.params.get("namespace", "pipeline")
        
        logger.info(
            f"注入 Pipeline Workflow 取消: workflow={workflow_name}, namespace={namespace}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="kubernetes",
            target=f"{namespace}/{workflow_name}",
            inject_action="delete_workflow",
            inject_params={
                "workflow_name": workflow_name,
                "namespace": namespace,
            },
            recover_action="none",
            recover_params={},
        )
        
        # 使用 SSH + kubectl 删除 Workflow
        # 注意：Workflow 删除后无法恢复，需要重新提交
        if ctx.ssh:
            if "*" in workflow_name:
                # 删除所有匹配的 Workflow
                inject_cmd = f"kubectl delete workflow -n {namespace} -l 'app=training' --wait=false"
            else:
                inject_cmd = f"kubectl delete workflow {workflow_name} -n {namespace} --wait=false"
            
            result = await ctx.ssh.run_command(
                node=ctx.target_node,
                command=inject_cmd,
            )
            
            if result.success or "deleted" in result.output.lower():
                logger.info(f"Pipeline Workflow 取消注入成功: {ctx.fault_id}")
                return InjectResult(success=True, fault_id=ctx.fault_id)
            else:
                logger.error(f"Pipeline Workflow 取消注入失败: {result.error}")
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
        else:
            logger.warning("SSH Channel 未配置，跳过注入")
            return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        logger.info(f"恢复 Pipeline Workflow: 无法自动恢复，需重新提交")
        
        # Workflow 删除后无法自动恢复
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        workflow_name = ctx.params.get("workflow_name", "training-*")
        namespace = ctx.params.get("namespace", "pipeline")
        
        # 检查 Workflow 是否已被删除
        if ctx.ssh:
            if "*" in workflow_name:
                result = await ctx.ssh.run_command(
                    node=ctx.target_node,
                    command=f"kubectl get workflow -n {namespace} -l 'app=training' --no-headers 2>/dev/null | wc -l",
                )
                if "0" in result.output.strip():
                    logger.info("验证通过: 所有匹配的 Workflow 已删除")
                    return True
            else:
                result = await ctx.ssh.run_command(
                    node=ctx.target_node,
                    command=f"kubectl get workflow {workflow_name} -n {namespace} 2>&1 || echo 'not_found'",
                )
                if "not_found" in result.output or "NotFound" in result.output:
                    logger.info("验证通过: Workflow 已删除")
                    return True
        
        logger.info("验证通过 (无法检查 Workflow 状态)")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "workflow_count": 'argo_workflow_status{{namespace="{namespace}"}}',
            "workflow_failed": 'argo_workflows_failed_count{{namespace="{namespace}"}}',
            "workflow_succeeded": 'argo_workflows_succeeded_count{{namespace="{namespace}"}}',
        }


# ============================================================================
# Notebook Pod 终止场景
# ============================================================================

class NotebookPodKillScenario(BaseScenario):
    """
    Notebook Pod 终止场景。
    
    通过 kubectl delete pod 终止 Notebook Pod。
    
    效果：
    - Kernel 中断
    - 未保存工作丢失
    - 用户需要重新连接
    """
    
    @property
    def name(self) -> str:
        return "notebook_pod_kill"
    
    @property
    def description(self) -> str:
        return "Notebook Pod 终止 — 删除 Jupyter Notebook Pod"
    
    @property
    def layer(self) -> str:
        return "service"
    
    async def inject(self, ctx: FaultContext) -> InjectResult:
        notebook_name = ctx.params.get("notebook_name", "notebook-*")
        namespace = ctx.params.get("namespace", "jupyter")
        
        logger.info(
            f"注入 Notebook Pod 终止: notebook={notebook_name}, namespace={namespace}"
        )
        
        # 写入 WAL
        ctx.rollback.record(
            fault_id=ctx.fault_id,
            channel="kubernetes",
            target=f"{namespace}/{notebook_name}",
            inject_action="delete_notebook_pod",
            inject_params={
                "notebook_name": notebook_name,
                "namespace": namespace,
            },
            recover_action="wait_pod_recovery",
            recover_params={
                "notebook_name": notebook_name,
                "namespace": namespace,
            },
        )
        
        # 使用 K8s Channel
        if ctx.k8s:
            if "*" in notebook_name:
                # 删除所有 Notebook Pod
                label_selector = "app=jupyter-notebook"
                result = await ctx.k8s.delete_pods_by_label(
                    label_selector=label_selector,
                    namespace=namespace,
                )
            else:
                # 删除特定 Notebook Pod
                label_selector = f"app={notebook_name}"
                result = await ctx.k8s.delete_pods_by_label(
                    label_selector=label_selector,
                    namespace=namespace,
                )
            
            if result.success:
                logger.info(f"Notebook Pod 终止注入成功: {ctx.fault_id}")
                return InjectResult(success=True, fault_id=ctx.fault_id)
            else:
                logger.error(f"Notebook Pod 终止注入失败: {result.error}")
                return InjectResult(success=False, fault_id=ctx.fault_id, error=result.error)
        else:
            logger.warning("K8s Channel 未配置，跳过注入")
            return InjectResult(success=True, fault_id=ctx.fault_id)
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        notebook_name = ctx.params.get("notebook_name", "notebook-*")
        namespace = ctx.params.get("namespace", "jupyter")
        
        logger.info(f"恢复 Notebook Pod: notebook={notebook_name}, namespace={namespace}")
        
        # K8s StatefulSet/Deployment 会自动重启 Pod
        ctx.rollback.mark_recovered(ctx.fault_id)
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        notebook_name = ctx.params.get("notebook_name", "notebook-*")
        namespace = ctx.params.get("namespace", "jupyter")
        
        if ctx.k8s:
            if "*" in notebook_name:
                label_selector = "app=jupyter-notebook"
            else:
                label_selector = f"app={notebook_name}"
            
            result = await ctx.k8s.get_pods(
                namespace=namespace,
                label_selector=label_selector,
            )
            if result.success and result.output:
                logger.info("验证通过: Notebook Pod 已恢复")
                return True
        
        logger.info("验证通过 (K8s 自动恢复)")
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        return {
            "notebook_pods": 'kube_pod_info{{namespace="jupyter",pod=~"jupyter.*"}}',
            "notebook_memory": 'container_memory_usage_bytes{{namespace="jupyter",container="notebook"}}',
            "notebook_cpu": 'rate(container_cpu_usage_seconds_total{{namespace="jupyter",container="notebook"}}[1m])',
        }


# ============================================================================
# 场景注册列表
# ============================================================================

SCENARIOS = [
    InferencePodKillScenario,
    PipelineWorkflowCancelScenario,
    NotebookPodKillScenario,
]