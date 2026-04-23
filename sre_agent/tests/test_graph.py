from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

import sre_agent.agent.graph as graph_module
import sre_agent.agent.nodes as nodes_module
from sre_agent.ttft_process_policy import is_ttft_suspect_process
from sre_agent.agent import run_diagnosis, run_diagnosis_stream
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


class _TimeoutThenSuccessBoundLLM:
    def __init__(self, parent: "_TimeoutThenSuccessLLM", tool_choice: str) -> None:
        self._parent = parent
        self._tool_choice = tool_choice

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return await self._parent._ainvoke(messages, tool_choice=self._tool_choice)  # noqa: SLF001


class _TimeoutThenSuccessLLM:
    def __init__(self, response: AIMessage) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def _ainvoke(self, messages: list[Any], *, tool_choice: str) -> AIMessage:
        self.calls.append({"messages": messages, "tool_choice": tool_choice})
        if len(self.calls) == 1:
            import asyncio

            await asyncio.sleep(0.2)
        return self._response

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return await self._ainvoke(messages, tool_choice="none")

    def bind_tools(self, _tools: list[Any], tool_choice: str = "auto") -> _TimeoutThenSuccessBoundLLM:
        return _TimeoutThenSuccessBoundLLM(self, tool_choice)


class _TimeoutAlwaysBoundLLM:
    def __init__(self, parent: "_TimeoutAlwaysLLM", tool_choice: str) -> None:
        self._parent = parent
        self._tool_choice = tool_choice

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return await self._parent._ainvoke(messages, tool_choice=self._tool_choice)  # noqa: SLF001


class _TimeoutAlwaysLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def _ainvoke(self, messages: list[Any], *, tool_choice: str) -> AIMessage:
        self.calls.append({"messages": messages, "tool_choice": tool_choice})
        import asyncio

        await asyncio.sleep(0.2)
        return AIMessage(content="{}")

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return await self._ainvoke(messages, tool_choice="none")

    def bind_tools(self, _tools: list[Any], tool_choice: str = "auto") -> _TimeoutAlwaysBoundLLM:
        return _TimeoutAlwaysBoundLLM(self, tool_choice)


class _FakeStreamChunk:
    def __init__(self, content: str | None = None, *, reasoning_content: str | None = None) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.additional_kwargs = {}


class _FakeStreamGraph:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def astream_events(self, *_args: Any, **_kwargs: Any):  # noqa: ANN401
        for event in self._events:
            yield event


class _ResilientFakeBoundModel:
    def __init__(self, parent: "_ResilientFakeChatOpenAI", tool_choice: str) -> None:
        self._parent = parent
        self._tool_choice = tool_choice

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return await self._parent._ainvoke(messages, tool_choice=self._tool_choice)  # noqa: SLF001


