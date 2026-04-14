from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from sre_agent.agent import run_diagnosis
from sre_agent.agent.prompts import build_system_prompt
from sre_agent.models.diagnosis import DiagnosisResult, ThinkingTrace
from sre_agent.tools import ToolExecutionContext, build_default_registry


class _FakePrometheus:
    async def query_instant(self, promql: str) -> float:
        _ = promql
        return 1.0


class _FakeSSHChannel:
    async def run_command(self, node: str, command: str, use_sudo: bool = False) -> Any:
        _ = node, command, use_sudo

        class Result:
            success = True
            output = "ok"
            error = ""

        return Result()


class _FakeTcEvidenceSSHChannel:
    async def run_command(self, node: str, command: str, use_sudo: bool = False) -> Any:
        _ = node, use_sudo

        class Result:
            success = True
            error = ""
            if "tc qdisc show" in command:
                output = "qdisc netem 8016: parent 8016:10 limit 1000 delay 120ms"
            elif "ps -eo" in command:
                output = "2233 fault_injector /usr/local/bin/fault_injector --scene network-jitter"
            else:
                output = "ok"

        return Result()


class _FakeK8sClient:
    def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        _ = label_selector
        return [{"name": "pod-a", "namespace": namespace, "status": {"phase": "Running"}}]


def _happy_context() -> ToolExecutionContext:
    from lib.channels.kubernetes import K8sChannel

    return ToolExecutionContext(
        channels={
            "k8s": K8sChannel(client=_FakeK8sClient()),
            "prometheus": _FakePrometheus(),
            "ssh": _FakeSSHChannel(),
        }
    )


def _tc_evidence_context() -> ToolExecutionContext:
    from lib.channels.kubernetes import K8sChannel

    return ToolExecutionContext(
        channels={
            "k8s": K8sChannel(client=_FakeK8sClient()),
            "prometheus": _FakePrometheus(),
            "ssh": _FakeTcEvidenceSSHChannel(),
        }
    )


class _FakeBoundLLM:
    def __init__(self, parent: "_FakeLLM", tools: list[Any], tool_choice: str) -> None:
        self._parent = parent
        self._tools = tools
        self._tool_choice = tool_choice

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self._parent.calls.append(
            {
                "messages": messages,
                "tools": self._tools,
                "tool_choice": self._tool_choice,
            }
        )
        response = self._parent.responses[self._parent.index]
        self._parent.index += 1
        return response


class _RetryOnEmptyMessagesBoundLLM(_FakeBoundLLM):
    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self._parent.calls.append(
            {
                "messages": messages,
                "tools": self._tools,
                "tool_choice": self._tool_choice,
            }
        )
        if self._parent.fail_first_with_empty_messages_error:
            self._parent.fail_first_with_empty_messages_error = False
            raise RuntimeError(
                "Error code: 400 - {'type': 'error', 'error': {'type': 'bad_request_error', "
                "'message': 'invalid params, messages is empty (2013)', 'http_code': '400'}}"
            )
        response = self._parent.responses[self._parent.index]
        self._parent.index += 1
        return response


class _FakeLLM:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.index = 0
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append({"messages": messages, "tools": [], "tool_choice": "none"})
        response = self.responses[self.index]
        self.index += 1
        return response

    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> _FakeBoundLLM:
        return _FakeBoundLLM(self, tools, tool_choice)


class _RetryOnEmptyMessagesLLM(_FakeLLM):
    def __init__(self, responses: list[AIMessage]) -> None:
        super().__init__(responses)
        self.fail_first_with_empty_messages_error = True

    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> _RetryOnEmptyMessagesBoundLLM:
        return _RetryOnEmptyMessagesBoundLLM(self, tools, tool_choice)


class _SlowLLM:
    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> "_SlowLLM":
        _ = tools, tool_choice
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        _ = messages
        import asyncio

        await asyncio.sleep(0.2)
        return AIMessage(content="{}")


