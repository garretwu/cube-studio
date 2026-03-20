from __future__ import annotations

import importlib.util
import os
from typing import Any

import pytest

from fault_injector.config.schema import SSHConfig, TargetNodeConfig
from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel
from lib.channels.ssh import SSHChannel
from lib.tests._real_test_support import require_env, require_real_tests
from sre_agent.skills import SkillExecutor, SkillRegistry
from sre_agent.tools import ToolExecutionContext, build_default_registry


def _require_ssh_auth() -> tuple[str | None, str | None]:
    password = os.getenv("SRE_SSH_PASSWORD", "").strip() or None
    key_file = os.getenv("SRE_SSH_KEY_FILE", "").strip() or None
    if not password and not key_file:
        pytest.skip("real skills test requires one of SRE_SSH_PASSWORD or SRE_SSH_KEY_FILE")
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
@pytest.mark.asyncio
async def test_real_platform_health_skill_succeeds() -> None:
    require_real_tests()
    if importlib.util.find_spec("kubernetes") is None:
        pytest.skip("python package 'kubernetes' is required")

    env = require_env("SRE_PROMETHEUS_URL", "SRE_TEST_NAMESPACE", "SRE_TEST_NODE")
    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    promql = os.getenv("SRE_TEST_PROMQL", "up").strip() or "up"

    registry = SkillRegistry()
    registry.discover()
    skill = registry.get("builtin-platform-health")
    tool_registry = build_default_registry()
    executor = SkillExecutor()
    context = _build_real_context(
        namespace=env["SRE_TEST_NAMESPACE"],
        node=env["SRE_TEST_NODE"],
        prom_url=env["SRE_PROMETHEUS_URL"],
        kubeconfig=kubeconfig,
    )
    try:
        result = await executor.execute(
            skill=skill,
            registry=tool_registry,
            context=context,
            variables={
                "namespace": env["SRE_TEST_NAMESPACE"],
                "node": env["SRE_TEST_NODE"],
                "promql": promql,
            },
        )
        assert result.status == "success", result.summary
        assert all(run.success for run in result.tool_runs)
    finally:
        await _close_context(context)


@pytest.mark.real
@pytest.mark.asyncio
async def test_real_rdma_diagnosis_skill_succeeds() -> None:
    require_real_tests()
    if importlib.util.find_spec("kubernetes") is None:
        pytest.skip("python package 'kubernetes' is required")

    env = require_env("SRE_PROMETHEUS_URL", "SRE_TEST_NAMESPACE", "SRE_TEST_NODE")
    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    promql = os.getenv("SRE_TEST_PROMQL", "up").strip() or "up"

    registry = SkillRegistry()
    registry.discover()
    skill = registry.get("builtin-rdma-diagnosis")
    tool_registry = build_default_registry()
    executor = SkillExecutor()
    context = _build_real_context(
        namespace=env["SRE_TEST_NAMESPACE"],
        node=env["SRE_TEST_NODE"],
        prom_url=env["SRE_PROMETHEUS_URL"],
        kubeconfig=kubeconfig,
    )
    try:
        result = await executor.execute(
            skill=skill,
            registry=tool_registry,
            context=context,
            variables={
                "namespace": env["SRE_TEST_NAMESPACE"],
                "node": env["SRE_TEST_NODE"],
                "promql": promql,
            },
        )
        assert result.status == "success", result.summary
        assert all(run.success for run in result.tool_runs)
    finally:
        await _close_context(context)


@pytest.mark.real
@pytest.mark.asyncio
async def test_real_skill_missing_variable_fails_fast() -> None:
    require_real_tests()
    env = require_env("SRE_PROMETHEUS_URL", "SRE_TEST_NAMESPACE", "SRE_TEST_NODE")
    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"

    registry = SkillRegistry()
    registry.discover()
    skill = registry.get("builtin-platform-health")
    tool_registry = build_default_registry()
    executor = SkillExecutor()
    context = _build_real_context(
        namespace=env["SRE_TEST_NAMESPACE"],
        node=env["SRE_TEST_NODE"],
        prom_url=env["SRE_PROMETHEUS_URL"],
        kubeconfig=kubeconfig,
    )
    try:
        result = await executor.execute(
            skill=skill,
            registry=tool_registry,
            context=context,
            variables={"namespace": env["SRE_TEST_NAMESPACE"], "promql": "up"},
        )
        assert result.status == "failed"
        assert "missing required variable: node" in result.summary
        assert result.tool_runs[-1].success is False
    finally:
        await _close_context(context)