class _ResilientFakeChatOpenAI:
    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self.model = str(kwargs.get("model", "")).strip()

    async def _ainvoke(self, _messages: list[Any], *, tool_choice: str) -> AIMessage:
        if self.model == "glm-5.1":
            raise RuntimeError('{"error":{"code":"1305","message":"该模型当前访问量过大，请您稍后再试"}}')
        return AIMessage(content=f"ok:{self.model}:{tool_choice}")

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return await self._ainvoke(messages, tool_choice="none")

    def bind_tools(self, _tools: list[Any], tool_choice: str = "auto") -> _ResilientFakeBoundModel:
        return _ResilientFakeBoundModel(self, tool_choice)


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
            self.assertIn(str(llm.calls[-1]["tool_choice"]), {"auto", "none"})
            self.assertEqual(result["tool_runs"][-1]["data"]["status"], "success")
            system_prompts = []
            tool_ledger_sections = []
            for call in llm.calls:
                for message in call.get("messages", []):
                    role = str(getattr(message, "type", "") or "").strip()
                    content = str(getattr(message, "content", "") or "")
                    if role == "system":
                        system_prompts.append(content)
                    if role == "human":
                        evidence_kind = str((getattr(message, "additional_kwargs", {}) or {}).get("evidence_kind", "") or "").strip()
                        if evidence_kind == "tool_ledger":
                            tool_ledger_sections.append(content)
            self.assertTrue(any("Active skill guidance:" in text for text in system_prompts))
            self.assertTrue(any("# vLLM Diagnosis" in text for text in system_prompts))
            self.assertTrue(tool_ledger_sections)
            self.assertTrue(any('"content_len":' in text for text in tool_ledger_sections))
            self.assertFalse(any("# vLLM Diagnosis" in text for text in tool_ledger_sections))

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

    async def test_load_skill_success_adds_persistent_skill_summary_trace(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Find relevant skills first.",
                    tool_calls=[
                        {
                            "name": "skills.list_skills",
                            "args": {"query": "gpu thermal diagnosis"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Load matched skill.",
                    tool_calls=[
                        {
                            "name": "skills.load_skill",
                            "args": {"skill_id": "gpu-thermal-diagnosis"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Skill summary is enough for this test.",
                            "diagnosis": {
                                "root_cause": "skill loaded",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.6,
                                "impact_summary": "Loaded skill and produced summary trace.",
                                "affected_services": ["service:test"],
                                "triage_priority": "P3",
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
            skill_dir = skill_root / "gpu-thermal-diagnosis"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: GPU Thermal Diagnosis
description: >
  GPU温度异常专项诊断技能。当用户报告GPU温度过高、thermal throttling、风扇异常、
  散热系统故障、频繁降频、推理性能退化与温度关联等问题时触发。
  适用场景：GPUTemperatureHigh告警、thermal throttle、GPU降频、风扇转速异常、
  机房环境温度问题、机柜风道问题、BMC风扇控制策略异常。
---

# GPU Thermal Diagnosis
""",
                encoding="utf-8",
            )

            from sre_agent.skills import SkillRegistry

            result = await run_diagnosis(
                query="Diagnose GPU thermal issue with skills first.",
                context=_happy_context(),
                variables={},
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

        trace_items = [item for item in result.get("trace_items", []) if isinstance(item, dict)]
        self.assertTrue(
            any(
                str((item.get("tool_params") or {}).get("kind", "")) == "skill_load_summary"
                and "GPU Thermal Diagnosis" in str(item.get("content", ""))
                and "适用场景：GPUTemperatureHigh告警" in str(item.get("content", ""))
                for item in trace_items
            )
        )

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
            self.assertGreaterEqual(len(diagnosis.hypotheses), 1)
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

    async def test_prompt_includes_active_skill_guidance_when_provided(self) -> None:
        registry = build_default_registry()
        prompt = build_system_prompt(
            registry,
            allowed_tool_names=["skills.list_skills", "skills.load_skill", "skills.read_skill_ref", "skills.run_skill"],
            active_skill_id="builtin-gpu-thermal-diagnosis",
            active_skill_content="# GPU Thermal Diagnosis\nStep 1: collect fan status",
        )
        self.assertIn("Active skill guidance:", prompt)
        self.assertIn("active_skill_id: builtin-gpu-thermal-diagnosis", prompt)
        self.assertIn("# GPU Thermal Diagnosis", prompt)

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
            self.assertFalse(result["loop_guard"]["triggered"])
            self.assertFalse(result["force_final_turn"])
            self.assertEqual(llm.calls[-1]["tool_choice"], "none")
            trace_items = list(result.get("trace_items", []))
            self.assertTrue(
                any(
                    isinstance(item, dict)
                    and str((item.get("tool_params") or {}).get("kind", "")) == "duplicate_tool_suppressed"
                    for item in trace_items
                )
            )

    async def test_act_node_deduplicates_same_round_duplicate_tool_calls(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Collect pod list once.",
                    tool_calls=[
                        {
                            "name": "k8s.list_pods",
                            "args": {"namespace": "default"},
                            "id": "call-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "k8s.list_pods",
                            "args": {"namespace": "default"},
                            "id": "call-2",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "已收集到足够证据并完成总结。",
                            "diagnosis": {
                                "root_cause": "未发现异常，重复调用已被抑制。",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["default"],
                                "confidence": 0.65,
                                "impact_summary": "同轮重复工具调用已去重。",
                                "affected_services": ["demo"],
                                "triage_priority": "P3",
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
                query="Diagnose duplicate list pod calls.",
                context=_happy_context(),
                variables={},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["k8s.list_pods"],
                max_steps=6,
            )

            executed_tools = [item["tool"] for item in result["tool_runs"]]
            self.assertEqual(executed_tools.count("k8s.list_pods"), 1)
            trace_items = list(result.get("trace_items", []))
            self.assertTrue(
                any(
                    isinstance(item, dict)
                    and str((item.get("tool_params") or {}).get("kind", "")) == "duplicate_tool_suppressed"
                    for item in trace_items
                )
            )

    async def test_cross_round_identical_tool_call_is_suppressed_without_new_tool_run(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Collect pod list.",
                    tool_calls=[
                        {
                            "name": "k8s.list_pods",
                            "args": {"namespace": "default"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Collect pod list again.",
                    tool_calls=[
                        {
                            "name": "k8s.list_pods",
                            "args": {"namespace": "default"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "重复同参工具调用已抑制，进入总结。",
                            "diagnosis": {
                                "root_cause": "重复调用已被抑制。",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["default"],
                                "confidence": 0.7,
                                "impact_summary": "同参重复调用不会新增工具执行。",
                                "affected_services": ["demo"],
                                "triage_priority": "P3",
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
                query="Diagnose duplicate list pod calls across rounds.",
                context=_happy_context(),
                variables={},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["k8s.list_pods"],
                max_steps=8,
            )

            executed_tools = [item["tool"] for item in result["tool_runs"]]
            self.assertEqual(executed_tools.count("k8s.list_pods"), 1)
            trace_items = list(result.get("trace_items", []))
            self.assertTrue(
                any(
                    isinstance(item, dict)
                    and str((item.get("tool_params") or {}).get("kind", "")) == "duplicate_tool_suppressed"
                    and str((item.get("tool_params") or {}).get("reason", "")) == "cross_round_duplicate"
                    for item in trace_items
                )
            )

    async def test_cross_round_identical_tool_call_allows_retry_after_failure(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Query fan status.",
                    tool_calls=[
                        {
                            "name": "bmc.get_fan_status",
                            "args": {"node": "10.11.4.13"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Retry fan status with same params.",
                    tool_calls=[
                        {
                            "name": "bmc.get_fan_status",
                            "args": {"node": "10.11.4.13"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "工具失败后允许同参重试。",
                            "diagnosis": {
                                "root_cause": "BMC 通道不可用导致查询失败。",
                                "root_cause_layer": "platform",
                                "root_cause_entities": ["10.11.4.13"],
                                "confidence": 0.6,
                                "impact_summary": "同参失败调用不会被去重抑制。",
                                "affected_services": ["demo"],
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
            result = await run_diagnosis(
                query="Diagnose bmc fan status retries.",
                context=_happy_context(),
                variables={},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["bmc.get_fan_status"],
                max_steps=8,
            )

            bmc_runs = [item for item in result["tool_runs"] if item.get("tool") == "bmc.get_fan_status"]
            self.assertEqual(len(bmc_runs), 2)
            self.assertTrue(all(not bool(item.get("success", True)) for item in bmc_runs))

    async def test_cross_round_dedup_considers_equivalent_arg_order(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Run command once.",
                    tool_calls=[
                        {
                            "name": "ssh.run_command",
                            "args": {"node": "10.11.4.13", "command": "nvidia-smi -L"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Run same command with reordered args.",
                    tool_calls=[
                        {
                            "name": "ssh.run_command",
                            "args": {"command": "nvidia-smi -L", "node": "10.11.4.13"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "参数顺序不同但语义相同，重复调用已抑制。",
                            "diagnosis": {
                                "root_cause": "重复命令调用已被去重。",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["10.11.4.13"],
                                "confidence": 0.68,
                                "impact_summary": "参数顺序差异不再导致重复执行。",
                                "affected_services": ["demo"],
                                "triage_priority": "P3",
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
                query="Diagnose duplicate ssh command with reordered args.",
                context=_happy_context(),
                variables={"namespace": "nvidia-dcgm"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["ssh.run_command"],
                max_steps=8,
            )

            ssh_runs = [item for item in result["tool_runs"] if item.get("tool") == "ssh.run_command"]
            self.assertEqual(len(ssh_runs), 1)

    async def test_cross_round_dedup_uses_normalized_params_with_runtime_defaults(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Collect NIC counters with explicit namespace.",
                    tool_calls=[
                        {
                            "name": "network.get_nic_counters",
                            "args": {"node": "10.11.4.13", "namespace": "nvidia-dcgm"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Collect NIC counters again with different namespace argument.",
                    tool_calls=[
                        {
                            "name": "network.get_nic_counters",
                            "args": {"node": "10.11.4.13", "namespace": "other-ns"},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "runtime defaults 归一化后判定为重复调用。",
                            "diagnosis": {
                                "root_cause": "NIC 无新增异常证据。",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["10.11.4.13"],
                                "confidence": 0.62,
                                "impact_summary": "归一化参数后重复查询被抑制。",
                                "affected_services": ["demo"],
                                "triage_priority": "P3",
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
                query="Diagnose duplicate NIC counter calls after normalization.",
                context=_happy_context(),
                variables={"namespace": "nvidia-dcgm"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["network.get_nic_counters"],
                max_steps=8,
            )

            nic_runs = [item for item in result["tool_runs"] if item.get("tool") == "network.get_nic_counters"]
            self.assertEqual(len(nic_runs), 1)

    async def test_loop_guard_triggers_on_repeated_prometheus_family_calls(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Query metric A.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "sum(rate(http_request_duration_seconds_count{service=\"svc-a\"}[5m]))"},
                            "id": "call-a",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Query metric B.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "sum(rate(http_request_duration_seconds_count{service=\"svc-b\"}[5m]))"},
                            "id": "call-b",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Query metric C.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "sum(rate(http_request_duration_seconds_count{service=\"svc-c\"}[5m]))"},
                            "id": "call-c",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "同类工具调用重复，进入总结。",
                            "diagnosis": {
                                "root_cause": "同类 Prometheus 查询未提供新增证据。",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["svc-a"],
                                "confidence": 0.58,
                                "impact_summary": "触发工具族 loop guard。",
                                "affected_services": ["svc-a"],
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
                query="Diagnose repeated prometheus family calls.",
                context=_happy_context(),
                variables={},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["prometheus.query_instant"],
                max_steps=8,
            )

            self.assertEqual(result["status"], "diagnosed")
            self.assertTrue(result["loop_guard"]["triggered"])
            self.assertEqual(llm.calls[-1]["tool_choice"], "none")

    async def test_ttft_prometheus_budget_limits_real_execution_and_reuses_cached_results(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Verify TTFT baseline metric.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{service=\"svc-a\"}[5m])) by (le))"},
                            "id": "call-prom-a",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Cross-check with second query.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service=\"svc-b\"}[5m])) by (le))"},
                            "id": "call-prom-b",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="Try one more query that should be budget-suppressed.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "histogram_quantile(0.90, sum(rate(http_request_duration_seconds_bucket{service=\"svc-c\"}[5m])) by (le))"},
                            "id": "call-prom-c",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "TTFT query budget reached; conclude with existing evidence.",
                            "diagnosis": {
                                "root_cause": "TTFT increase observed with stable GPU evidence.",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["svc-a"],
                                "confidence": 0.66,
                                "impact_summary": "Suppressed redundant prometheus queries after budget reached.",
                                "affected_services": ["svc-a"],
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
            result = await run_diagnosis(
                query="Diagnose AIServiceTTFT with deterministic query budget.",
                context=_happy_context(),
                variables={},
                llm=llm,
                alert_snapshot={"alert_name": "AIServiceTTFTP99High"},
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["prometheus.query_instant"],
                max_steps=8,
            )

            prom_runs = [item for item in result["tool_runs"] if item.get("tool") == "prometheus.query_instant"]
            real_exec_runs = [item for item in prom_runs if str(item.get("source", "tool")) == "tool"]
            reuse_runs = [item for item in prom_runs if "reuse" in str(item.get("source", ""))]
            self.assertLessEqual(len(real_exec_runs), 2)
            self.assertGreaterEqual(len(reuse_runs), 1)
            self.assertFalse(result["force_final_turn"])
            trace_items = [item for item in result.get("trace_items", []) if isinstance(item, dict)]
            self.assertTrue(
                any(
                    str((item.get("tool_params") or {}).get("kind", "")) == "prometheus_query_suppressed"
                    for item in trace_items
                )
            )

    def test_ttft_auto_kill_process_plan_generates_two_steps_with_process_canary(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="异常负载导致 TTFT 抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.71,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="同节点多进程压测争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        evidence_signals = {
            "ttft_suspect_process_present": True,
            "ttft_suspect_processes": [
                {"pid": 11001, "process_name": "load_simulator --tag ls_demo_a"},
                {"pid": 11002, "process_name": "load_simulator --tag ls_demo_b"},
            ],
        }
        tool_runs = [
            {
                "tool": "gpu.get_processes",
                "params": {"node": "10.11.4.13"},
                "success": True,
                "data": {},
            }
        ]

        plan = nodes_module._build_ttft_kill_process_plan_candidate(
            diagnosis=diagnosis,
            evidence_signals=evidence_signals,
            session_id="sess-ttft-demo-01",
            tool_runs=tool_runs,
            variables={},
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan["steps"]), 2)
        step_one = plan["steps"][0]
        step_two = plan["steps"][1]
        self.assertEqual(step_one["tool"], "kill_process")
        self.assertEqual(step_two["tool"], "kill_process")
        self.assertEqual(step_one["params"]["entity_id"], "proc:11001")
        self.assertEqual(step_two["params"]["entity_id"], "proc:11002")
        self.assertEqual(step_one["params"]["node"], "10.11.4.13")
        self.assertEqual(step_two["params"]["node"], "10.11.4.13")
        self.assertEqual(plan["canary"]["target_percentage"], 0.5)
        self.assertEqual(plan["canary"]["max_batches"], 2)
        self.assertFalse(plan["canary"]["progressive"])

    def test_ttft_auto_kill_process_plan_uses_process_node_when_differs_from_serving_node(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="外部压测源导致 TTFT 抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.12"],
            confidence=0.72,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="服务节点与压测源节点不一致",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        evidence_signals = {
            "ttft_suspect_process_present": True,
            "ttft_suspect_processes": [
                {"pid": 473156, "process_name": "python3 -m load_simulator run --only inference", "node": "10.11.4.13"},
                {"pid": 473220, "process_name": "python3 -m load_simulator run --only inference", "node": "10.11.4.13"},
            ],
        }
        tool_runs = [
            {
                "tool": "gpu.get_processes",
                "params": {"node": "10.11.4.12"},
                "success": True,
                "data": {},
            }
        ]

        plan = nodes_module._build_ttft_kill_process_plan_candidate(
            diagnosis=diagnosis,
            evidence_signals=evidence_signals,
            session_id="sess-ttft-cross-node-01",
            tool_runs=tool_runs,
            variables={"node": "10.11.4.12"},
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan["steps"]), 2)
        self.assertEqual(plan["steps"][0]["params"]["node"], "10.11.4.13")
        self.assertEqual(plan["steps"][1]["params"]["node"], "10.11.4.13")

    def test_ttft_auto_kill_process_plan_caps_to_six_suspects_and_batches(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="异常负载导致 TTFT 抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.71,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="同节点多进程压测争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        evidence_signals = {
            "ttft_suspect_process_present": True,
            "ttft_suspect_processes": [
                {"pid": 11000 + idx, "process_name": f"load_simulator --tag ls_demo_{idx}"}
                for idx in range(1, 8)
            ],
        }
        tool_runs = [
            {
                "tool": "gpu.get_processes",
                "params": {"node": "10.11.4.13"},
                "success": True,
                "data": {},
            }
        ]

        plan = nodes_module._build_ttft_kill_process_plan_candidate(
            diagnosis=diagnosis,
            evidence_signals=evidence_signals,
            session_id="sess-ttft-demo-06",
            tool_runs=tool_runs,
            variables={},
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan["steps"]), 6)
        self.assertEqual(plan["canary"]["max_batches"], 2)
        self.assertFalse(plan["canary"]["progressive"])
        self.assertAlmostEqual(plan["canary"]["target_percentage"], round(1.0 / 6, 4))

    def test_ttft_auto_kill_process_plan_prefers_gpu_burn_suspects_over_load_simulator(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="GPU contention drives TTFT spike",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.81,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="GPU burn and synthetic traffic overlap caused TTFT regression",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        evidence_signals = {
            "ttft_suspect_process_present": True,
            "ttft_suspect_processes": [
                {"pid": 10001, "process_name": "python -m load_simulator run --tag normal"},
                {"pid": 10002, "process_name": "fi_gpu_burn_gpu_contention_ad4cf1ee -m 100% -i 0 3600"},
                {"pid": 10003, "process_name": "fi_gpu_burn_gpu_contention_ad4cf1ee -m 100% -i 1 3600"},
            ],
        }
        tool_runs = [
            {
                "tool": "gpu.get_processes",
                "params": {"node": "10.11.4.13"},
                "success": True,
                "data": {},
            }
        ]

        plan = nodes_module._build_ttft_kill_process_plan_candidate(
            diagnosis=diagnosis,
            evidence_signals=evidence_signals,
            session_id="sess-ttft-gpu-burn-priority",
            tool_runs=tool_runs,
            variables={},
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan["steps"]), 2)
        self.assertEqual(plan["steps"][0]["params"]["entity_id"], "proc:10002")
        self.assertEqual(plan["steps"][1]["params"]["entity_id"], "proc:10003")
        self.assertEqual(plan["canary"]["max_batches"], 2)

    def test_extract_evidence_signals_collects_process_find_suspects(self) -> None:
        tool_runs = [
            {
                "tool": "process.find",
                "success": True,
                "params": {"pattern": "load_simulator", "node": "10.11.4.13"},
                "key_fields": {
                    "match_count": 2,
                    "node": "10.11.4.13",
                    "suspicious_load_present": True,
                    "suspicious_load_processes": [
                        {
                            "pid": 473156,
                            "process_name": "python3 -m load_simulator run --only inference",
                            "memory_mib": None,
                            "node": "10.11.4.13",
                        },
                        {
                            "pid": 473573,
                            "process_name": "python3 -m load_simulator run --only inference",
                            "memory_mib": None,
                            "node": "10.11.4.13",
                        },
                    ],
                },
                "data": {},
            }
        ]

        signals = nodes_module._extract_evidence_signals(tool_runs)
        self.assertTrue(signals["ttft_suspect_process_present"])
        suspects = signals["ttft_suspect_processes"]
        self.assertIsInstance(suspects, list)
        self.assertEqual(len(suspects), 2)
        self.assertEqual(suspects[0]["node"], "10.11.4.13")

    def test_extract_evidence_signals_filters_non_whitelisted_process_find_matches(self) -> None:
        tool_runs: list[dict[str, Any]] = [
            {
                "tool": "process.find",
                "success": True,
                "params": {"pattern": "load", "node": "10.11.4.13"},
                "key_fields": {
                    "match_count": 2,
                    "node": "10.11.4.13",
                    "suspicious_load_processes": [],
                },
                "data": {
                    "matches": [
                        {
                            "pid": 9783,
                            "process": "prometheus-config-reloader",
                            "command": "/bin/prometheus-config-reloader --reload-url=http://localhost:12345/-/reload",
                        },
                        {
                            "pid": 13312,
                            "process": "uvicorn",
                            "command": "uvicorn app.gateway.app:app --reload",
                        },
                    ]
                },
            }
        ]

        signals = nodes_module._extract_evidence_signals(tool_runs)
        self.assertFalse(signals["ttft_suspect_process_present"])
        self.assertEqual(signals["ttft_suspect_processes"], [])

    def test_ttft_strict_process_policy_rejects_reloader_and_backend(self) -> None:
        self.assertFalse(is_ttft_suspect_process("/bin/prometheus-config-reloader --reload-url=..."))
        self.assertFalse(is_ttft_suspect_process("uvicorn app.gateway.app:app --reload"))
        self.assertTrue(is_ttft_suspect_process("python -m load_simulator run --only inference"))
        self.assertTrue(is_ttft_suspect_process("fi_gpu_burn_gpu_contention_ad4cf1ee -m 100% -i 0 3600"))

    def test_has_probed_ttft_external_node_detects_existing_probe(self) -> None:
        tool_runs: list[dict[str, Any]] = [
            {
                "tool": "process.find",
                "params": {"pattern": "stress", "node": "10.11.4.13"},
                "data": {"node": "10.11.4.13"},
            },
        ]
        self.assertTrue(nodes_module._has_probed_ttft_external_node(tool_runs, "10.11.4.13"))
        self.assertFalse(nodes_module._has_probed_ttft_external_node(tool_runs, "10.11.4.99"))

    def test_has_probed_ttft_external_node_returns_false_for_empty(self) -> None:
        self.assertFalse(nodes_module._has_probed_ttft_external_node([], "10.11.4.13"))

    def test_has_probed_ttft_external_node_detects_via_data_node(self) -> None:
        tool_runs: list[dict[str, Any]] = [
            {
                "tool": "process.find",
                "params": {"pattern": "stress"},
                "data": {"node": "10.11.4.13"},
            },
        ]
        self.assertTrue(nodes_module._has_probed_ttft_external_node(tool_runs, "10.11.4.13"))

    def test_evaluate_ttft_min_coverage_detects_missing_external_probe(self) -> None:
        state: dict[str, Any] = {
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            }
        }
        tool_runs: list[dict[str, Any]] = [
            {"tool": "gpu.get_processes", "success": True, "params": {"node": "worker-03"}},
        ]
        met, missing = nodes_module._evaluate_ttft_min_coverage(state=state, tool_runs=tool_runs)
        self.assertFalse(met)
        self.assertIn("external_process_find", missing)
        self.assertNotIn("gpu_processes", missing)

    def test_evaluate_ttft_min_coverage_met_with_gpu_and_external_probe(self) -> None:
        state: dict[str, Any] = {
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            }
        }
        tool_runs: list[dict[str, Any]] = [
            {"tool": "gpu.get_processes", "success": True, "params": {"node": "worker-03"}},
            {
                "tool": "process.find",
                "success": False,
                "params": {"node": "10.11.4.13", "pattern": "load_simulator"},
                "error": "permission denied",
            },
        ]
        met, missing = nodes_module._evaluate_ttft_min_coverage(state=state, tool_runs=tool_runs)
        self.assertTrue(met)
        self.assertEqual(missing, [])

    def test_find_ttft_external_probe_auth_error_detects_permission_denied(self) -> None:
        tool_runs: list[dict[str, Any]] = [
            {
                "tool": "process.find",
                "success": False,
                "params": {"pattern": "load_simulator", "node": "10.11.4.13"},
                "error": "SSH error: Permission denied for user yuyonghao on host 10.11.4.13",
            }
        ]
        error = nodes_module._find_ttft_external_probe_auth_error(tool_runs, "10.11.4.13")
        self.assertIn("Permission denied", error)

    def test_find_ttft_external_probe_auth_error_ignores_after_success(self) -> None:
        tool_runs: list[dict[str, Any]] = [
            {
                "tool": "process.find",
                "success": False,
                "params": {"pattern": "load_simulator", "node": "10.11.4.13"},
                "error": "SSH error: Permission denied for user yuyonghao on host 10.11.4.13",
            },
            {
                "tool": "process.find",
                "success": True,
                "params": {"pattern": "load_simulator", "node": "10.11.4.13"},
                "data": {"node": "10.11.4.13", "count": 2},
            },
        ]
        error = nodes_module._find_ttft_external_probe_auth_error(tool_runs, "10.11.4.13")
        self.assertEqual(error, "")

    def test_normalize_step_params_in_place_normalizes_kill_signal_sigterm(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="ttft load contention",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.86,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="ttft elevated",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="confirmed",
        )
        registry = build_default_registry()
        step = {
            "tool": "kill_process",
            "params": {"node": "10.11.4.13", "pid": 12345, "signal": "SIGTERM"},
        }

        error = nodes_module._normalize_step_params_in_place(
            step,
            diagnosis=diagnosis,
            registry=registry,
            tool_runs=[],
            variables={},
        )

        self.assertIsNone(error)
        self.assertEqual(step["params"]["signal"], "TERM")

    def test_normalize_step_params_in_place_keeps_kill_signal_term(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="ttft load contention",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.86,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="ttft elevated",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="confirmed",
        )
        registry = build_default_registry()
        step = {
            "tool": "kill_process",
            "params": {"node": "10.11.4.13", "pid": 12345, "signal": "TERM"},
        }

        error = nodes_module._normalize_step_params_in_place(
            step,
            diagnosis=diagnosis,
            registry=registry,
            tool_runs=[],
            variables={},
        )

        self.assertIsNone(error)
        self.assertEqual(step["params"]["signal"], "TERM")

    def test_get_ttft_external_node_prefers_alert_snapshot(self) -> None:
        state: dict[str, Any] = {
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
            "variables": {"ttft_external_process_default_node": "10.11.4.99"},
        }
        self.assertEqual(nodes_module._get_ttft_external_node(state), "10.11.4.13")

    def test_get_ttft_external_node_falls_back_to_variables(self) -> None:
        state: dict[str, Any] = {
            "alert_snapshot": {"alert_name": "AIServiceTTFTP99High"},
            "variables": {"ttft_external_process_default_node": "10.11.4.99"},
        }
        self.assertEqual(nodes_module._get_ttft_external_node(state), "10.11.4.99")

    def test_get_ttft_external_node_returns_empty_when_absent(self) -> None:
        state: dict[str, Any] = {
            "alert_snapshot": {"alert_name": "AIServiceTTFTP99High"},
            "variables": {},
        }
        self.assertEqual(nodes_module._get_ttft_external_node(state), "")

    def test_is_force_canary_alert_name_matches_ttft_case_insensitive(self) -> None:
        self.assertTrue(nodes_module._is_force_canary_alert_name("AIServiceTTFTP99High"))
        self.assertTrue(nodes_module._is_force_canary_alert_name("AIServiceTTFT"))
        self.assertTrue(nodes_module._is_force_canary_alert_name("foo-ttft-bar"))
        self.assertFalse(nodes_module._is_force_canary_alert_name("NetworkLatencyHigh"))

    def test_normalize_remediation_plan_payload_force_canary_for_ttft_alert(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="异常负载导致 TTFT 抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.71,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="同节点多进程压测争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-force-canary",
            "root_cause": diagnosis.root_cause,
            "description": "terminate suspicious process",
            "steps": [
                {
                    "step_id": 1,
                    "description": "kill process",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:473156", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                }
            ],
            "estimated_impact": diagnosis.impact_summary,
            "confidence": diagnosis.confidence,
            "priority": diagnosis.triage_priority,
            "safety_level": "high",
        }

        plan = nodes_module._normalize_remediation_plan_payload(
            raw_plan=raw_plan,
            diagnosis=diagnosis,
            session_id="sess-force-canary",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="AIServiceTTFTP99High",
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNotNone(plan.canary)
        assert plan.canary is not None
        self.assertTrue(plan.canary.enabled)
        self.assertEqual(plan.canary.max_batches, 1)
        self.assertFalse(plan.canary.progressive)
        self.assertEqual(plan.canary.target_percentage, 1.0)
        self.assertGreaterEqual(plan.canary.monitor_duration, 60)

    def test_normalize_remediation_plan_payload_force_canary_for_other_ttft_alert(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="异常负载导致 TTFT 抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.71,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="同节点多进程压测争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-no-force-canary",
            "root_cause": diagnosis.root_cause,
            "description": "terminate suspicious process",
            "steps": [
                {
                    "step_id": 1,
                    "description": "kill process",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:473156", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                }
            ],
            "estimated_impact": diagnosis.impact_summary,
            "confidence": diagnosis.confidence,
            "priority": diagnosis.triage_priority,
            "safety_level": "high",
        }

        plan = nodes_module._normalize_remediation_plan_payload(
            raw_plan=raw_plan,
            diagnosis=diagnosis,
            session_id="sess-no-force-canary",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="AIServiceTTFTP95High",
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNotNone(plan.canary)
        assert plan.canary is not None
        self.assertTrue(plan.canary.enabled)
        self.assertFalse(plan.canary.progressive)
        self.assertEqual(plan.canary.max_batches, 1)
        self.assertEqual(plan.canary.target_percentage, 1.0)

    def test_normalize_remediation_plan_payload_force_canary_override_uses_two_batches_for_multiple_process_targets(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="异常负载导致 TTFT 抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.74,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="多进程负载争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-force-canary-override",
            "root_cause": diagnosis.root_cause,
            "description": "terminate suspicious processes",
            "steps": [
                {
                    "step_id": 1,
                    "description": "kill process 1",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:101", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                },
                {
                    "step_id": 2,
                    "description": "kill process 2",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:102", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                },
                {
                    "step_id": 3,
                    "description": "kill process 3",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:103", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                },
            ],
            "estimated_impact": diagnosis.impact_summary,
            "confidence": diagnosis.confidence,
            "priority": diagnosis.triage_priority,
            "safety_level": "high",
        }

        plan = nodes_module._normalize_remediation_plan_payload(
            raw_plan=raw_plan,
            diagnosis=diagnosis,
            session_id="sess-force-canary-override",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="NetworkLatencyHigh",
            force_canary_override=True,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNotNone(plan.canary)
        assert plan.canary is not None
        self.assertTrue(plan.canary.enabled)
        self.assertFalse(plan.canary.progressive)
        self.assertEqual(plan.canary.max_batches, 2)
        self.assertEqual(plan.canary.target_percentage, round(1.0 / 3, 4))

    def test_normalize_remediation_plan_payload_force_canary_corrects_invalid_monitor_duration(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="异常负载导致 TTFT 抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.74,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="同节点异常进程导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-force-canary-invalid-monitor-duration",
            "root_cause": diagnosis.root_cause,
            "description": "terminate suspicious process",
            "steps": [
                {
                    "step_id": 1,
                    "description": "kill process",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:473156", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                }
            ],
            "canary": {
                "enabled": False,
                "target_percentage": 0.0,
                "monitor_duration": 0,
                "success_criteria": [],
                "max_batches": 0,
            },
            "estimated_impact": diagnosis.impact_summary,
            "confidence": diagnosis.confidence,
            "priority": diagnosis.triage_priority,
            "safety_level": "high",
        }

        plan = nodes_module._normalize_remediation_plan_payload(
            raw_plan=raw_plan,
            diagnosis=diagnosis,
            session_id="sess-force-canary-invalid-monitor-duration",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="AIServiceTTFTP99High",
            force_canary_override=True,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNotNone(plan.canary)
        assert plan.canary is not None
        self.assertTrue(plan.canary.enabled)
        self.assertGreaterEqual(plan.canary.monitor_duration, 60)

    def test_normalize_remediation_plan_payload_force_canary_corrects_invalid_ratio_and_batches(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="异常负载导致 TTFT 抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.74,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="多进程异常负载导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-force-canary-invalid-ratio-batches",
            "root_cause": diagnosis.root_cause,
            "description": "terminate suspicious processes",
            "steps": [
                {
                    "step_id": 1,
                    "description": "kill process 1",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:101", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                },
                {
                    "step_id": 2,
                    "description": "kill process 2",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:102", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                },
            ],
            "canary": {
                "enabled": True,
                "target_percentage": 0,
                "monitor_duration": 0,
                "success_criteria": [],
                "max_batches": 0,
                "progressive": True,
            },
            "estimated_impact": diagnosis.impact_summary,
            "confidence": diagnosis.confidence,
            "priority": diagnosis.triage_priority,
            "safety_level": "high",
        }

        plan = nodes_module._normalize_remediation_plan_payload(
            raw_plan=raw_plan,
            diagnosis=diagnosis,
            session_id="sess-force-canary-invalid-ratio-batches",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="AIServiceTTFTP99High",
            force_canary_override=True,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNotNone(plan.canary)
        assert plan.canary is not None
        self.assertTrue(plan.canary.enabled)
        self.assertGreater(plan.canary.target_percentage, 0)
        self.assertEqual(plan.canary.max_batches, 2)
        self.assertFalse(plan.canary.progressive)

    def test_normalize_remediation_plan_payload_does_not_force_canary_for_non_ttft_alert(self) -> None:
        diagnosis = DiagnosisResult(
            root_cause="异常负载导致延迟抬高",
            root_cause_layer="service",
            root_cause_entities=["node:10.11.4.13"],
            confidence=0.71,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="同节点多进程压测争用导致延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            recommended_fix=None,
            triage_priority="P1",
            ranked_candidates=[],
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-no-force-canary-non-ttft",
            "root_cause": diagnosis.root_cause,
            "description": "terminate suspicious process",
            "steps": [
                {
                    "step_id": 1,
                    "description": "kill process",
                    "tool": "kill_process",
                    "params": {"node": "10.11.4.13", "entity_id": "proc:473156", "signal": "TERM"},
                    "verification": {"method": "wait", "wait_seconds": 10},
                    "timeout": 60,
                }
            ],
            "estimated_impact": diagnosis.impact_summary,
            "confidence": diagnosis.confidence,
            "priority": diagnosis.triage_priority,
            "safety_level": "high",
        }

        plan = nodes_module._normalize_remediation_plan_payload(
            raw_plan=raw_plan,
            diagnosis=diagnosis,
            session_id="sess-no-force-canary-non-ttft",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="GPUUtilizationHigh",
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIsNotNone(plan.canary)
        assert plan.canary is not None
        self.assertTrue(plan.canary.progressive)

    async def test_run_diagnosis_stream_emits_real_duration_and_backend_next_action_without_trace_duplicates(self) -> None:
        stream_events = [
            {
                "event": "on_chain_start",
                "name": "reason",
                "run_id": "run-reason-1",
                "data": {},
            },
            {
                "event": "on_chat_model_stream",
                "name": "ChatOpenAI",
                "run_id": "run-llm-1",
                "metadata": {"langgraph_node": "reason"},
                "data": {"chunk": _FakeStreamChunk("Analyzing telemetry...")},
            },
            {
                "event": "on_chain_end",
                "name": "reason",
                "run_id": "run-reason-1",
                "data": {
                    "output": {
                        "status": "diagnosing",
                        "step_count": 1,
                        "trace_items": [
                            {
                                "type": "thought",
                                "step": 1,
                                "timestamp": "2026-04-13T13:00:00+00:00",
                                "content": "Inspecting service latency and queue depth.",
                                "action": "tool_call",
                                "thought_key": "run-reason-1:reason",
                                "tool_name": "query_metrics",
                                "tool_params": {"service": "auth-svc"},
                                "confidence": 0.74,
                                "next_action": "Next action: call query_metrics for auth-svc and compare p95 with baseline.",
                            }
                        ],
                    }
                },
            },
            {
                "event": "on_chain_end",
                "name": "finalize",
                "run_id": "run-finalize-1",
                "data": {
                    "output": {
                        "status": "diagnosed",
                        "step_count": 2,
                        "trace_items": [
                            {
                                "type": "thought",
                                "step": 1,
                                "timestamp": "2026-04-13T13:00:00+00:00",
                                "content": "Inspecting service latency and queue depth.",
                                "action": "tool_call",
                                "thought_key": "run-reason-1:reason",
                                "tool_name": "query_metrics",
                                "tool_params": {"service": "auth-svc"},
                                "confidence": 0.74,
                                "next_action": "Next action: call query_metrics for auth-svc and compare p95 with baseline.",
                            }
                        ],
                        "diagnosis_result": {
                            "root_cause": "Node contention",
                            "root_cause_layer": "platform",
                            "root_cause_entities": ["node:worker-03"],
                            "confidence": 0.82,
                            "hypotheses": [],
                            "propagation_chain": [],
                            "impact_summary": "p95 latency increased due to contention.",
                            "affected_services": ["auth-svc"],
                            "triage_priority": "P1",
                            "diagnosis_certainty": "probable",
                            "ranked_candidates": [],
                        },
                    }
                },
            },
        ]

        with patch("sre_agent.agent.graph.create_sre_graph", return_value=_FakeStreamGraph(stream_events)):
            emitted: list[dict[str, Any]] = []
            async for event in run_diagnosis_stream(
                query="Diagnose auth latency",
                context=_happy_context(),
                variables={},
                checkpoint_dir=None,
                total_timeout_sec=10.0,
            ):
                emitted.append(event)

        token_event = next(item for item in emitted if item["type"] == "token_delta")
        self.assertEqual(token_event["data"]["node"], "reason")
        self.assertEqual(token_event["data"]["run_id"], "run-reason-1")
        self.assertEqual(token_event["data"]["thought_key"], "run-reason-1:reason")

        diagnosis_started = next(item for item in emitted if item["type"] == "diagnosis_started")
        self.assertEqual(diagnosis_started["data"]["bootstrap_state"], "thinking")

        node_started = next(item for item in emitted if item["type"] == "node_started")
        self.assertEqual(node_started["data"]["run_id"], "run-reason-1")
        self.assertEqual(node_started["data"]["thought_key"], "run-reason-1:reason")
        self.assertIn("started_at", node_started["data"])
        self.assertEqual(node_started["data"]["display_mode"], "thinking_only")

        node_completed_events = [item for item in emitted if item["type"] == "node_completed"]
        self.assertGreaterEqual(len(node_completed_events), 2)
        reason_completed = next(item for item in node_completed_events if item["data"].get("node") == "reason")
        self.assertEqual(reason_completed["data"]["run_id"], "run-reason-1")
        self.assertEqual(reason_completed["data"]["thought_key"], "run-reason-1:reason")
        self.assertIn("completed_at", reason_completed["data"])
        self.assertGreaterEqual(int(reason_completed["data"]["thought_duration_sec"]), 1)
        self.assertIn("new_trace_items", reason_completed["data"])

        new_trace_items = reason_completed["data"]["new_trace_items"]
        self.assertEqual(len(new_trace_items), 1)
        self.assertEqual(new_trace_items[0]["event_type"], "tool_call")
        self.assertEqual(new_trace_items[0]["thought_key"], "run-reason-1:reason")
        self.assertEqual(
            new_trace_items[0]["next_action"],
            "Next action: call query_metrics for auth-svc and compare p95 with baseline.",
        )
        self.assertGreaterEqual(int(new_trace_items[0]["thought_duration_sec"]), 1)

        finalize_completed = next(item for item in node_completed_events if item["data"].get("node") == "finalize")
        self.assertNotIn("new_trace_items", finalize_completed["data"])

    async def test_run_diagnosis_stream_keeps_next_action_missing_when_trace_item_does_not_provide_it(self) -> None:
        stream_events = [
            {
                "event": "on_chain_start",
                "name": "reason",
                "run_id": "run-reason-2",
                "data": {},
            },
            {
                "event": "on_chain_end",
                "name": "reason",
                "run_id": "run-reason-2",
                "data": {
                    "output": {
                        "status": "diagnosing",
                        "step_count": 1,
                        "trace_items": [
                            {
                                "type": "thought",
                                "step": 1,
                                "timestamp": "2026-04-13T13:10:00+00:00",
                                "content": "Inspecting error budget burn before concluding.",
                                "action": "conclude",
                            }
                        ],
                    }
                },
            },
        ]

        with patch("sre_agent.agent.graph.create_sre_graph", return_value=_FakeStreamGraph(stream_events)):
            emitted: list[dict[str, Any]] = []
            async for event in run_diagnosis_stream(
                query="Diagnose error budget burn",
                context=_happy_context(),
                variables={},
                checkpoint_dir=None,
                total_timeout_sec=10.0,
            ):
                emitted.append(event)

        reason_completed = next(item for item in emitted if item["type"] == "node_completed")
        new_trace_items = reason_completed["data"]["new_trace_items"]
        self.assertEqual(len(new_trace_items), 1)
        self.assertNotIn("next_action", new_trace_items[0])

    async def test_run_diagnosis_stream_emits_token_from_reasoning_content_when_content_empty(self) -> None:
        stream_events = [
            {
                "event": "on_chain_start",
                "name": "reason",
                "run_id": "run-reason-3",
                "data": {},
            },
            {
                "event": "on_chat_model_stream",
                "name": "ChatOpenAI",
                "run_id": "run-llm-2",
                "metadata": {"langgraph_node": "reason"},
                "data": {"chunk": _FakeStreamChunk(content=None, reasoning_content="thinking-token")},
            },
            {
                "event": "on_chain_end",
                "name": "reason",
                "run_id": "run-reason-3",
                "data": {"output": {"status": "diagnosing", "step_count": 1, "trace_items": []}},
            },
        ]
        with patch("sre_agent.agent.graph.create_sre_graph", return_value=_FakeStreamGraph(stream_events)):
            emitted: list[dict[str, Any]] = []
            async for event in run_diagnosis_stream(
                query="Diagnose reasoning stream",
                context=_happy_context(),
                variables={},
                checkpoint_dir=None,
                total_timeout_sec=10.0,
            ):
                emitted.append(event)

        token_event = next(item for item in emitted if item["type"] == "token_delta")
        self.assertEqual(token_event["data"]["content"], "thinking-token")
        self.assertEqual(token_event["data"]["thought_key"], "run-reason-3:reason")

    async def test_resilient_llm_retries_and_fallbacks_for_retryable_errors(self) -> None:
        env = {
            "SRE_OPENAI_API_KEY": "test-key",
            "SRE_LLM_PROVIDER": "glm",
            "SRE_LLM_MODEL": "glm-5.1",
            "SRE_LLM_FALLBACK_MODELS": "glm-5-turbo",
            "SRE_OPENAI_BASE_URL": "https://open.bigmodel.cn/api/coding/paas/v4",
        }
        with (
            patch.dict("os.environ", env, clear=True),
            patch("sre_agent.agent.graph.ChatOpenAI", _ResilientFakeChatOpenAI),
            patch("sre_agent.agent.graph.asyncio.sleep", new=AsyncMock(return_value=None)),
        ):
            runtime_llm = graph_module.build_default_llm_from_env()
            response = await runtime_llm.ainvoke([])
            self.assertIsInstance(response, AIMessage)
            self.assertIn("glm-5-turbo", str(response.content))

            diagnostics = graph_module.get_last_llm_runtime_diagnostics()
            self.assertEqual(diagnostics["active_model"], "glm-5-turbo")
            self.assertTrue(diagnostics["fallback_used"])
            self.assertGreaterEqual(int(diagnostics["retry_count"]), 1)
            self.assertIn(str(diagnostics["last_error_code"]), {"1305", "None"})

    async def test_reason_timeout_retries_once_with_compact_final_prompt_and_succeeds(self) -> None:
        llm = _TimeoutThenSuccessLLM(
            AIMessage(
                content=json.dumps(
                    {
                        "thought": "Retry completed with compact final evidence.",
                        "diagnosis": {
                            "root_cause": "Transient LLM latency during reason step",
                            "root_cause_layer": "service",
                            "root_cause_entities": ["service:test"],
                            "confidence": 0.63,
                            "impact_summary": "Retry succeeded after first reason timeout.",
                            "affected_services": ["service:test"],
                            "triage_priority": "P2",
                            "diagnosis_certainty": "probable",
                        },
                        "remediation_plan": None,
                    }
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await run_diagnosis(
                query="Diagnose with a temporary reason timeout and retry once.",
                context=_happy_context(),
                variables={},
                llm=llm,
                step_timeout_sec=0.05,
                total_timeout_sec=1.0,
                allowed_tool_names=["prometheus.query_instant"],
                checkpoint_dir=tmpdir,
            )

        self.assertEqual(result["status"], "diagnosed")
        self.assertGreaterEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[-1]["tool_choice"], "none")
        trace_items = [item for item in result.get("trace_items", []) if isinstance(item, dict)]
        self.assertTrue(
            any(
                str((item.get("tool_params") or {}).get("kind", "")) == "reason_timeout_retry"
                for item in trace_items
            )
        )

    async def test_reason_timeout_retry_exhausted_keeps_step_timeout_and_retry_trace(self) -> None:
        llm = _TimeoutAlwaysLLM()
        result = await run_diagnosis(
            query="Diagnose with persistent reason timeout.",
            context=_happy_context(),
            variables={},
            llm=llm,
            step_timeout_sec=0.05,
            total_timeout_sec=1.0,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "step_timeout")
        trace_items = [item for item in result.get("trace_items", []) if isinstance(item, dict)]
        self.assertTrue(
            any(
                str((item.get("tool_params") or {}).get("kind", "")) == "reason_timeout_retry"
                for item in trace_items
            )
        )
        self.assertTrue(
            any(
                str((item.get("tool_params") or {}).get("kind", "")) == "reason_timeout"
                for item in trace_items
            )
        )

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


class TestSelectBoundToolNamesForTurn(unittest.TestCase):
    def setUp(self) -> None:
        self._func = nodes_module._select_bound_tool_names_for_turn

    def test_empty_tool_runs_with_pending_returns_pending_names(self) -> None:
        state = {
            "tool_runs": [],
            "pending_tool_calls": [
                {"name": "process.find", "args": {"pattern": "stress"}},
            ],
        }
        result = self._func(state)
        self.assertEqual(result, ["process.find"])

    def test_empty_tool_runs_with_multiple_pending_deduplicates(self) -> None:
        state = {
            "tool_runs": [],
            "pending_tool_calls": [
                {"name": "process.find", "args": {"pattern": "a"}},
                {"name": "process.find", "args": {"pattern": "b"}},
                {"name": "gpu.get_processes", "args": {"node": "x"}},
            ],
        }
        result = self._func(state)
        self.assertCountEqual(result, ["process.find", "gpu.get_processes"])

    def test_empty_tool_runs_no_pending_falls_back_to_skills(self) -> None:
        state: dict[str, Any] = {"tool_runs": [], "pending_tool_calls": []}
        result = self._func(state)
        self.assertEqual(result, ["skills.list_skills"])

    def test_empty_tool_runs_none_pending_falls_back_to_skills(self) -> None:
        state: dict[str, Any] = {"tool_runs": [], "pending_tool_calls": None}
        result = self._func(state)
        self.assertEqual(result, ["skills.list_skills"])

    def test_allowed_tool_names_takes_priority_over_pending(self) -> None:
        state = {
            "allowed_tool_names": ["prometheus.query_instant"],
            "tool_runs": [],
            "pending_tool_calls": [{"name": "process.find", "args": {}}],
        }
        result = self._func(state)
        self.assertEqual(result, ["prometheus.query_instant"])

    def test_nonempty_tool_runs_no_allowed_returns_none(self) -> None:
        state = {
            "tool_runs": [{"tool": "gpu.get_processes", "success": True}],
        }
        result = self._func(state)
        self.assertIsNone(result)

    def test_nonempty_tool_runs_with_allowed_returns_allowed(self) -> None:
        state = {
            "tool_runs": [{"tool": "gpu.get_processes", "success": True}],
            "allowed_tool_names": ["process.find", "prometheus.query_instant"],
        }
        result = self._func(state)
        self.assertEqual(result, ["process.find", "prometheus.query_instant"])

    def test_empty_allowed_tool_names_treated_as_none(self) -> None:
        state = {
            "tool_runs": [{"tool": "gpu.get_processes", "success": True}],
            "allowed_tool_names": [],
        }
        result = self._func(state)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