class TestGraphUnit(unittest.IsolatedAsyncioTestCase):
    async def test_react_rebuilds_prompt_when_provider_returns_empty_messages_error(self) -> None:
        llm = _RetryOnEmptyMessagesLLM(
            [
                AIMessage(
                    content="Collect Prometheus evidence first.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "up"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Prometheus evidence is stable after retry.",
                            "diagnosis": {
                                "root_cause": "No active platform issue detected",
                                "root_cause_layer": "platform",
                                "root_cause_entities": ["cluster:default"],
                                "confidence": 0.9,
                                "impact_summary": "Current health checks do not show an active issue.",
                                "affected_services": ["platform"],
                                "triage_priority": "P3",
                                "diagnosis_certainty": "confirmed",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await run_diagnosis(
                query="Investigate platform health without changing anything.",
                context=_happy_context(),
                variables={"promql": "up"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["prometheus.query_instant"],
            )
            self.assertEqual(result["status"], "diagnosed")
            self.assertGreaterEqual(len(llm.calls), 3)
            self.assertTrue(llm.calls[0]["messages"])
            self.assertTrue(llm.calls[1]["messages"])

    async def test_react_can_use_fixed_skill_tools_before_concluding(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Discover matching skills first.",
                    tool_calls=[
                        {
                            "name": "skills.list_skills",
                            "args": {"query": "vllm latency diagnosis"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Load the most relevant skill details.",
                    tool_calls=[
                        {
                            "name": "skills.load_skill",
                            "args": {"skill_id": "sre:vllm-diagnosis"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Read the triage note before executing the script.",
                    tool_calls=[
                        {
                            "name": "skills.read_skill_ref",
                            "args": {"skill_id": "sre:vllm-diagnosis", "reference": "triage.md"},
                            "id": "call-3",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Run the diagnosis script now.",
                    tool_calls=[
                        {
                            "name": "skills.run_skill",
                            "args": {"skill_id": "sre:vllm-diagnosis", "script": "diagnose.sh", "args": ["quick"]},
                            "id": "call-4",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The skill output confirms the root cause.",
                            "diagnosis": {
                                "root_cause": "Reusable skill completed the latency triage",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.84,
                                "impact_summary": "The Claude-style skill collected enough evidence to conclude.",
                                "affected_services": ["service:test"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "vllm-diagnosis"
            (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
            (skill_dir / "references").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: sre:vllm-diagnosis
description: Diagnose vLLM latency with a Claude-style skill.
---

# vLLM Diagnosis
""",
                encoding="utf-8",
            )
            (skill_dir / "scripts" / "diagnose.sh").write_text("#!/usr/bin/env bash\necho script-$1\n", encoding="utf-8")
            (skill_dir / "references" / "triage.md").write_text("triage guide\n", encoding="utf-8")

            from sre_agent.skills import SkillRegistry

            result = await run_diagnosis(
                query="Investigate a vLLM latency alert using reusable skills first.",
                context=_happy_context(),
                variables={"promql": "up"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=[
                    "skills.list_skills",
                    "skills.load_skill",
                    "skills.read_skill_ref",
                    "skills.run_skill",
                ],
                skill_registry=SkillRegistry(root=skill_root),
            )
            self.assertEqual(result["status"], "diagnosed")
            self.assertEqual(
                [item["tool"] for item in result["tool_runs"]],
                ["skills.list_skills", "skills.load_skill", "skills.read_skill_ref", "skills.run_skill"],
            )
            self.assertEqual(llm.calls[0]["tool_choice"], "required")
            self.assertEqual(llm.calls[-1]["tool_choice"], "auto")
            self.assertEqual(result["tool_runs"][-1]["data"]["status"], "success")

    async def test_first_round_binds_only_skill_tools_when_available(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Discover matching skills first.",
                    tool_calls=[
                        {
                            "name": "skills.list_skills",
                            "args": {"query": "gpu missing card diagnosis"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Load the discovered skill details.",
                    tool_calls=[
                        {
                            "name": "skills.load_skill",
                            "args": {"skill_id": "builtin-gpu-drop-diagnosis"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Run the discovered skill script.",
                    tool_calls=[
                        {
                            "name": "skills.run_skill",
                            "args": {"skill_id": "builtin-gpu-drop-diagnosis", "script": "gpu_drop_recover.sh"},
                            "id": "call-3",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Skill-first round completed.",
                            "diagnosis": {
                                "root_cause": "Skill discovery completed",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.6,
                                "impact_summary": "The first round was restricted to skill tools.",
                                "affected_services": ["service:test"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "gpu-drop-diagnosis"
            (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
id: builtin-gpu-drop-diagnosis
name: GPU Drop Diagnosis
description: Diagnose missing GPU and fallen-off-bus incidents.
tags:
  - gpu
  - missing
  - gpucardmissing
---

# GPU Drop Diagnosis
""",
                encoding="utf-8",
            )
            (skill_dir / "scripts" / "gpu_drop_recover.sh").write_text(
                "#!/usr/bin/env bash\necho ok\n",
                encoding="utf-8",
            )

            from sre_agent.skills import SkillRegistry

            result = await run_diagnosis(
                query="Diagnose GPUCardMissing using skills first.",
                context=_happy_context(),
                variables={"alert_name": "GPUCardMissing"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=None,
                allowed_tool_names=[
                    "skills.list_skills",
                    "skills.load_skill",
                    "skills.read_skill_ref",
                    "skills.run_skill",
                    "gpu.get_metrics",
                    "gpu.get_processes",
                ],
                skill_registry=SkillRegistry(root=skill_root),
            )

        self.assertEqual(result["status"], "diagnosed")
        first_bound_tool_names = [str(getattr(tool, "name", "")) for tool in llm.calls[0]["tools"]]
        self.assertEqual(
            first_bound_tool_names,
            ["skills.list_skills", "skills.load_skill", "skills.read_skill_ref", "skills.run_skill"],
        )
        self.assertEqual(result["tool_runs"][0]["tool"], "skills.list_skills")

    async def test_first_round_rejects_unbound_tool_calls_from_provider(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="I will inspect GPU metrics first.",
                    tool_calls=[
                        {
                            "name": "gpu.get_metrics",
                            "args": {"node": "10.11.4.13", "namespace": "monitoring"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The provider returned an out-of-policy tool call, so execution was rejected.",
                            "diagnosis": {
                                "root_cause": "Tool policy violation from provider tool call",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.7,
                                "impact_summary": "The first turn rejected a non-skill tool call.",
                                "affected_services": ["service:test"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )

        result = await run_diagnosis(
            query="Diagnose GPUCardMissing using skills first.",
            context=_happy_context(),
            variables={"alert_name": "GPUCardMissing"},
            llm=llm,
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            checkpoint_dir=None,
            allowed_tool_names=[
                "skills.list_skills",
                "skills.load_skill",
                "skills.read_skill_ref",
                "skills.run_skill",
                "gpu.get_metrics",
                "gpu.get_processes",
            ],
        )

        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["tool_runs"][0]["tool"], "gpu.get_metrics")
        self.assertFalse(result["tool_runs"][0]["success"])
        self.assertIn("not allowed in the current turn", result["tool_runs"][0]["error"])
        self.assertIn("skills.list_skills", result["tool_runs"][0]["error"])

    async def test_repeated_skill_list_is_promoted_to_load_skill(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Discover matching skills first.",
                    tool_calls=[
                        {
                            "name": "skills.list_skills",
                            "args": {"query": "gpu missing card diagnosis"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="List matching skills again.",
                    tool_calls=[
                        {
                            "name": "skills.list_skills",
                            "args": {"query": "gpu missing card diagnosis"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The system promoted repeated discovery to skill loading.",
                            "diagnosis": {
                                "root_cause": "已命中高相关 skill，并自动推进到加载详情阶段",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.78,
                                "impact_summary": "重复的 skills.list_skills 已被改写为 skills.load_skill。",
                                "affected_services": ["service:test"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "gpu-drop-diagnosis"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: GPU Drop Diagnosis
description: Diagnose missing GPU and fallen-off-bus incidents.
tags:
  - gpu
  - missing
  - gpucardmissing
---

# GPU Drop Diagnosis
""",
                encoding="utf-8",
            )

            from sre_agent.skills import SkillRegistry

            result = await run_diagnosis(
                query="Diagnose GPUCardMissing using skills first.",
                context=_happy_context(),
                variables={"alert_name": "GPUCardMissing"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=None,
                allowed_tool_names=[
                    "skills.list_skills",
                    "skills.load_skill",
                    "skills.read_skill_ref",
                    "skills.run_skill",
                ],
                skill_registry=SkillRegistry(root=skill_root),
            )

        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual([item["tool"] for item in result["tool_runs"][:2]], ["skills.list_skills", "skills.load_skill"])
        self.assertEqual(result["tool_runs"][1]["params"]["skill_id"], "gpu-drop-diagnosis")

    async def test_high_score_skill_listing_auto_loads_without_repeat(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Discover matching skills first.",
                    tool_calls=[
                        {
                            "name": "skills.list_skills",
                            "args": {"query": "gpu missing card nvidia-smi node diagnostic"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="I should continue with GPU evidence gathering.",
                    tool_calls=[],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The high-score skill was auto-loaded after discovery.",
                            "diagnosis": {
                                "root_cause": "已自动加载高分匹配的 skill 详情",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.8,
                                "impact_summary": "命中高分 skill 后无需等待模型重复 list_skills。",
                                "affected_services": ["service:test"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    ),
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "gpu-drop-diagnosis"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
id: builtin-gpu-drop-diagnosis
name: GPU Drop Diagnosis
description: Diagnose missing GPU and fallen-off-bus incidents.
tags:
  - gpu
  - missing
  - gpucardmissing
---

# GPU Drop Diagnosis
""",
                encoding="utf-8",
            )

            from sre_agent.skills import SkillRegistry

            result = await run_diagnosis(
                query="Diagnose GPUCardMissing using skills first.",
                context=_happy_context(),
                variables={"alert_name": "GPUCardMissing"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=None,
                allowed_tool_names=[
                    "skills.list_skills",
                    "skills.load_skill",
                    "skills.read_skill_ref",
                    "skills.run_skill",
                ],
                skill_registry=SkillRegistry(root=skill_root),
            )

        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual([item["tool"] for item in result["tool_runs"][:2]], ["skills.list_skills", "skills.load_skill"])
        self.assertEqual(result["tool_runs"][1]["params"]["skill_id"], "builtin-gpu-drop-diagnosis")

    async def test_single_script_skill_auto_runs_after_load(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Discover matching skills first.",
                    tool_calls=[
                        {
                            "name": "skills.list_skills",
                            "args": {"query": "gpu missing card nvidia-smi node diagnostic"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="I should inspect the loaded skill details.",
                    tool_calls=[],
                ),
                AIMessage(
                    content="I should continue investigating.",
                    tool_calls=[],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The single-script skill was auto-executed after loading.",
                            "diagnosis": {
                                "root_cause": "已自动执行单脚本 skill",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.82,
                                "impact_summary": "load_skill 成功且只有一个脚本时，系统会自动推进到 run_skill。",
                                "affected_services": ["service:test"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    ),
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "gpu-drop-diagnosis"
            (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
id: builtin-gpu-drop-diagnosis
name: GPU Drop Diagnosis
description: Diagnose missing GPU and fallen-off-bus incidents.
tags:
  - gpu
  - missing
  - gpucardmissing
---

# GPU Drop Diagnosis
""",
                encoding="utf-8",
            )
            (skill_dir / "scripts" / "gpu_drop_recover.sh").write_text(
                "#!/usr/bin/env bash\necho auto-run\n",
                encoding="utf-8",
            )

            from sre_agent.skills import SkillRegistry

            result = await run_diagnosis(
                query="Diagnose GPUCardMissing using skills first.",
                context=_happy_context(),
                variables={"alert_name": "GPUCardMissing"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=None,
                allowed_tool_names=[
                    "skills.list_skills",
                    "skills.load_skill",
                    "skills.read_skill_ref",
                    "skills.run_skill",
                ],
                skill_registry=SkillRegistry(root=skill_root),
            )

        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(
            [item["tool"] for item in result["tool_runs"][:3]],
            ["skills.list_skills", "skills.load_skill", "skills.run_skill"],
        )
        self.assertEqual(result["tool_runs"][2]["params"]["skill_id"], "builtin-gpu-drop-diagnosis")
        self.assertEqual(result["tool_runs"][2]["params"]["script"], "gpu_drop_recover.sh")

    async def test_react_happy_path_generates_trace_and_diagnosis(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Collect Prometheus evidence first.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "up"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Prometheus is healthy and no failure signal is present.",
                            "diagnosis": {
                                "root_cause": "No active platform issue detected",
                                "root_cause_layer": "platform",
                                "root_cause_entities": ["cluster:default"],
                                "confidence": 0.9,
                                "impact_summary": "Current health checks do not show an active issue.",
                                "affected_services": ["platform"],
                                "triage_priority": "P3",
                                "diagnosis_certainty": "confirmed",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await run_diagnosis(
                query="Investigate platform health without changing anything.",
                context=_happy_context(),
                variables={"promql": "up"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["prometheus.query_instant"],
            )
            self.assertEqual(result["status"], "diagnosed")
            self.assertEqual(len(result["tool_runs"]), 1)
            self.assertGreaterEqual(len(result["llm_interactions"]), 2)
            self.assertIn("prompt_messages", result["llm_interactions"][0])
            self.assertEqual(result["tool_runs"][0]["tool"], "prometheus.query_instant")
            self.assertEqual(llm.calls[0]["tool_choice"], "required")
            self.assertEqual(llm.calls[1]["tool_choice"], "auto")
            diagnosis = DiagnosisResult.model_validate(result["diagnosis_result"])
            self.assertEqual(diagnosis.root_cause_layer, "platform")
            self.assertGreaterEqual(len(diagnosis.hypotheses), 3)
            trace = ThinkingTrace.from_langraph_state(result["trace_items"])
            self.assertGreaterEqual(len(trace.steps), 3)
            snapshot_files = list(Path(tmpdir).glob(f"{result['session_id']}-*.json"))
            self.assertTrue(snapshot_files)

    async def test_prompt_renders_skill_tools_without_ranked_skill_guidance(self) -> None:
        registry = build_default_registry()
        prompt = build_system_prompt(
            registry,
            allowed_tool_names=["skills.list_skills", "skills.load_skill", "skills.read_skill_ref", "skills.run_skill"],
        )
        self.assertIn("skills.list_skills", prompt)
        self.assertIn("skills.run_skill", prompt)
        self.assertNotIn("Current turn guidance:", prompt)
        self.assertIn("k8s.apply_manifest", prompt)

    async def test_tc_evidence_forces_consistent_root_cause_when_initial_conclusion_is_ambiguous(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Inspect tc qdisc first.",
                    tool_calls=[
                        {
                            "name": "network.get_tc_qdisc",
                            "args": {"node": "worker-03", "iface": "roce"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "目前证据不足，暂时只能给出保守判断。",
                            "diagnosis": {
                                "root_cause": "证据不足，暂无法确认根因",
                                "root_cause_layer": "service",
                                "root_cause_entities": [],
                                "confidence": 0.42,
                                "impact_summary": "需要更多证据。",
                                "affected_services": ["inference-service"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "ambiguous",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "已根据 tc/netem 证据纠偏诊断结论。",
                            "diagnosis": {
                                "root_cause": "节点存在 tc/netem 注入规则导致网络时延抖动",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["worker-03"],
                                "confidence": 0.82,
                                "impact_summary": "tc qdisc 出现 netem delay 120ms，与告警一致。",
                                "affected_services": ["inference-service"],
                                "triage_priority": "P1",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await run_diagnosis(
                query="Diagnose NetworkLatencyHigh100ms alert on worker-03.",
                context=_tc_evidence_context(),
                variables={},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["network.get_tc_qdisc"],
            )

            self.assertEqual(result["status"], "diagnosed")
            root_cause = str(result["diagnosis_result"]["root_cause"]).lower()
            self.assertTrue("tc" in root_cause or "netem" in root_cause)
            self.assertTrue(result["evidence_signals"]["tc_netem_present"])
            self.assertIn("netem_present=true", result["tool_runs"][0]["prompt_summary"])
            self.assertTrue(result["tool_runs"][0]["key_fields"]["netem_present"])

    async def test_loop_guard_forces_final_turn_after_repeated_same_tool_calls(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Collect NIC counters.",
                    tool_calls=[
                        {
                            "name": "network.get_nic_counters",
                            "args": {"node": "worker-03"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Collect NIC counters again.",
                    tool_calls=[
                        {
                            "name": "network.get_nic_counters",
                            "args": {"node": "worker-03"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Collect NIC counters one more time.",
                    tool_calls=[
                        {
                            "name": "network.get_nic_counters",
                            "args": {"node": "worker-03"},
                            "id": "call-3",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "重复采样收益不足，直接总结。",
                            "diagnosis": {
                                "root_cause": "网络计数器重复采样未发现新增异常",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["worker-03"],
                                "confidence": 0.6,
                                "impact_summary": "重复调用触发 loop guard，转入总结。",
                                "affected_services": ["inference-service"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "ambiguous",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await run_diagnosis(
                query="Diagnose network jitter.",
                context=_happy_context(),
                variables={},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["network.get_nic_counters"],
                max_steps=8,
            )

            self.assertEqual(result["status"], "diagnosed")
            self.assertTrue(result["loop_guard"]["triggered"])
            self.assertTrue(result["force_final_turn"] is False)
            self.assertEqual(llm.calls[-1]["tool_choice"], "none")

    async def test_step_timeout_returns_step_timeout_state(self) -> None:
        result = await run_diagnosis(
            query="Diagnose with a slow llm.",
            context=_happy_context(),
            variables={},
            llm=_SlowLLM(),
            step_timeout_sec=0.05,
            total_timeout_sec=1.0,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "step_timeout")
        self.assertIn("timed out", result["summary"])

    async def test_session_timeout_returns_timeout_state(self) -> None:
        result = await run_diagnosis(
            query="Diagnose with a slow llm.",
            context=_happy_context(),
            variables={},
            llm=_SlowLLM(),
            step_timeout_sec=1.0,
            total_timeout_sec=0.05,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["error"], "diagnosis session timed out")


if __name__ == "__main__":
    unittest.main()
