from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from sre_agent.skills import SkillDecision, SkillExecutor, SkillPolicy, SkillRegistry, rank_skills
from sre_agent.tools import ToolExecutionContext, build_default_registry


class _FakePrometheus:
    async def query_instant(self, promql: str) -> float:
        _ = promql
        return 1.0


class TestSkillsUnit(unittest.IsolatedAsyncioTestCase):
    async def test_registry_discovers_builtin_skills_including_doc_first_skill(self) -> None:
        registry = SkillRegistry()
        skills = registry.discover(refresh=True)
        ids = {skill.id for skill in skills}
        self.assertIn("builtin-vllm-diagnosis", ids)
        self.assertIn("gpu-fault-sop", ids)
        self.assertIn("builtin-gpu-thermal-diagnosis", ids)
        self.assertIn("builtin-gpu-drop-diagnosis", ids)
        self.assertIn("builtin-rdma-diagnosis", ids)
        gpu_fault = registry.get("gpu-fault-sop")
        self.assertIn("gpu_health_check.sh", gpu_fault.scripts)
        gpu_drop = registry.get("builtin-gpu-drop-diagnosis")
        self.assertIn("gpu_drop_recover.sh", gpu_drop.scripts)
        rdma_skill = registry.get("builtin-rdma-diagnosis")
        self.assertEqual(rdma_skill.scripts, [])
        self.assertEqual(rdma_skill.script_descriptions, {})

    async def test_registry_supports_claude_style_skill_with_scripts_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "skills"
            skill_dir = root / "diag" / "network-check"
            (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
            (skill_dir / "references").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: sre:network-check
description: Diagnose a network issue from a Claude-style skill.
version: "1.0"
---

# Network Check

## Notes
Read the reference first and then run the script.

## Scripts

- `check.sh`
  - Collect link state evidence.
""",
                encoding="utf-8",
            )
            (skill_dir / "scripts" / "check.sh").write_text("#!/usr/bin/env bash\necho ready\n", encoding="utf-8")
            (skill_dir / "references" / "triage.md").write_text("look at link state\n", encoding="utf-8")

            registry = SkillRegistry(root=root)
            skills = registry.discover(refresh=True)
            self.assertEqual(len(skills), 1)
            skill = skills[0]
            self.assertEqual(skill.id, "sre:network-check")
            self.assertEqual(skill.version, "1.0")
            self.assertEqual(skill.scripts, ["check.sh"])
            self.assertEqual(skill.script_descriptions["check.sh"], "Collect link state evidence.")
            self.assertEqual(skill.references, ["triage.md"])

    async def test_registry_skips_invalid_skill_and_records_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "skills"
            good = root / "good-skill"
            bad = root / "bad-skill"
            good.mkdir(parents=True, exist_ok=True)
            bad.mkdir(parents=True, exist_ok=True)
            (good / "SKILL.md").write_text(
                "---\nname: good-skill\ndescription: ok\n---\n\n# Good Skill\n",
                encoding="utf-8",
            )
            (bad / "SKILL.md").write_text("---\nname: [broken\ndescription: nope\n", encoding="utf-8")
            registry = SkillRegistry(root=root)
            skills = registry.discover(refresh=True)
            self.assertEqual([item.id for item in skills], ["good-skill"])
            self.assertTrue(registry.warnings)

    async def test_rank_skills_prefers_vllm_skill_for_alert_like_query(self) -> None:
        registry = SkillRegistry()
        skills = registry.discover(refresh=True)
        ranked = rank_skills(
            (
                "alertname VLLMInterTokenLatencyP95High "
                "summary vLLM inter-token latency p95 is high "
                "service qwen3-32b-fp8-202602261 topology inference_service gpu"
            ),
            skills,
            top_k=3,
        )
        self.assertEqual(ranked[0].id, "builtin-vllm-diagnosis")

    async def test_skill_policy_returns_allow_ask_and_deny(self) -> None:
        policy = SkillPolicy(deny=["builtin-danger:*"], ask=["gpu-fault-sop:gpu_benchmark.sh"])
        self.assertEqual(
            policy.evaluate(skill_id="builtin-vllm-diagnosis", script="noop.sh").decision,
            SkillDecision.ALLOW,
        )
        self.assertEqual(
            policy.evaluate(skill_id="gpu-fault-sop", script="gpu_benchmark.sh").decision,
            SkillDecision.ASK,
        )
        self.assertEqual(
            policy.evaluate(skill_id="builtin-danger", script="wipe.sh").decision,
            SkillDecision.DENY,
        )

    async def test_executor_runs_script_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "skills"
            skill_dir = root / "scripted"
            (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: scripted\ndescription: run a script\n---\n\n# Scripted Skill\n",
                encoding="utf-8",
            )
            (skill_dir / "scripts" / "collect.sh").write_text(
                "#!/usr/bin/env bash\necho script-ok\n",
                encoding="utf-8",
            )
            registry = SkillRegistry(root=root)
            skill = registry.discover(refresh=True)[0]
            executor = SkillExecutor()
            result = await executor.execute(
                skill=skill,
                script="collect.sh",
                args=[],
                policy=SkillPolicy(),
                skill_registry=registry,
            )
            self.assertEqual(result.status, "success")
            self.assertEqual(result.mode, "script")
            self.assertIn("script-ok", result.stdout)

    async def test_executor_requires_script_parameter(self) -> None:
        registry = SkillRegistry()
        skill = registry.get("gpu-fault-sop")
        executor = SkillExecutor()
        result = await executor.execute(skill=skill)
        self.assertEqual(result.status, "failed")
        self.assertIn("script", result.summary)

    async def test_skill_tools_cover_list_load_read_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "skills"
            skill_dir = root / "doc-skill"
            (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
            (skill_dir / "references").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: doc-skill\ndescription: a scripted diagnosis skill\n---\n\n# Doc Skill\n",
                encoding="utf-8",
            )
            (skill_dir / "scripts" / "run.sh").write_text(
                "#!/usr/bin/env bash\necho $1\n",
                encoding="utf-8",
            )
            (skill_dir / "references" / "guide.md").write_text("read me first\n", encoding="utf-8")

            skill_registry = SkillRegistry(root=root)
            skill_policy = SkillPolicy()
            skill_executor = SkillExecutor()
            tool_registry = build_default_registry()
            context = ToolExecutionContext(
                metadata={
                    "skill_registry": skill_registry,
                    "skill_policy": skill_policy,
                    "skill_executor": skill_executor,
                    "tool_registry": tool_registry,
                }
            )

            listed = await tool_registry.execute("skills.list_skills", {"query": "doc scripted"}, context)
            self.assertTrue(listed.success)
            self.assertEqual(listed.data["skills"][0]["skill_id"], "doc-skill")
            self.assertEqual(listed.data["skills"][0]["name"], "doc-skill")
            self.assertEqual(listed.data["skills"][0]["description"], "a scripted diagnosis skill")
            self.assertNotIn("scripts", listed.data["skills"][0])
            self.assertNotIn("references", listed.data["skills"][0])

            missing_query = await tool_registry.execute("skills.list_skills", {}, context)
            self.assertFalse(missing_query.success)
            self.assertIn("parameter 'query' is required", missing_query.error)

            derived_query = await tool_registry.execute(
                "skills.list_skills",
                {
                    "alert_name": "doc-skill",
                    "labels": {"category": "scripted"},
                    "annotations": {"summary": "diagnosis skill"},
                },
                context,
            )
            self.assertTrue(derived_query.success)
            self.assertEqual(derived_query.data["skills"][0]["skill_id"], "doc-skill")

            loaded = await tool_registry.execute("skills.load_skill", {"skill_id": "doc-skill"}, context)
            self.assertTrue(loaded.success)
            self.assertIn("Doc Skill", loaded.data["content"])
            self.assertEqual(loaded.data["scripts"], ["run.sh"])
            self.assertEqual(loaded.data["references"], ["guide.md"])

            reference = await tool_registry.execute(
                "skills.read_skill_ref",
                {"skill_id": "doc-skill", "reference": "guide.md"},
                context,
            )
            self.assertTrue(reference.success)
            self.assertIn("read me first", reference.data["content"])

            executed = await tool_registry.execute(
                "skills.run_skill",
                {"skill_id": "doc-skill", "script": "run.sh", "args": ["hello"]},
                context,
            )
            self.assertTrue(executed.success)
            self.assertEqual(executed.data["status"], "success")
            self.assertIn("hello", executed.data["stdout"])

    async def test_read_skill_ref_rejects_escape_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "skills"
            skill_dir = root / "doc-skill"
            (skill_dir / "references").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: doc-skill\ndescription: a diagnosis skill\n---\n\n# Doc Skill\n",
                encoding="utf-8",
            )
            (skill_dir / "references" / "guide.md").write_text("guide\n", encoding="utf-8")
            tool_registry = build_default_registry()
            context = ToolExecutionContext(metadata={"skill_registry": SkillRegistry(root=root)})

            result = await tool_registry.execute(
                "skills.read_skill_ref",
                {"skill_id": "doc-skill", "reference": "../secret.txt"},
                context,
            )
            self.assertFalse(result.success)
            self.assertIn("must not escape", result.error)


if __name__ == "__main__":
    unittest.main()
