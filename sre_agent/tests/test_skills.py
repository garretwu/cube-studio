from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from sre_agent.skills import SkillExecutor, SkillPolicy, SkillRegistry, SkillRegistryError
from sre_agent.tools import ToolExecutionContext, build_default_registry


class _FakeSSHChannel:
    async def run_command(self, node: str, command: str, use_sudo: bool = False) -> Any:
        _ = node, command, use_sudo

        class Result:
            success = True
            output = "ok"
            error = ""

        return Result()


class _FakeK8sClient:
    def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        _ = label_selector
        return [{"name": "pod-a", "namespace": namespace, "status": {"phase": "Running"}}]


class _FakePrometheus:
    async def query_instant(self, promql: str) -> float:
        _ = promql
        return 1.0


class _FailingPrometheus:
    async def query_instant(self, promql: str) -> Any:
        _ = promql
        raise RuntimeError("prometheus unavailable")


class _FakeSkillChannelBundle:
    @staticmethod
    def context() -> ToolExecutionContext:
        from lib.channels.kubernetes import K8sChannel

        return ToolExecutionContext(
            channels={
                "k8s": K8sChannel(client=_FakeK8sClient()),
                "prometheus": _FakePrometheus(),
                "ssh": _FakeSSHChannel(),
            }
        )


class TestSkillsUnit(unittest.IsolatedAsyncioTestCase):
    async def test_registry_discovers_builtin_skills(self) -> None:
        registry = SkillRegistry()
        skills = registry.discover()
        self.assertEqual(len(skills), 6)
        ids = {skill.id for skill in skills}
        self.assertIn("builtin-vllm-diagnosis", ids)
        self.assertIn("builtin-platform-health", ids)

    async def test_registry_rejects_invalid_skill_markdown(self) -> None:
        tmp_root = Path("sre_agent/tests/.tmp_skills")
        bad_skill_dir = tmp_root / "bad-skill"
        bad_skill_dir.mkdir(parents=True, exist_ok=True)
        (bad_skill_dir / "SKILL.md").write_text(
            "---\nname: bad\nscope: builtin\nsummary: missing id\npermissions: []\ntags: []\n---\n\n## Steps\n```yaml\n[]\n```\n",
            encoding="utf-8",
        )
        try:
            registry = SkillRegistry(root=tmp_root)
            with self.assertRaises(SkillRegistryError):
                registry.discover()
        finally:
            for item in sorted(tmp_root.rglob("*"), reverse=True):
                if item.is_file():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    item.rmdir()

    async def test_policy_rank_is_deterministic(self) -> None:
        registry = SkillRegistry()
        skills = registry.discover()
        policy = SkillPolicy()

        ranked = policy.rank("rdma network anomaly", skills, top_k=3)
        self.assertEqual(len(ranked), 3)
        self.assertGreaterEqual(ranked[0].match_score, ranked[1].match_score)
        self.assertIn("rdma", " ".join(ranked[0].tags).lower())

    async def test_policy_prefers_vllm_skill_for_alert_like_query(self) -> None:
        registry = SkillRegistry()
        skills = registry.discover()
        policy = SkillPolicy()

        ranked = policy.rank(
            (
                "alertname VLLMInterTokenLatencyP95High "
                "summary vLLM inter-token latency p95 is high "
                "service qwen3-32b-fp8-202602261 "
                "topology inference_service gpu"
            ),
            skills,
            top_k=3,
        )
        self.assertEqual(ranked[0].id, "builtin-vllm-diagnosis")

    async def test_executor_success_path(self) -> None:
        skill_registry = SkillRegistry()
        skill_registry.discover()
        skill = skill_registry.get("builtin-platform-health")

        tool_registry = build_default_registry()
        executor = SkillExecutor()
        context = _FakeSkillChannelBundle.context()
        result = await executor.execute(
            skill=skill,
            registry=tool_registry,
            context=context,
            variables={"namespace": "default", "promql": "up", "node": "worker-01"},
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.tool_runs), 3)
        self.assertTrue(all(run.success for run in result.tool_runs))

    async def test_executor_continues_on_step_error(self) -> None:
        skill_registry = SkillRegistry()
        skill_registry.discover()
        skill = skill_registry.get("builtin-vllm-diagnosis")

        tool_registry = build_default_registry()
        executor = SkillExecutor()
        context = ToolExecutionContext(
            channels={
                "k8s": _FakeK8sClient(),  # wrong interface for tool on purpose
                "prometheus": _FailingPrometheus(),
                "ssh": _FakeSSHChannel(),
            }
        )
        result = await executor.execute(
            skill=skill,
            registry=tool_registry,
            context=context,
            variables={"namespace": "default", "promql": "up", "node": "worker-01"},
        )

        self.assertIn(result.status, {"failed", "partial"})
        self.assertEqual(len(result.tool_runs), len(skill.steps))
        self.assertFalse(result.tool_runs[0].success)

    async def test_executor_enforces_v1_tool_whitelist(self) -> None:
        from sre_agent.skills.registry import SkillDescriptor, SkillStep

        tool_registry = build_default_registry()
        skill = SkillDescriptor(
            id="custom-bad",
            name="Bad",
            scope="custom",
            summary="contains disallowed tool",
            source="skills://bad",
            permissions=[],
            tags=[],
            steps=[SkillStep(tool="ontology.query", params={"entity_type": "node"})],
        )
        executor = SkillExecutor()
        result = await executor.execute(
            skill=skill,
            registry=tool_registry,
            context=_FakeSkillChannelBundle.context(),
            variables={},
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("tool_not_allowed", result.summary)


if __name__ == "__main__":
    unittest.main()
