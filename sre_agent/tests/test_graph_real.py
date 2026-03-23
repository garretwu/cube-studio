from __future__ import annotations

import importlib.util
import os

import pytest

from fault_injector.config.schema import SSHConfig, TargetNodeConfig
from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel
from lib.channels.ssh import SSHChannel
from lib.tests._real_test_support import require_env, require_real_tests
from sre_agent.agent import run_diagnosis
from sre_agent.models.diagnosis import DiagnosisResult, ThinkingTrace
from sre_agent.models.remediation import RemediationPlan
from sre_agent.tools import ToolExecutionContext


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _require_ssh_auth() -> tuple[str | None, str | None]:
    password = os.getenv("SRE_SSH_PASSWORD", "").strip() or None
    key_file = os.getenv("SRE_SSH_KEY_FILE", "").strip() or None
    if not password and not key_file:
        pytest.skip("real graph test requires one of SRE_SSH_PASSWORD or SRE_SSH_KEY_FILE")
    return password, key_file


def _build_real_context(*, namespace: str, node: str, prom_url: str, kubeconfig: str) -> ToolExecutionContext:
    _ = namespace
    ssh_host = os.getenv("SRE_SSH_HOST", "").strip() or node
    ssh_user = os.getenv("SRE_SSH_USER", "").strip() or "root"
    ssh_port = int(os.getenv("SRE_SSH_PORT", "22"))
    password, key_file = _require_ssh_auth()
    inventory = {
        node: TargetNodeConfig(
            name=node,
            ssh=SSHConfig(
                host=ssh_host,
                port=ssh_port,
                user=ssh_user,
                password=password,
                key_file=key_file,
            ),
        )
    }
    ssh = SSHChannel(inventory=inventory, dry_run=False)
    k8s = K8sChannel(client=None, kubeconfig=kubeconfig)
    prom = PrometheusChannel(base_url=prom_url)
    return ToolExecutionContext(channels={"k8s": k8s, "prometheus": prom, "ssh": ssh})


async def _close_context(context: ToolExecutionContext) -> None:
    for channel in context.channels.values():
        close = getattr(channel, "close", None)
        if callable(close):
            value = close()
            if hasattr(value, "__await__"):
                await value


@pytest.mark.real
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_real_llm_graph_platform_health_readonly() -> None:
    require_real_tests()
    if not _env_bool("SRE_REAL_LLM", default=False):
        pytest.skip("set SRE_REAL_LLM=1 to enable real LLM graph tests")
    if importlib.util.find_spec("kubernetes") is None:
        pytest.skip("python package 'kubernetes' is required")

    api_key = os.getenv("SRE_OPENAI_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("real LLM graph test requires SRE_OPENAI_API_KEY or OPENAI_API_KEY")

    env = require_env("SRE_PROMETHEUS_URL", "SRE_TEST_NAMESPACE", "SRE_TEST_NODE")
    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    context = _build_real_context(
        namespace=env["SRE_TEST_NAMESPACE"],
        node=env["SRE_TEST_NODE"],
        prom_url=env["SRE_PROMETHEUS_URL"],
        kubeconfig=kubeconfig,
    )
    try:
        result = await run_diagnosis(
            query="Diagnose platform health for this cluster and node. Use read-only tools only and gather evidence before concluding.",
            context=context,
            variables={"namespace": env["SRE_TEST_NAMESPACE"], "promql": "up", "node": env["SRE_TEST_NODE"]},
            allowed_tool_names=["prometheus.query_instant", "k8s.list_pods", "gpu.get_metrics"],
            checkpoint_dir="./data/checkpoints/sre_agent_real",
        )
        assert result["status"] == "diagnosed", result["summary"]
        assert result["tool_runs"], "expected at least one read-only tool run"
        assert all("apply_manifest" not in run["tool"] for run in result["tool_runs"])
        DiagnosisResult.model_validate(result["diagnosis_result"])
        ThinkingTrace.from_langraph_state(result["trace_items"])
        if result["remediation_plan"] is not None:
            RemediationPlan.model_validate(result["remediation_plan"])
    finally:
        await _close_context(context)


@pytest.mark.real
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_real_llm_graph_rdma_readonly() -> None:
    require_real_tests()
    if not _env_bool("SRE_REAL_LLM", default=False):
        pytest.skip("set SRE_REAL_LLM=1 to enable real LLM graph tests")
    if importlib.util.find_spec("kubernetes") is None:
        pytest.skip("python package 'kubernetes' is required")

    api_key = os.getenv("SRE_OPENAI_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("real LLM graph test requires SRE_OPENAI_API_KEY or OPENAI_API_KEY")

    env = require_env("SRE_PROMETHEUS_URL", "SRE_TEST_NAMESPACE", "SRE_TEST_NODE")
    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    context = _build_real_context(
        namespace=env["SRE_TEST_NAMESPACE"],
        node=env["SRE_TEST_NODE"],
        prom_url=env["SRE_PROMETHEUS_URL"],
        kubeconfig=kubeconfig,
    )
    try:
        result = await run_diagnosis(
            query="Diagnose RDMA and RoCE anomalies around this node. Use read-only evidence only and do not change the system.",
            context=context,
            variables={"namespace": env["SRE_TEST_NAMESPACE"], "promql": "up", "node": env["SRE_TEST_NODE"]},
            allowed_tool_names=["network.get_rdma_stats", "k8s.list_pods", "gpu.get_metrics", "prometheus.query_instant"],
            checkpoint_dir="./data/checkpoints/sre_agent_real",
        )
        assert result["status"] == "diagnosed", result["summary"]
        assert result["tool_runs"], "expected at least one read-only tool run"
        assert all("set_bmc_" not in run["tool"] for run in result["tool_runs"])
        DiagnosisResult.model_validate(result["diagnosis_result"])
        ThinkingTrace.from_langraph_state(result["trace_items"])
        if result["remediation_plan"] is not None:
            RemediationPlan.model_validate(result["remediation_plan"])
    finally:
        await _close_context(context)
