from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from typing import Any

import pytest

from fault_injector.config.schema import SSHConfig, TargetNodeConfig
from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel
from lib.channels.ssh import SSHChannel
from lib.tests._real_test_support import require_env, require_real_tests
from sre_agent.skills import SkillExecutor, SkillPolicy, SkillRegistry
from sre_agent.skills.executor import SkillExecutionResult
from sre_agent.skills.registry import SkillDescriptor
from sre_agent.tools import ToolExecutionContext, build_default_registry


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


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


class _DeterministicSkillSelector:
    def __init__(self, policy: SkillPolicy) -> None:
        self._policy = policy

    def select_skill_id(self, query: str, skills: list[SkillDescriptor]) -> str:
        ranked = self._policy.rank(query, skills, top_k=1)
        if not ranked:
            raise RuntimeError("no skills available for deterministic selection")
        return ranked[0].id


class _OpenAISkillSelector:
    def __init__(self, model: str) -> None:
        self._model = model

    def select_skill_id(self, query: str, skills: list[SkillDescriptor]) -> str:
        if importlib.util.find_spec("openai") is None:
            raise RuntimeError("python package 'openai' is required for real LLM selection")

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for real LLM selection")

        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        catalog = [
            {
                "id": item.id,
                "name": item.name,
                "summary": item.summary,
                "scope": item.scope,
            }
            for item in skills
        ]
        allowed_ids = {item["id"] for item in catalog}
        sys_prompt = (
            "You are a strict skill selector. Return JSON only with keys: "
            '{"skill_id":"...", "reason":"..."}. skill_id must be one of provided ids.'
        )
        user_prompt = json.dumps({"query": query, "skills": catalog}, ensure_ascii=True)
        response = client.chat.completions.create(
            model=self._model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = ""
        if response.choices:
            content = str(response.choices[0].message.content or "").strip()
        if not content:
            raise RuntimeError("real LLM selector returned empty response")

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"real LLM selector did not return valid JSON: {content}") from exc

        skill_id = str(payload.get("skill_id", "")).strip()
        if not skill_id:
            raise RuntimeError(f"real LLM selector returned missing skill_id: {payload}")
        if skill_id not in allowed_ids:
            raise RuntimeError(f"real LLM selector returned unknown skill_id: {skill_id}")
        return skill_id


@dataclass
class _SkillConsumer:
    registry: SkillRegistry
    policy: SkillPolicy
    executor: SkillExecutor
    tools: Any
    context: ToolExecutionContext
    real_llm_selector: _OpenAISkillSelector | None = None
    _loaded: bool = False

    def load_skills(self) -> list[SkillDescriptor]:
        skills = self.registry.discover()
        self._loaded = True
        return skills

    def select_skill(self, query: str, skills: list[SkillDescriptor], use_real_llm: bool = False) -> str:
        selector: _DeterministicSkillSelector | _OpenAISkillSelector
        if use_real_llm:
            if self.real_llm_selector is None:
                raise RuntimeError("real_llm selector is not configured")
            selector = self.real_llm_selector
        else:
            selector = _DeterministicSkillSelector(self.policy)
        selected_id = selector.select_skill_id(query, skills)
        if selected_id not in {item.id for item in skills}:
            raise RuntimeError(f"selected skill is not in discovered catalog: {selected_id}")
        return selected_id

    async def run_skill(self, skill_id: str, variables: dict[str, Any]) -> SkillExecutionResult:
        if not self._loaded:
            self.load_skills()
        skill = self.registry.get(skill_id)
        return await self.executor.execute(
            skill=skill,
            registry=self.tools,
            context=self.context,
            variables=variables,
        )


def _build_real_consumer(context: ToolExecutionContext) -> _SkillConsumer:
    llm_model = os.getenv("SRE_LLM_MODEL", "").strip() or "gpt-4o-mini"
    return _SkillConsumer(
        registry=SkillRegistry(),
        policy=SkillPolicy(),
        executor=SkillExecutor(),
        tools=build_default_registry(),
        context=context,
        real_llm_selector=_OpenAISkillSelector(model=llm_model),
    )


def _common_variables(namespace: str, node: str, promql: str) -> dict[str, Any]:
    return {
        "namespace": namespace,
        "node": node,
        "promql": promql,
    }


@pytest.mark.real
@pytest.mark.asyncio
async def test_real_platform_health_skill_succeeds() -> None:
    require_real_tests()
    if importlib.util.find_spec("kubernetes") is None:
        pytest.skip("python package 'kubernetes' is required")

    env = require_env("SRE_PROMETHEUS_URL", "SRE_TEST_NAMESPACE", "SRE_TEST_NODE")
    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    promql = os.getenv("SRE_TEST_PROMQL", "up").strip() or "up"
    context = _build_real_context(
        namespace=env["SRE_TEST_NAMESPACE"],
        node=env["SRE_TEST_NODE"],
        prom_url=env["SRE_PROMETHEUS_URL"],
        kubeconfig=kubeconfig,
    )

    try:
        consumer = _build_real_consumer(context)
        skills = consumer.load_skills()
        assert len(skills) > 0
        selected = consumer.select_skill("platform health baseline gpu check", skills, use_real_llm=False)
        result = await consumer.run_skill(
            selected,
            _common_variables(env["SRE_TEST_NAMESPACE"], env["SRE_TEST_NODE"], promql),
        )
        assert result.status == "success", result.summary
        assert len(result.tool_runs) > 0
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
            variables=_common_variables(env["SRE_TEST_NAMESPACE"], env["SRE_TEST_NODE"], promql),
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


@pytest.mark.real
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_real_skill_consumer_with_openai_selector() -> None:
    require_real_tests()
    if not _env_bool("SRE_REAL_LLM", default=False):
        pytest.skip("set SRE_REAL_LLM=1 to enable real LLM skill-selection tests")
    if importlib.util.find_spec("kubernetes") is None:
        pytest.skip("python package 'kubernetes' is required")

    env = require_env("SRE_PROMETHEUS_URL", "SRE_TEST_NAMESPACE", "SRE_TEST_NODE", "OPENAI_API_KEY")
    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    promql = os.getenv("SRE_TEST_PROMQL", "up").strip() or "up"

    context = _build_real_context(
        namespace=env["SRE_TEST_NAMESPACE"],
        node=env["SRE_TEST_NODE"],
        prom_url=env["SRE_PROMETHEUS_URL"],
        kubeconfig=kubeconfig,
    )
    try:
        consumer = _build_real_consumer(context)
        skills = consumer.load_skills()
        assert len(skills) > 0
        selected = consumer.select_skill(
            query="Diagnose RDMA and network anomalies around this node",
            skills=skills,
            use_real_llm=True,
        )
        result = await consumer.run_skill(
            selected,
            _common_variables(env["SRE_TEST_NAMESPACE"], env["SRE_TEST_NODE"], promql),
        )
        assert result.status == "success", result.summary
        assert len(result.tool_runs) > 0
    finally:
        await _close_context(context)
