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
from sre_agent.models.remediation import RemediationPlan
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


def _diagnosis_result(
    *,
    title: str,
    layer: str = "service",
    entities: list[str] | None = None,
    confidence: float = 0.8,
    impact_summary: str = "impact observed",
    affected_services: list[str] | None = None,
    triage_priority: str = "P1",
    diagnosis_certainty: str = "probable",
) -> DiagnosisResult:
    return DiagnosisResult.model_validate(
        {
            "root_cause": [
                {
                    "id": "rc-test-1",
                    "title": title,
                    "layer": layer,
                    "entities": entities or [],
                    "confidence": confidence,
                    "certainty": diagnosis_certainty,
                    "status": "confirmed" if diagnosis_certainty == "confirmed" else "suspected",
                    "evidence_summary": title,
                    "impact_summary": impact_summary,
                    "distinguishing_verification": None,
                    "recommended_fix": None,
                }
            ],
            "confidence": confidence,
            "hypotheses": [],
            "propagation_chain": [],
            "impact_summary": impact_summary,
            "affected_services": affected_services or [],
            "recommended_fix": None,
            "triage_priority": triage_priority,
            "diagnosis_certainty": diagnosis_certainty,
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
            self.assertEqual(diagnosis.root_cause[0].layer, "platform")
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
        self.assertIn("Alert catalog reference (from sre_agent/conf/Entity.md):", prompt)
        self.assertIn("GPUTemperatureHigh", prompt)
        self.assertIn(
            "When multiple abnormal factors are observed, prefer separating them into distinct root-cause candidates",
            prompt,
        )

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

    async def test_cross_round_dedup_preserves_explicit_params_over_runtime_defaults(self) -> None:
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
            self.assertEqual(len(nic_runs), 2)
            self.assertEqual(nic_runs[0]["params"].get("namespace"), "nvidia-dcgm")
            self.assertEqual(nic_runs[1]["params"].get("namespace"), "other-ns")

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

    async def test_act_node_skips_loop_guard_count_while_ttft_coverage_incomplete(self) -> None:
        session_id = "ttft-incomplete-loop-guard"
        expected_run = nodes_module._canonicalize_tool_run(
            {
                "step": 1,
                "tool": "network.get_nic_counters",
                "params": {"node": "worker-03"},
                "success": True,
                "data": {
                    "output": "ok",
                    "error": "",
                    "node": "worker-03",
                    "iface": None,
                    "source": "ethtool/sysfs counters",
                },
                "error": "",
            },
            session_id=session_id,
            source="tool",
        )
        recent_fingerprint = nodes_module._build_tool_call_fingerprint(expected_run)
        state: dict[str, Any] = {
            "session_id": session_id,
            "messages": [],
            "tool_runs": [],
            "pending_tool_calls": [
                {
                    "name": "network.get_nic_counters",
                    "args": {"node": "worker-03"},
                    "id": "call-nic",
                }
            ],
            "variables": {},
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
            "trace_items": [],
            "step_count": 0,
            "step_timeout_sec": 5.0,
            "max_steps": 8,
            "force_final_turn": False,
            "loop_guard": {
                "recent_fingerprint": recent_fingerprint,
                "repeat_count": 2,
                "threshold": 2,
                "recent_family_fingerprint": "network.get_nic_counters",
                "family_repeat_count": 2,
                "family_threshold": 2,
                "triggered": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            state["checkpoint_dir"] = tmpdir
            result = await nodes_module.act_node(
                state,
                registry=build_default_registry(),
                context=_happy_context(),
            )

        self.assertEqual(len(result["tool_runs"]), 1)
        self.assertEqual(result["loop_guard"]["repeat_count"], 2)
        self.assertEqual(result["loop_guard"]["family_repeat_count"], 2)
        self.assertFalse(result["loop_guard"]["triggered"])
        self.assertFalse(result["force_final_turn"])

    async def test_act_node_counts_loop_guard_after_ttft_coverage_met(self) -> None:
        state: dict[str, Any] = {
            "session_id": "ttft-complete-loop-guard",
            "messages": [],
            "tool_runs": [
                {
                    "step": 1,
                    "tool": "gpu.get_processes",
                    "params": {"node": "worker-03"},
                    "success": True,
                    "data": {"processes": []},
                    "error": None,
                },
                {
                    "step": 2,
                    "tool": "process.find",
                    "params": {"node": "10.11.4.13", "pattern": "load_simulator"},
                    "success": True,
                    "data": {"node": "10.11.4.13", "count": 0, "matches": []},
                    "error": None,
                },
            ],
            "pending_tool_calls": [
                {
                    "name": "network.get_nic_counters",
                    "args": {"node": "worker-03"},
                    "id": "call-nic",
                }
            ],
            "variables": {},
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
            "trace_items": [],
            "step_count": 0,
            "step_timeout_sec": 5.0,
            "max_steps": 8,
            "force_final_turn": False,
            "loop_guard": {
                "recent_fingerprint": "previous-tool",
                "repeat_count": 2,
                "threshold": 2,
                "recent_family_fingerprint": "network.get_nic_counters",
                "family_repeat_count": 2,
                "family_threshold": 2,
                "triggered": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            state["checkpoint_dir"] = tmpdir
            result = await nodes_module.act_node(
                state,
                registry=build_default_registry(),
                context=_happy_context(),
            )

        self.assertEqual(len(result["tool_runs"]), 3)
        self.assertEqual(result["loop_guard"]["family_repeat_count"], 3)
        self.assertTrue(result["loop_guard"]["triggered"])
        self.assertTrue(result["force_final_turn"])

    async def test_act_node_ttft_first_cross_round_duplicate_does_not_force_final(self) -> None:
        session_id = "ttft-first-duplicate"
        gpu_run = nodes_module._canonicalize_tool_run(
            {
                "step": 2,
                "tool": "gpu.get_processes",
                "params": {"node": "worker-03", "namespace": "service"},
                "success": True,
                "data": {"output": "1234 fi_gpu_burn_gpu_cont 127984MiB", "error": ""},
                "error": "",
            },
            session_id=session_id,
            source="tool",
        )
        state: dict[str, Any] = {
            "session_id": session_id,
            "messages": [],
            "tool_runs": [
                {
                    "step": 1,
                    "tool": "process.find",
                    "params": {"node": "10.11.4.13", "pattern": "load_simulator"},
                    "success": True,
                    "data": {"node": "10.11.4.13", "count": 1, "matches": []},
                    "error": None,
                },
                gpu_run,
            ],
            "pending_tool_calls": [
                {
                    "name": "gpu.get_processes",
                    "args": {"node": "worker-03", "namespace": "service"},
                    "id": "call-gpu-duplicate",
                }
            ],
            "variables": {"namespace": "service"},
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
            "trace_items": [],
            "step_count": 3,
            "step_timeout_sec": 5.0,
            "max_steps": 8,
            "force_final_turn": False,
            "loop_guard": {"threshold": 2},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            state["checkpoint_dir"] = tmpdir
            result = await nodes_module.act_node(
                state,
                registry=build_default_registry(),
                context=_happy_context(),
            )

        self.assertEqual(len(result["tool_runs"]), 2)
        self.assertFalse(result["force_final_turn"])
        self.assertEqual(result["loop_guard"]["duplicate_repeat_count"], 1)
        duplicate_trace = next(
            item
            for item in result["trace_items"]
            if str((item.get("tool_params") or {}).get("reason", "")) == "cross_round_duplicate"
        )
        self.assertEqual(duplicate_trace["action"], "observe")
        self.assertFalse((duplicate_trace["tool_params"] or {}).get("force_final_turn"))

    async def test_act_node_ttft_repeated_cross_round_duplicate_forces_final_after_threshold(self) -> None:
        session_id = "ttft-repeated-duplicate"
        gpu_run = nodes_module._canonicalize_tool_run(
            {
                "step": 2,
                "tool": "gpu.get_processes",
                "params": {"node": "worker-03", "namespace": "service"},
                "success": True,
                "data": {"output": "1234 fi_gpu_burn_gpu_cont 127984MiB", "error": ""},
                "error": "",
            },
            session_id=session_id,
            source="tool",
        )
        duplicate_fingerprint = nodes_module._build_tool_call_fingerprint(gpu_run)
        state: dict[str, Any] = {
            "session_id": session_id,
            "messages": [],
            "tool_runs": [
                {
                    "step": 1,
                    "tool": "process.find",
                    "params": {"node": "10.11.4.13", "pattern": "load_simulator"},
                    "success": True,
                    "data": {"node": "10.11.4.13", "count": 1, "matches": []},
                    "error": None,
                },
                gpu_run,
            ],
            "pending_tool_calls": [
                {
                    "name": "gpu.get_processes",
                    "args": {"node": "worker-03", "namespace": "service"},
                    "id": "call-gpu-duplicate",
                }
            ],
            "variables": {"namespace": "service"},
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
            "trace_items": [],
            "step_count": 4,
            "step_timeout_sec": 5.0,
            "max_steps": 8,
            "force_final_turn": False,
            "loop_guard": {
                "threshold": 2,
                "recent_duplicate_fingerprint": duplicate_fingerprint,
                "duplicate_repeat_count": 2,
                "duplicate_threshold": 2,
                "triggered": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            state["checkpoint_dir"] = tmpdir
            result = await nodes_module.act_node(
                state,
                registry=build_default_registry(),
                context=_happy_context(),
            )

        self.assertEqual(len(result["tool_runs"]), 2)
        self.assertEqual(result["loop_guard"]["duplicate_repeat_count"], 3)
        self.assertTrue(result["loop_guard"]["triggered"])
        self.assertTrue(result["force_final_turn"])
        duplicate_trace = next(
            item
            for item in result["trace_items"]
            if str((item.get("tool_params") or {}).get("reason", "")) == "cross_round_duplicate"
        )
        self.assertEqual(duplicate_trace["action"], "conclude")
        self.assertTrue((duplicate_trace["tool_params"] or {}).get("force_final_turn"))

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
        diagnosis = _diagnosis_result(
            title="异常负载导致 TTFT 抬高",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.71,
            impact_summary="同节点多进程压测争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
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
        self.assertEqual(step_one["verification"]["tool"], "process.find")
        self.assertEqual(step_two["verification"]["tool"], "process.find")
        self.assertEqual(step_one["verification"]["tool_params"]["pattern"], "11001")
        self.assertEqual(step_two["verification"]["tool_params"]["pattern"], "11002")
        self.assertEqual(step_one["verification"]["condition"]["field"], "count")
        self.assertEqual(step_one["verification"]["condition"]["operator"], "==")
        self.assertEqual(step_one["verification"]["condition"]["value"], 0)
        self.assertEqual(plan["canary"]["target_percentage"], 0.5)
        self.assertEqual(plan["canary"]["max_batches"], 2)
        self.assertFalse(plan["canary"]["progressive"])

    def test_ttft_auto_kill_process_plan_uses_process_node_when_differs_from_serving_node(self) -> None:
        diagnosis = _diagnosis_result(
            title="外部压测源导致 TTFT 抬高",
            layer="service",
            entities=["node:10.11.4.12"],
            confidence=0.72,
            impact_summary="服务节点与压测源节点不一致",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
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
        diagnosis = _diagnosis_result(
            title="异常负载导致 TTFT 抬高",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.71,
            impact_summary="同节点多进程压测争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
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
        diagnosis = _diagnosis_result(
            title="GPU contention drives TTFT spike",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.81,
            impact_summary="GPU burn and synthetic traffic overlap caused TTFT regression",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
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
        self.assertEqual(plan["steps"][0]["verification"]["tool_params"]["pattern"], "10002")
        self.assertEqual(plan["steps"][1]["verification"]["tool_params"]["pattern"], "10003")
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
        self.assertEqual(suspects[0]["source_tool"], "process.find")
        self.assertEqual(suspects[0]["process_family_hint"], "external_load")
        self.assertEqual(suspects[0]["node_role_hint"], "external_load_node")
        self.assertEqual(signals["ttft_evidence_cards"][0]["process_family_hint"], "external_load")

    def test_extract_evidence_signals_allows_process_find_gpu_contention_on_serving_node(self) -> None:
        tool_runs = [
            {
                "tool": "process.find",
                "success": True,
                "step": 7,
                "params": {"pattern": "fi_gpu_burn", "node": "worker-03"},
                "key_fields": {
                    "match_count": 1,
                    "node": "worker-03",
                    "suspicious_load_present": True,
                    "suspicious_load_processes": [
                        {
                            "pid": 2167682,
                            "process_name": "fi_gpu_burn_gpu_contention_b084ded0",
                            "memory_mib": None,
                            "node": "worker-03",
                        }
                    ],
                },
                "data": {},
            }
        ]

        signals = nodes_module._extract_evidence_signals(tool_runs)

        self.assertTrue(signals["ttft_suspect_process_present"])
        suspect = signals["ttft_suspect_processes"][0]
        self.assertEqual(suspect["process_family_hint"], "gpu_contention")
        self.assertEqual(suspect["node_role_hint"], "serving_gpu_node")
        self.assertEqual(suspect["evidence_id"], "tool:7:process.find")
        self.assertEqual(signals["ttft_evidence_cards"][0]["process_family_hint"], "gpu_contention")

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

    def test_validate_ttft_root_cause_consistency_flags_external_title_with_gpu_only_refs(self) -> None:
        payload = nodes_module._normalize_diagnosis_payload(
            {
                "root_cause": [
                    {
                        "id": "rc-1",
                        "title": "外部负载生成器加剧GPU资源紧张",
                        "layer": "service",
                        "entities": ["10.11.4.13", "python load generator"],
                        "confidence": 0.7,
                        "certainty": "probable",
                        "status": "contributing",
                        "factor_type": "external_load",
                        "evidence_refs": ["tool:1:gpu.get_processes"],
                        "evidence_summary": "gpu.get_processes发现fi_gpu_burn_gpu_contention_b084ded0进程",
                        "impact_summary": "TTFT elevated",
                    }
                ],
                "confidence": 0.9,
                "hypotheses": [],
                "impact_summary": "TTFT elevated",
                "affected_services": [],
                "triage_priority": "P0",
                "diagnosis_certainty": "confirmed",
            }
        )
        evidence_cards = [
            {
                "evidence_id": "tool:1:gpu.get_processes",
                "source_tool": "gpu.get_processes",
                "source_node": "worker-03",
                "node_role_hint": "serving_gpu_node",
                "process_family_hint": "gpu_contention",
                "evidence_role_hint": "root_cause_evidence",
                "summary": "worker-03 gpu.get_processes found fi_gpu_burn",
                "processes": [],
            }
        ]

        findings = nodes_module._validate_ttft_root_cause_consistency(payload, evidence_cards)
        fallback = nodes_module._apply_ttft_consistency_fallback(payload, findings)

        self.assertEqual(findings[0]["type"], "evidence_mismatch")
        self.assertEqual(fallback["root_cause"][0]["certainty"], "ambiguous")
        self.assertEqual(fallback["root_cause"][0]["status"], "suspected")
        self.assertIsNone(fallback["root_cause"][0]["recommended_fix"])

    def test_validate_ttft_root_cause_consistency_allows_mixed_with_both_refs(self) -> None:
        payload = nodes_module._normalize_diagnosis_payload(
            {
                "root_cause": [
                    {
                        "id": "rc-1",
                        "title": "外部负载加剧GPU争用",
                        "layer": "service",
                        "entities": ["worker-03", "10.11.4.13"],
                        "confidence": 0.8,
                        "certainty": "probable",
                        "status": "contributing",
                        "factor_type": "mixed",
                        "evidence_refs": ["tool:1:gpu.get_processes", "tool:2:process.find"],
                        "evidence_summary": "gpu.get_processes发现GPU burn；process.find发现load_simulator",
                        "impact_summary": "TTFT elevated",
                    }
                ],
                "confidence": 0.9,
                "hypotheses": [],
                "impact_summary": "TTFT elevated",
                "affected_services": [],
                "triage_priority": "P0",
                "diagnosis_certainty": "confirmed",
            }
        )
        evidence_cards = [
            {"evidence_id": "tool:1:gpu.get_processes", "process_family_hint": "gpu_contention"},
            {"evidence_id": "tool:2:process.find", "process_family_hint": "external_load"},
        ]

        findings = nodes_module._validate_ttft_root_cause_consistency(payload, evidence_cards)

        self.assertEqual(findings, [])

    def test_dedupe_ttft_root_causes_merges_same_external_evidence(self) -> None:
        payload = nodes_module._normalize_diagnosis_payload(
            {
                "root_cause": [
                    {
                        "id": "rc-1",
                        "title": "External load_simulator process causes high request pressure",
                        "layer": "service",
                        "entities": ["10.11.4.13", "load_simulator"],
                        "confidence": 0.9,
                        "certainty": "confirmed",
                        "status": "contributing",
                        "factor_type": "external_load",
                        "evidence_refs": ["tool:2:process.find"],
                        "evidence_summary": "tool:2:process.find found load_simulator on 10.11.4.13",
                        "impact_summary": "TTFT elevated",
                    },
                    {
                        "id": "rc-2",
                        "title": "load_simulator generates high concurrency inference requests",
                        "layer": "service",
                        "entities": ["10.11.4.13"],
                        "confidence": 0.9,
                        "certainty": "confirmed",
                        "status": "contributing",
                        "evidence_summary": "tool:2:process.find found load_simulator on 10.11.4.13",
                        "impact_summary": "TTFT elevated",
                        "recommended_fix": {
                            "plan_id": "proposal-load",
                            "root_cause": "load_simulator generates high concurrency inference requests",
                            "description": "stop load simulator",
                            "steps": [
                                {
                                    "step_id": 1,
                                    "description": "stop load simulator",
                                    "tool": "kill_process",
                                    "params": {"node": "10.11.4.13", "pid": 4077465},
                                    "verification": {"method": "wait", "wait_seconds": 30},
                                }
                            ],
                            "estimated_impact": "reduce request pressure",
                            "confidence": 0.9,
                            "priority": "P0",
                            "safety_level": "medium",
                        },
                    },
                ],
                "confidence": 0.9,
                "hypotheses": [],
                "impact_summary": "TTFT elevated",
                "affected_services": [],
                "triage_priority": "P0",
                "diagnosis_certainty": "confirmed",
            }
        )

        nodes_module._dedupe_ttft_root_causes(payload)

        self.assertEqual(len(payload["root_cause"]), 1)
        self.assertEqual(payload["root_cause"][0]["factor_type"], "external_load")
        self.assertEqual(payload["root_cause"][0]["evidence_refs"], ["tool:2:process.find"])
        self.assertIsNotNone(payload["root_cause"][0]["recommended_fix"])

    def test_normalize_ttft_root_cause_certainty_raises_high_confidence_ambiguous(self) -> None:
        payload = {
            "root_cause": [
                {
                    "title": "External load_simulator process causes high request pressure",
                    "confidence": 0.9,
                    "certainty": "ambiguous",
                    "status": "contributing",
                    "evidence_summary": "tool:2:process.find found load_simulator",
                    "impact_summary": "TTFT elevated",
                }
            ]
        }

        nodes_module._normalize_ttft_root_cause_certainty_from_confidence(payload)

        self.assertEqual(payload["root_cause"][0]["certainty"], "confirmed")

    def test_augment_ttft_hardware_hypotheses_adds_temperature_and_fan_eliminations(self) -> None:
        payload = {"hypotheses": []}
        evidence_signals = {
            "gpu_temperature_steps": [1],
            "gpu_max_temp": 68,
            "gpu_thermal_normal": True,
            "bmc_fan_status_steps": [2],
            "bmc_fan_mode": "Auto",
            "bmc_fan_is_manual": False,
            "bmc_fan_fixed_pwm": False,
            "bmc_fan_count": 8,
        }

        nodes_module._augment_ttft_hardware_hypotheses_from_evidence(payload, evidence_signals)

        descriptions = [item["description"] for item in payload["hypotheses"]]
        self.assertIn("GPU thermal throttling contributes to TTFT latency", descriptions)
        self.assertIn("Fan control policy limits GPU cooling and worsens latency", descriptions)
        self.assertTrue(all(item["status"] == "eliminated" for item in payload["hypotheses"]))

    def test_attach_per_root_cause_recommended_fixes_builds_independent_ttft_plans(self) -> None:
        diagnosis = DiagnosisResult.model_validate(
            {
                "root_cause": [
                    {
                        "id": "rc-1",
                        "title": "GPU contention caused by fi_gpu_burn process",
                        "layer": "service",
                        "entities": ["worker-03", "fi_gpu_burn_gpu_contention_4d080fa0"],
                        "confidence": 0.95,
                        "certainty": "confirmed",
                        "status": "confirmed",
                        "evidence_summary": "gpu.get_processes found fi_gpu_burn process on worker-03",
                        "factor_type": "gpu_contention",
                        "evidence_refs": ["tool:1:gpu.get_processes"],
                        "impact_summary": "GPU occupied and TTFT increased",
                        "distinguishing_verification": "terminate fi_gpu_burn and observe TTFT",
                        "recommended_fix": None,
                    },
                    {
                        "id": "rc-2",
                        "title": "External load_simulator continuously sends high request volume",
                        "layer": "service",
                        "entities": ["10.11.4.13", "load_simulator"],
                        "confidence": 0.85,
                        "certainty": "confirmed",
                        "status": "contributing",
                        "evidence_summary": "process.find on 10.11.4.13 found load_simulator processes",
                        "factor_type": "external_load",
                        "evidence_refs": ["tool:2:process.find"],
                        "impact_summary": "queueing pressure increased",
                        "distinguishing_verification": "stop load_simulator and observe TTFT",
                        "recommended_fix": None,
                    },
                ],
                "confidence": 0.95,
                "next_action": "proposal only",
                "hypotheses": [],
                "propagation_chain": [],
                "impact_summary": "TTFT P99 elevated",
                "affected_services": ["qwen3-32b-fp8-202602261"],
                "recommended_fix": None,
                "triage_priority": "P0",
                "diagnosis_certainty": "confirmed",
            }
        )
        evidence_signals = {
            "ttft_suspect_process_present": True,
            "ttft_suspect_processes": [
                {
                    "pid": 1155497,
                    "process_name": "fi_gpu_burn_gpu_contention_4d080fa0",
                    "memory_mib": 9272,
                    "node": "worker-03",
                    "source_tool": "gpu.get_processes",
                    "evidence_id": "tool:1:gpu.get_processes",
                    "process_family_hint": "gpu_contention",
                },
                {
                    "pid": 1392199,
                    "process_name": "python -m load_simulator run --config inference-qwen3-32b-fp8.yaml --only inference",
                    "memory_mib": None,
                    "node": "10.11.4.13",
                    "source_tool": "process.find",
                    "evidence_id": "tool:2:process.find",
                    "process_family_hint": "external_load",
                },
            ],
        }

        updated_diagnosis, primary_plan = nodes_module._attach_per_root_cause_recommended_fixes(
            diagnosis=diagnosis,
            primary_plan=None,
            evidence_signals=evidence_signals,
            session_id="sess-multi-root-plan",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="AIServiceTTFTP99High",
        )

        self.assertIsNotNone(primary_plan)
        assert primary_plan is not None
        root_causes = updated_diagnosis.root_cause
        self.assertEqual(len(root_causes), 2)
        self.assertIsNotNone(root_causes[0].recommended_fix)
        self.assertIsNotNone(root_causes[1].recommended_fix)
        assert root_causes[0].recommended_fix is not None
        assert root_causes[1].recommended_fix is not None
        self.assertEqual(root_causes[0].recommended_fix.plan_id, primary_plan.plan_id)

        first_step = root_causes[0].recommended_fix.steps[0]
        second_step = root_causes[1].recommended_fix.steps[0]
        self.assertEqual(first_step.tool, "kill_process")
        self.assertEqual(second_step.tool, "kill_process")
        self.assertEqual(first_step.params.get("node"), "worker-03")
        self.assertIn(str(second_step.params.get("node")), {"10.11.4.13", "worker-04"})
        self.assertEqual(first_step.params.get("pid"), 1155497)
        self.assertEqual(second_step.params.get("pid"), 1392199)
        self.assertEqual(len(root_causes[0].recommended_fix.steps), 1)
        self.assertEqual(len(root_causes[1].recommended_fix.steps), 1)

    def test_attach_per_root_cause_recommended_fixes_sanitizes_existing_secondary_plans(self) -> None:
        mixed_secondary_plan = RemediationPlan.model_validate(
            {
                "plan_id": "proposal-external-load-001",
                "root_cause": "External load",
                "description": "stop load and verify TTFT",
                "steps": [
                    {
                        "step_id": 1,
                        "description": "terminate load simulator",
                        "tool": "kill_process",
                        "params": {"node": "10.11.4.13", "pid": 4076302, "entity_id": "proc:4076302", "signal": "TERM"},
                        "verification": {"method": "wait", "wait_seconds": 30},
                        "timeout": 60,
                    },
                    {
                        "step_id": 2,
                        "description": "verify TTFT with Prometheus",
                        "tool": "prometheus.query_instant",
                        "params": {"promql": "histogram_quantile(0.99, rate(vllm_bucket[5m]))"},
                        "verification": {"method": "wait", "wait_seconds": 30},
                        "timeout": 60,
                    },
                ],
                "estimated_impact": "reduce queueing",
                "confidence": 0.9,
                "priority": "P0",
                "safety_level": "high",
            }
        )
        read_only_plan = RemediationPlan.model_validate(
            {
                "plan_id": "proposal-read-only",
                "root_cause": "Metric-only followup",
                "description": "verify only",
                "steps": [
                    {
                        "step_id": 1,
                        "description": "check TTFT only",
                        "tool": "prometheus.query_instant",
                        "params": {"promql": "up"},
                        "verification": {"method": "wait", "wait_seconds": 30},
                        "timeout": 60,
                    }
                ],
                "estimated_impact": "none",
                "confidence": 0.5,
                "priority": "P2",
                "safety_level": "high",
            }
        )
        diagnosis = DiagnosisResult.model_validate(
            {
                "root_cause": [
                    {
                        "id": "rc-1",
                        "title": "GPU contention",
                        "layer": "service",
                        "entities": ["worker-03"],
                        "confidence": 0.95,
                        "certainty": "confirmed",
                        "status": "confirmed",
                        "evidence_summary": "gpu busy",
                        "impact_summary": "TTFT high",
                        "recommended_fix": None,
                    },
                    {
                        "id": "rc-2",
                        "title": "External load",
                        "layer": "service",
                        "entities": ["10.11.4.13"],
                        "confidence": 0.9,
                        "certainty": "confirmed",
                        "status": "confirmed",
                        "evidence_summary": "load_simulator present",
                        "impact_summary": "TTFT high",
                        "recommended_fix": mixed_secondary_plan.model_dump(mode="json"),
                    },
                    {
                        "id": "rc-3",
                        "title": "Metric-only followup",
                        "layer": "service",
                        "entities": ["qwen3"],
                        "confidence": 0.6,
                        "certainty": "probable",
                        "status": "suspected",
                        "evidence_summary": "metric check only",
                        "impact_summary": "TTFT high",
                        "recommended_fix": read_only_plan.model_dump(mode="json"),
                    },
                ],
                "confidence": 0.95,
                "next_action": "proposal only",
                "hypotheses": [],
                "propagation_chain": [],
                "impact_summary": "TTFT P99 elevated",
                "affected_services": ["qwen3-32b-fp8-202602261"],
                "recommended_fix": None,
                "triage_priority": "P0",
                "diagnosis_certainty": "confirmed",
            }
        )

        updated_diagnosis, primary_plan = nodes_module._attach_per_root_cause_recommended_fixes(
            diagnosis=diagnosis,
            primary_plan=None,
            evidence_signals={},
            session_id="sess-secondary-plan-sanitize",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="AIServiceTTFTP99High",
        )

        self.assertIsNone(primary_plan)
        sanitized_secondary = updated_diagnosis.root_cause[1].recommended_fix
        self.assertIsNotNone(sanitized_secondary)
        assert sanitized_secondary is not None
        self.assertEqual([step.tool for step in sanitized_secondary.steps], ["kill_process"])
        self.assertEqual(sanitized_secondary.steps[0].step_id, 1)
        self.assertIsNone(updated_diagnosis.root_cause[2].recommended_fix)

    def test_attach_per_root_cause_recommended_fixes_replaces_mismatched_primary_plan(self) -> None:
        diagnosis = DiagnosisResult.model_validate(
            {
                "root_cause": [
                    {
                        "id": "rc-1",
                        "title": "GPU contention caused by fi_gpu_burn process",
                        "layer": "service",
                        "entities": ["worker-03", "fi_gpu_burn_gpu_contention_4d080fa0"],
                        "confidence": 0.95,
                        "certainty": "confirmed",
                        "status": "confirmed",
                        "evidence_summary": "gpu.get_processes found fi_gpu_burn process on worker-03",
                        "impact_summary": "GPU occupied and TTFT increased",
                        "distinguishing_verification": "terminate fi_gpu_burn and observe TTFT",
                        "recommended_fix": None,
                    },
                    {
                        "id": "rc-2",
                        "title": "External load_simulator continuously sends high request volume",
                        "layer": "service",
                        "entities": ["10.11.4.13", "load_simulator"],
                        "confidence": 0.85,
                        "certainty": "confirmed",
                        "status": "contributing",
                        "evidence_summary": "process.find on 10.11.4.13 found load_simulator processes",
                        "impact_summary": "queueing pressure increased",
                        "distinguishing_verification": "stop load_simulator and observe TTFT",
                        "recommended_fix": None,
                    },
                ],
                "confidence": 0.95,
                "next_action": "proposal only",
                "hypotheses": [],
                "propagation_chain": [],
                "impact_summary": "TTFT P99 elevated",
                "affected_services": ["qwen3-32b-fp8-202602261"],
                "recommended_fix": None,
                "triage_priority": "P0",
                "diagnosis_certainty": "confirmed",
            }
        )
        stale_primary_plan = RemediationPlan.model_validate(
            {
                "plan_id": "proposal-stale-primary",
                "root_cause": "GPU contention caused by fi_gpu_burn process",
                "description": "stale plan targets external load only",
                "steps": [
                    {
                        "step_id": 1,
                        "description": "terminate load process",
                        "tool": "kill_process",
                        "params": {"node": "10.11.4.13", "pid": 1392199, "entity_id": "proc:1392199", "signal": "TERM"},
                        "verification": {"method": "wait", "wait_seconds": 30},
                        "timeout": 60,
                    }
                ],
                "estimated_impact": "reduced queueing",
                "confidence": 0.8,
                "priority": "P0",
                "safety_level": "high",
            }
        )
        evidence_signals = {
            "ttft_suspect_process_present": True,
            "ttft_suspect_processes": [
                {
                    "pid": 1155497,
                    "process_name": "fi_gpu_burn_gpu_contention_4d080fa0",
                    "memory_mib": 9272,
                    "node": "worker-03",
                },
                {
                    "pid": 1392199,
                    "process_name": "python -m load_simulator run --only inference",
                    "memory_mib": None,
                    "node": "10.11.4.13",
                },
            ],
        }

        updated_diagnosis, primary_plan = nodes_module._attach_per_root_cause_recommended_fixes(
            diagnosis=diagnosis,
            primary_plan=stale_primary_plan,
            evidence_signals=evidence_signals,
            session_id="sess-multi-root-plan-replace",
            registry=build_default_registry(),
            tool_runs=[],
            variables={},
            alert_name="AIServiceTTFTP99High",
        )

        self.assertIsNotNone(primary_plan)
        assert primary_plan is not None
        self.assertNotEqual(primary_plan.plan_id, "proposal-stale-primary")
        self.assertEqual(primary_plan.steps[0].params.get("pid"), 1155497)
        self.assertEqual(updated_diagnosis.root_cause[0].recommended_fix.steps[0].params.get("pid"), 1155497)

    def test_normalize_diagnosis_payload_repairs_zero_canary_target_percentage(self) -> None:
        def _plan(plan_id: str, root_title: str, pid: int, node: str) -> dict[str, Any]:
            return {
                "plan_id": plan_id,
                "root_cause": root_title,
                "description": f"terminate suspect process {pid}",
                "steps": [
                    {
                        "step_id": 1,
                        "description": f"kill pid {pid} on {node}",
                        "tool": "kill_process",
                        "params": {"node": node, "pid": pid, "entity_id": f"proc:{pid}", "signal": "TERM"},
                        "verification": {"method": "wait", "wait_seconds": 30},
                        "timeout": 60,
                    }
                ],
                "canary": {
                    "enabled": True,
                    "target_percentage": 0,
                    "monitor_duration": 60,
                    "success_criteria": [],
                    "criteria_mode": "all",
                    "max_batches": 0,
                    "auto_rollback_on_regression": True,
                    "progressive": False,
                },
                "estimated_impact": "release abnormal contention",
                "confidence": 0.82,
                "priority": "P0",
                "safety_level": "high",
            }

        root1_plan = _plan("proposal-root-1", "GPU contention by gpuburn", 1172212, "worker-03")
        root2_plan = _plan("proposal-root-2", "External load_simulator pressure", 1583596, "10.11.4.13")
        payload = {
            "root_cause": [
                {
                    "id": "rc-1",
                    "title": "GPU contention by gpuburn",
                    "layer": "service",
                    "entities": ["worker-03", "fi_gpu_burn_gpu_contention_fcd3e47b"],
                    "confidence": 0.91,
                    "certainty": "confirmed",
                    "status": "confirmed",
                    "evidence_summary": "gpu.get_processes found fi_gpu_burn process",
                    "impact_summary": "TTFT elevated due to GPU contention",
                    "distinguishing_verification": "stop gpuburn and observe TTFT",
                    "recommended_fix": root1_plan,
                },
                {
                    "id": "rc-2",
                    "title": "External load_simulator pressure",
                    "layer": "service",
                    "entities": ["10.11.4.13", "load_simulator"],
                    "confidence": 0.87,
                    "certainty": "confirmed",
                    "status": "contributing",
                    "evidence_summary": "process.find found python -m load_simulator",
                    "impact_summary": "queue pressure remains high",
                    "distinguishing_verification": "stop load_simulator and observe TTFT",
                    "recommended_fix": root2_plan,
                },
            ],
            "confidence": 0.92,
            "next_action": "proposal-only remediation",
            "hypotheses": [],
            "propagation_chain": [],
            "impact_summary": "TTFT p99 over threshold",
            "affected_services": ["qwen3-32b-fp8-202602261"],
            "recommended_fix": root1_plan,
            "triage_priority": "P0",
            "diagnosis_certainty": "confirmed",
        }

        normalized = nodes_module._normalize_diagnosis_payload(payload)
        diagnosis = DiagnosisResult.model_validate(normalized)

        primary_plan = diagnosis.root_cause[0].recommended_fix
        self.assertIsNotNone(primary_plan)
        assert primary_plan is not None
        assert primary_plan.canary is not None
        self.assertGreater(primary_plan.canary.target_percentage, 0.0)
        self.assertGreaterEqual(primary_plan.canary.max_batches, 1)

        secondary_plan = diagnosis.root_cause[1].recommended_fix
        self.assertIsNotNone(secondary_plan)
        assert secondary_plan is not None
        assert secondary_plan.canary is not None
        self.assertGreater(secondary_plan.canary.target_percentage, 0.0)
        self.assertGreaterEqual(secondary_plan.canary.max_batches, 1)

    def test_normalize_diagnosis_payload_drops_invalid_inline_recommended_fix(self) -> None:
        payload = {
            "root_cause": [
                {
                    "id": "rc-1",
                    "title": "GPU contention by gpuburn",
                    "layer": "service",
                    "entities": ["worker-03"],
                    "confidence": 0.9,
                    "certainty": "probable",
                    "status": "suspected",
                    "evidence_summary": "gpu util remains high",
                    "impact_summary": "TTFT elevated",
                    "distinguishing_verification": "inspect GPU processes",
                    "recommended_fix": {
                        "plan_id": "broken-inline-plan",
                        "root_cause": "GPU contention by gpuburn",
                        "description": "invalid because steps are missing",
                        "steps": [],
                        "canary": {"enabled": True, "target_percentage": 0},
                        "estimated_impact": "unknown",
                        "confidence": 0.7,
                        "priority": "P1",
                        "safety_level": "high",
                    },
                }
            ],
            "confidence": 0.9,
            "next_action": "continue diagnosis",
            "hypotheses": [],
            "propagation_chain": [],
            "impact_summary": "TTFT elevated",
            "affected_services": ["qwen3-32b-fp8-202602261"],
            "recommended_fix": None,
            "triage_priority": "P1",
            "diagnosis_certainty": "probable",
        }

        normalized = nodes_module._normalize_diagnosis_payload(payload)
        diagnosis = DiagnosisResult.model_validate(normalized)
        self.assertIsNone(diagnosis.root_cause[0].recommended_fix)

    def test_enrich_ttft_root_causes_from_evidence_replaces_generic_fallback(self) -> None:
        payload: dict[str, Any] = {
            "root_cause": [
                {
                    "id": "rc-fallback-non-json",
                    "title": "基于现有证据链，我已经收集到关键证据",
                    "layer": "platform",
                    "entities": [],
                    "confidence": 0.35,
                    "certainty": "ambiguous",
                    "status": "suspected",
                    "evidence_summary": "fallback summary",
                    "impact_summary": "fallback impact",
                    "distinguishing_verification": None,
                    "recommended_fix": {
                        "plan_id": "proposal-load-only",
                        "root_cause": "external load process",
                        "description": "proposal-only kill load",
                        "steps": [
                            {
                                "step_id": 1,
                                "description": "kill load simulator",
                                "tool": "kill_process",
                                "params": {"node": "10.11.4.13", "pid": 1696455, "entity_id": "proc:1696455"},
                                "verification": {"method": "wait", "wait_seconds": 30},
                                "timeout": 60,
                            }
                        ],
                        "estimated_impact": "reduce queue pressure",
                        "confidence": 0.7,
                        "priority": "P1",
                        "safety_level": "high",
                    },
                }
            ],
            "confidence": 0.35,
            "recommended_fix": {
                "plan_id": "proposal-load-only",
                "root_cause": "external load process",
                "description": "proposal-only kill load",
                "steps": [
                    {
                        "step_id": 1,
                        "description": "kill load simulator",
                        "tool": "kill_process",
                        "params": {"node": "10.11.4.13", "pid": 1696455, "entity_id": "proc:1696455"},
                        "verification": {"method": "wait", "wait_seconds": 30},
                        "timeout": 60,
                    }
                ],
                "estimated_impact": "reduce queue pressure",
                "confidence": 0.7,
                "priority": "P1",
                "safety_level": "high",
            },
        }
        evidence_signals = {
            "ttft_suspect_processes": [
                {
                    "pid": 1194134,
                    "process_name": "fi_gpu_burn_gpu_contention_4ee2a720",
                    "node": "worker-03",
                },
                {
                    "pid": 1696455,
                    "process_name": "python -m load_simulator run --only inference",
                    "node": "10.11.4.13",
                },
            ]
        }

        nodes_module._enrich_ttft_root_causes_from_evidence(payload, evidence_signals)

        root_causes = payload.get("root_cause")
        self.assertIsInstance(root_causes, list)
        assert isinstance(root_causes, list)
        self.assertEqual(len(root_causes), 2)
        titles = [str(item.get("title", "")).lower() for item in root_causes if isinstance(item, dict)]
        self.assertTrue(any("gpu" in title for title in titles))
        self.assertTrue(any("load_simulator" in title for title in titles))
        self.assertIsInstance(payload.get("recommended_fix"), dict)
        self.assertEqual(
            str(payload.get("recommended_fix", {}).get("plan_id", "")),
            str(root_causes[0].get("recommended_fix", {}).get("plan_id", "")),
        )

    def test_enrich_ttft_root_causes_from_evidence_appends_missing_factor(self) -> None:
        payload: dict[str, Any] = {
            "root_cause": [
                {
                    "id": "rc-1",
                    "title": "GPU contention caused by fi_gpu_burn process",
                    "layer": "service",
                    "entities": ["worker-03", "fi_gpu_burn_gpu_contention_4ee2a720"],
                    "confidence": 0.92,
                    "certainty": "confirmed",
                    "status": "confirmed",
                    "evidence_summary": "gpu.get_processes found fi_gpu_burn process",
                    "impact_summary": "TTFT elevated",
                    "distinguishing_verification": "terminate gpu burn process",
                    "recommended_fix": None,
                }
            ],
            "confidence": 0.92,
            "recommended_fix": None,
        }
        evidence_signals = {
            "ttft_suspect_processes": [
                {
                    "pid": 1194134,
                    "process_name": "fi_gpu_burn_gpu_contention_4ee2a720",
                    "node": "worker-03",
                },
                {
                    "pid": 1696455,
                    "process_name": "python -m load_simulator run --only inference",
                    "node": "10.11.4.13",
                },
            ]
        }

        nodes_module._enrich_ttft_root_causes_from_evidence(payload, evidence_signals)

        root_causes = payload.get("root_cause")
        self.assertIsInstance(root_causes, list)
        assert isinstance(root_causes, list)
        self.assertEqual(len(root_causes), 2)
        self.assertIn("fi_gpu_burn", str(root_causes[0].get("title", "")).lower())
        self.assertIn("load_simulator", str(root_causes[1].get("title", "")).lower())

    def test_summarize_process_find_detects_suspect_beyond_first_twelve_matches(self) -> None:
        matches: list[dict[str, Any]] = []
        for idx in range(1, 13):
            matches.append(
                {
                    "pid": 7000 + idx,
                    "process": "containerd-shim",
                    "command": f"/usr/local/bin/containerd-shim-runc-v2 --idx={idx}",
                }
            )
        matches.append(
            {
                "pid": 1124321,
                "process": "python",
                "command": "python -m load_simulator run --only inference --output-format json",
            }
        )

        _, fields = nodes_module._summarize_process_find(
            {
                "node": "10.11.4.13",
                "count": len(matches),
                "matches": matches,
            }
        )

        self.assertIsInstance(fields, dict)
        assert isinstance(fields, dict)
        self.assertTrue(bool(fields.get("suspicious_load_present")))
        suspicious = fields.get("suspicious_load_processes")
        self.assertIsInstance(suspicious, list)
        assert isinstance(suspicious, list)
        self.assertTrue(any(int(item.get("pid") or 0) == 1124321 for item in suspicious if isinstance(item, dict)))

    def test_merge_tool_args_normalizes_ttft_process_find_bare_load_pattern(self) -> None:
        registry = build_default_registry()
        merged = nodes_module._merge_tool_args(
            registry=registry,
            tool_name="process.find",
            tool_args={"pattern": "stress|benchmark|load|wrk|ab|locust|jmeter"},
            variables={"alert_name": "AIServiceTTFTP99High", "namespace": "service"},
        )

        pattern = str(merged.get("pattern", ""))
        self.assertIn("load_simulator", pattern)
        self.assertNotIn("|load|", f"|{pattern}|")

    def test_merge_tool_args_keeps_non_ttft_process_find_bare_load_pattern(self) -> None:
        registry = build_default_registry()
        merged = nodes_module._merge_tool_args(
            registry=registry,
            tool_name="process.find",
            tool_args={"pattern": "stress|load|wrk"},
            variables={"alert_name": "GPUUtilizationHigh", "namespace": "service"},
        )

        self.assertEqual(str(merged.get("pattern", "")), "stress|load|wrk")

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

    def test_render_trace_tool_params_backfills_external_node_for_process_find(self) -> None:
        state: dict[str, Any] = {
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            }
        }
        rendered = nodes_module._render_trace_tool_params(
            state=state,
            tool_name="process.find",
            tool_args={"pattern": "stress|benchmark"},
        )
        self.assertEqual(rendered.get("node"), "10.11.4.13")
        self.assertEqual(rendered.get("pattern"), "stress|benchmark")

    def test_render_trace_tool_params_keeps_explicit_process_find_node(self) -> None:
        state: dict[str, Any] = {
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            }
        }
        rendered = nodes_module._render_trace_tool_params(
            state=state,
            tool_name="process.find",
            tool_args={"node": "10.11.4.99", "pattern": "stress"},
        )
        self.assertEqual(rendered.get("node"), "10.11.4.99")

    def test_render_trace_tool_params_keeps_nested_kwargs_process_find_node(self) -> None:
        state: dict[str, Any] = {
            "alert_snapshot": {
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            }
        }
        rendered = nodes_module._render_trace_tool_params(
            state=state,
            tool_name="process.find",
            tool_args={"kwargs": {"node": "10.11.4.12", "pattern": "stress"}},
        )
        self.assertEqual(rendered, {"node": "10.11.4.12", "pattern": "stress"})

    def test_build_tool_prompt_fields_gpu_metrics_carries_node_hint(self) -> None:
        fields = nodes_module._build_tool_prompt_fields(
            session_id="sess-1",
            source="tool",
            step=1,
            tool="gpu.get_metrics",
            params={"node": "worker-03"},
            data={"output": "0, NVIDIA-A100, 71, 12000, 80000, 68"},
            error="",
            skill_id=None,
        )
        key_fields = fields.get("key_fields")
        self.assertIsInstance(key_fields, dict)
        self.assertEqual(key_fields.get("node"), "worker-03")
        self.assertIn("node=worker-03", str(fields.get("prompt_summary", "")))

    def test_build_tool_prompt_fields_gpu_processes_emits_suspect_details(self) -> None:
        fields = nodes_module._build_tool_prompt_fields(
            session_id="sess-1",
            source="tool",
            step=2,
            tool="gpu.get_processes",
            params={"node": "worker-03"},
            data={
                "output": (
                    "391287, VLLM::Worker_TP0, GPU-a, 22676\n"
                    "2087515, fi_gpu_burn_gpu_contention_6f8d3724, GPU-a, 9272\n"
                )
            },
            error="",
            skill_id=None,
        )
        key_fields = fields.get("key_fields")
        self.assertIsInstance(key_fields, dict)
        assert isinstance(key_fields, dict)
        self.assertTrue(key_fields.get("suspicious_load_present"))
        self.assertEqual(key_fields.get("total_mem"), 31948)
        suspicious = key_fields.get("suspicious_load_processes")
        self.assertIsInstance(suspicious, list)
        assert isinstance(suspicious, list)
        self.assertEqual(suspicious[0].get("pid"), 2087515)
        self.assertIn("fi_gpu_burn", str(suspicious[0].get("process_name")))
        self.assertIn("suspicious_load_present=true", str(fields.get("prompt_summary", "")))

    def test_build_tool_prompt_fields_bmc_fan_status_carries_node_hint(self) -> None:
        fields = nodes_module._build_tool_prompt_fields(
            session_id="sess-1",
            source="tool",
            step=2,
            tool="bmc.get_fan_status",
            params={"node": "10.11.4.13"},
            data={
                "fan_status_summary": {
                    "mode_name": "Auto",
                    "is_manual": False,
                    "is_fixed_pwm": False,
                    "fixed_pwm": None,
                    "unique_pwm_values": [40, 42],
                    "fan_count": 8,
                },
                "bmc_host": "10.11.4.13",
            },
            error="",
            skill_id=None,
        )
        key_fields = fields.get("key_fields")
        self.assertIsInstance(key_fields, dict)
        self.assertEqual(key_fields.get("node"), "10.11.4.13")
        self.assertIn("node=10.11.4.13", str(fields.get("prompt_summary", "")))

    def test_extract_evidence_signals_tracks_gpu_temperature_process_and_fan_status(self) -> None:
        gpu_metrics_fields = nodes_module._build_tool_prompt_fields(
            session_id="sess-1",
            source="tool",
            step=1,
            tool="gpu.get_metrics",
            params={"node": "worker-03"},
            data={"output": "0, NVIDIA-A100, 100, 12000, 80000, 68"},
            error="",
            skill_id=None,
        )
        gpu_process_fields = nodes_module._build_tool_prompt_fields(
            session_id="sess-1",
            source="tool",
            step=2,
            tool="gpu.get_processes",
            params={"node": "worker-03"},
            data={"output": "2087515, fi_gpu_burn_gpu_contention_6f8d3724, GPU-a, 9272"},
            error="",
            skill_id=None,
        )
        fan_fields = nodes_module._build_tool_prompt_fields(
            session_id="sess-1",
            source="tool",
            step=3,
            tool="bmc.get_fan_status",
            params={"node": "worker-03"},
            data={
                "fan_status_summary": {
                    "mode_name": "Auto",
                    "is_manual": False,
                    "is_fixed_pwm": False,
                    "fixed_pwm": None,
                    "unique_pwm_values": [40, 42],
                    "fan_count": 8,
                }
            },
            error="",
            skill_id=None,
        )
        tool_runs = [
            {
                "step": 1,
                "tool": "gpu.get_metrics",
                "success": True,
                "params": {"node": "worker-03"},
                "key_fields": gpu_metrics_fields.get("key_fields"),
            },
            {
                "step": 2,
                "tool": "gpu.get_processes",
                "success": True,
                "params": {"node": "worker-03"},
                "key_fields": gpu_process_fields.get("key_fields"),
            },
            {
                "step": 3,
                "tool": "bmc.get_fan_status",
                "success": True,
                "params": {"node": "worker-03"},
                "key_fields": fan_fields.get("key_fields"),
            },
        ]
        signals = nodes_module._extract_evidence_signals(tool_runs)
        self.assertEqual(signals["ttft_gpu_metrics_steps"], [1])
        self.assertEqual(signals["ttft_gpu_process_steps"], [2])
        self.assertEqual(signals["gpu_max_temp"], 68)
        self.assertEqual(signals["gpu_max_util"], 100)
        self.assertTrue(signals["gpu_thermal_normal"])
        self.assertTrue(signals["ttft_suspect_process_present"])
        self.assertEqual(signals["bmc_fan_status_steps"], [3])
        self.assertEqual(signals["bmc_fan_mode"], "Auto")
        self.assertFalse(signals["bmc_fan_is_manual"])
        self.assertEqual(signals["bmc_fan_count"], 8)

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
        diagnosis = _diagnosis_result(
            title="ttft load contention",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.86,
            impact_summary="ttft elevated",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
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
        diagnosis = _diagnosis_result(
            title="ttft load contention",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.86,
            impact_summary="ttft elevated",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
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

    def test_normalize_step_params_in_place_aligns_kill_verification_node(self) -> None:
        diagnosis = _diagnosis_result(
            title="external load contention",
            layer="service",
            entities=["10.11.4.13"],
            confidence=0.86,
            impact_summary="ttft elevated",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
            diagnosis_certainty="confirmed",
        )
        registry = build_default_registry()
        step = {
            "tool": "kill_process",
            "params": {"node": "10.11.4.13", "pid": 4077465, "signal": "TERM"},
            "verification": {
                "method": "tool_call",
                "tool": "process.find",
                "tool_params": {"node": "10.11.4.13", "pattern": "4077465"},
                "condition": {"field": "count", "operator": "==", "value": 0},
                "wait_seconds": 30,
            },
        }

        error = nodes_module._normalize_step_params_in_place(
            step,
            diagnosis=diagnosis,
            registry=registry,
            tool_runs=[],
            variables={},
        )

        self.assertIsNone(error)
        self.assertEqual(step["verification"]["tool_params"]["node"], step["params"]["node"])

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
        diagnosis = _diagnosis_result(
            title="异常负载导致 TTFT 抬高",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.71,
            impact_summary="同节点多进程压测争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-force-canary",
            "root_cause": diagnosis.root_cause[0].title,
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
        diagnosis = _diagnosis_result(
            title="异常负载导致 TTFT 抬高",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.71,
            impact_summary="同节点多进程压测争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-no-force-canary",
            "root_cause": diagnosis.root_cause[0].title,
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
        diagnosis = _diagnosis_result(
            title="异常负载导致 TTFT 抬高",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.74,
            impact_summary="多进程负载争用导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-force-canary-override",
            "root_cause": diagnosis.root_cause[0].title,
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
        diagnosis = _diagnosis_result(
            title="异常负载导致 TTFT 抬高",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.74,
            impact_summary="同节点异常进程导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-force-canary-invalid-monitor-duration",
            "root_cause": diagnosis.root_cause[0].title,
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
        diagnosis = _diagnosis_result(
            title="异常负载导致 TTFT 抬高",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.74,
            impact_summary="多进程异常负载导致首 token 延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-force-canary-invalid-ratio-batches",
            "root_cause": diagnosis.root_cause[0].title,
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
        diagnosis = _diagnosis_result(
            title="异常负载导致延迟抬高",
            layer="service",
            entities=["node:10.11.4.13"],
            confidence=0.71,
            impact_summary="同节点多进程压测争用导致延迟上升",
            affected_services=["qwen3-32b-fp8-202602261"],
            triage_priority="P1",
            diagnosis_certainty="probable",
        )
        raw_plan = {
            "plan_id": "plan-no-force-canary-non-ttft",
            "root_cause": diagnosis.root_cause[0].title,
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

    def test_normalize_diagnosis_payload_promotes_strong_confirmed_hypothesis(self) -> None:
        payload: dict[str, Any] = {
            "root_cause": [
                {
                    "id": "rc-1",
                    "title": "GPU contention from fi_gpu_burn process",
                    "layer": "service",
                    "entities": ["worker-03", "fi_gpu_burn_gpu_cont"],
                    "confidence": 0.95,
                    "certainty": "confirmed",
                    "status": "confirmed",
                    "evidence_summary": "gpu.get_processes found fi_gpu_burn occupying GPU resources",
                    "impact_summary": "TTFT P99 increased significantly",
                    "distinguishing_verification": "Terminate fi_gpu_burn and observe TTFT recovery",
                    "recommended_fix": None,
                }
            ],
            "confidence": 0.95,
            "diagnosis_certainty": "confirmed",
            "impact_summary": "TTFT P99 increased significantly",
            "hypotheses": [
                {
                    "description": "External load_simulator continuously sends high request volume",
                    "status": "confirmed",
                    "evidence_for": [
                        "process.find on 10.11.4.13 found 42 matching processes",
                        "python -m load_simulator run process exists",
                    ],
                    "evidence_against": [],
                    "confidence": 0.85,
                }
            ],
        }

        normalized = nodes_module._normalize_diagnosis_payload(payload)
        root_causes = normalized.get("root_cause")
        self.assertIsInstance(root_causes, list)
        assert isinstance(root_causes, list)
        self.assertEqual(len(root_causes), 2)
        self.assertEqual(root_causes[0]["title"], "GPU contention from fi_gpu_burn process")
        self.assertEqual(root_causes[1]["title"], "External load_simulator continuously sends high request volume")
        self.assertEqual(root_causes[1]["status"], "contributing")
        self.assertAlmostEqual(float(root_causes[1]["confidence"]), 0.85)

    def test_normalize_diagnosis_payload_does_not_duplicate_same_confirmed_hypothesis(self) -> None:
        payload: dict[str, Any] = {
            "root_cause": [
                {
                    "id": "rc-1",
                    "title": "GPU contention from fi_gpu_burn_gpu_cont process",
                    "layer": "service",
                    "entities": ["worker-03", "fi_gpu_burn_gpu_cont"],
                    "confidence": 0.94,
                    "certainty": "confirmed",
                    "status": "confirmed",
                    "evidence_summary": "gpu.get_processes found fi_gpu_burn_gpu_cont occupying GPU resources",
                    "impact_summary": "TTFT P99 increased significantly",
                    "distinguishing_verification": "Terminate fi_gpu_burn_gpu_cont and observe TTFT recovery",
                    "recommended_fix": None,
                }
            ],
            "confidence": 0.94,
            "diagnosis_certainty": "confirmed",
            "impact_summary": "TTFT P99 increased significantly",
            "hypotheses": [
                {
                    "description": "GPU contention from fi_gpu_burn process",
                    "status": "confirmed",
                    "evidence_for": [
                        "gpu.get_processes found fi_gpu_burn_gpu_cont process",
                        "GPU utilization remained close to 100%",
                    ],
                    "evidence_against": [],
                    "confidence": 0.9,
                }
            ],
        }

        normalized = nodes_module._normalize_diagnosis_payload(payload)
        root_causes = normalized.get("root_cause")
        self.assertIsInstance(root_causes, list)
        assert isinstance(root_causes, list)
        self.assertEqual(len(root_causes), 1)

    def test_normalize_diagnosis_payload_decouples_multi_root_evidence_summary(self) -> None:
        payload: dict[str, Any] = {
            "root_cause": [
                {
                    "id": "rc-1",
                    "title": "GPU contention from fi_gpu_burn process",
                    "layer": "service",
                    "entities": ["worker-03", "fi_gpu_burn_gpu_cont"],
                    "confidence": 0.95,
                    "certainty": "confirmed",
                    "status": "confirmed",
                    "evidence_summary": (
                        "gpu.get_processes found fi_gpu_burn; "
                        "process.find on 10.11.4.13 found load_simulator; "
                        "TTFT P99 exceeded threshold"
                    ),
                    "impact_summary": "TTFT P99 increased significantly",
                    "distinguishing_verification": "Terminate fi_gpu_burn and observe TTFT recovery",
                    "recommended_fix": None,
                }
            ],
            "confidence": 0.95,
            "diagnosis_certainty": "confirmed",
            "impact_summary": "TTFT P99 increased significantly",
            "hypotheses": [
                {
                    "description": "GPU contention from fi_gpu_burn process",
                    "status": "confirmed",
                    "evidence_for": [
                        "gpu.get_processes found fi_gpu_burn process",
                        "GPU utilization remained close to 100%",
                    ],
                    "evidence_against": [],
                    "confidence": 0.95,
                },
                {
                    "description": "External load_simulator continuously sends high request volume",
                    "status": "confirmed",
                    "evidence_for": [
                        "process.find on 10.11.4.13 found 42 matching processes",
                        "python -m load_simulator run process exists",
                    ],
                    "evidence_against": [],
                    "confidence": 0.85,
                },
            ],
        }

        normalized = nodes_module._normalize_diagnosis_payload(payload)
        root_causes = normalized.get("root_cause")
        self.assertIsInstance(root_causes, list)
        assert isinstance(root_causes, list)
        self.assertEqual(len(root_causes), 2)

        primary_summary = str(root_causes[0].get("evidence_summary", ""))
        secondary_summary = str(root_causes[1].get("evidence_summary", ""))

        self.assertIn("gpu.get_processes found fi_gpu_burn process", primary_summary)
        self.assertNotIn("10.11.4.13", primary_summary)
        self.assertIn("process.find on 10.11.4.13 found 42 matching processes", secondary_summary)

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

    async def test_reason_node_ttft_coverage_gate_injects_forced_calls_when_fallback_has_no_tool_calls(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(content="基于已收集的证据链，诊断结论已经明确。让我整理最终诊断结果。"),
            ]
        )
        state = nodes_module.initialize_state(
            query="Diagnose AIServiceTTFTP99High with strict TTFT workflow.",
            variables={"node": "worker-03"},
            session_id="sess-ttft-gate",
            step_timeout_sec=5.0,
            total_timeout_sec=30.0,
            max_steps=6,
            alert_snapshot={
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
        )

        result = await nodes_module.reason_node(state, llm=llm, registry=build_default_registry())
        self.assertEqual(result["status"], "running")
        pending_names = [str(item.get("name", "")).strip() for item in result.get("pending_tool_calls", [])]
        self.assertIn("gpu.get_processes", pending_names)
        self.assertIn("process.find", pending_names)
        trace_items = [item for item in result.get("trace_items", []) if isinstance(item, dict)]
        self.assertTrue(
            any(
                str((item.get("meta") or {}).get("reason", "")).strip() == "ttft_coverage_gate"
                for item in trace_items
            )
        )

    async def test_reason_node_ttft_coverage_gate_only_injects_external_probe_when_gpu_coverage_met(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(content="诊断结果已经明确，可以给出最终诊断。"),
            ]
        )
        state = nodes_module.initialize_state(
            query="Diagnose AIServiceTTFTP99High with strict TTFT workflow.",
            variables={"node": "worker-03"},
            session_id="sess-ttft-gate-external-only",
            step_timeout_sec=5.0,
            total_timeout_sec=30.0,
            max_steps=6,
            alert_snapshot={
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
        )
        state["tool_runs"] = [
            {
                "tool": "gpu.get_processes",
                "params": {"node": "worker-03"},
                "success": True,
                "data": {},
            }
        ]

        result = await nodes_module.reason_node(state, llm=llm, registry=build_default_registry())
        self.assertEqual(result["status"], "running")
        pending = result.get("pending_tool_calls", [])
        self.assertEqual(len(pending), 1)
        self.assertEqual(str(pending[0].get("name", "")).strip(), "process.find")

    async def test_reason_node_ttft_coverage_met_non_json_still_generates_auto_remediation_plan(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(content="基于已收集的证据链，诊断结论已经明确。让我整理最终诊断结果。"),
                AIMessage(content="诊断已完成，但本轮不返回 remediation_plan 字段。"),
            ]
        )
        state = nodes_module.initialize_state(
            query="Diagnose AIServiceTTFTP99High with strict TTFT workflow.",
            variables={"node": "worker-03"},
            session_id="sess-ttft-auto-plan",
            step_timeout_sec=5.0,
            total_timeout_sec=30.0,
            max_steps=6,
            alert_snapshot={
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
        )
        state["tool_runs"] = [
            {
                "tool": "gpu.get_processes",
                "params": {"node": "worker-03"},
                "success": True,
                "data": {},
                "key_fields": {
                    "suspicious_load_processes": [
                        {
                            "pid": 509120,
                            "process_name": "fi_gpu_burn_gpu_contention_ad4cf1ee -m 100% -i 0 3600",
                            "memory_mib": 63884,
                        }
                    ]
                },
            },
            {
                "tool": "process.find",
                "params": {"pattern": "load_simulator|gpu_burn", "node": "10.11.4.13"},
                "success": True,
                "data": {"node": "10.11.4.13", "count": 0},
            },
        ]

        result = await nodes_module.reason_node(state, llm=llm, registry=build_default_registry())
        self.assertEqual(result["status"], "diagnosed")
        self.assertIsNotNone(result.get("remediation_plan"))
        diagnosis_result = result.get("diagnosis_result") or {}
        root_causes = (diagnosis_result or {}).get("root_cause") or []
        self.assertIsNotNone(root_causes[0].get("recommended_fix"))
        self.assertEqual(result.get("plan_missing_reason"), None)

    async def test_reason_node_ttft_external_probe_blocked_can_finalize_without_coverage_gate(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(content="最终诊断结论：证据链受外部节点权限限制。"),
                AIMessage(content="诊断已完成，但不返回结构化 remediation_plan。"),
            ]
        )
        state = nodes_module.initialize_state(
            query="Diagnose AIServiceTTFTP99High with strict TTFT workflow.",
            variables={
                "node": "worker-03",
                "ttft_external_probe_blocked_reason": "SSH precheck failed for TTFT external node 10.11.4.13",
            },
            session_id="sess-ttft-blocked-finalize",
            step_timeout_sec=5.0,
            total_timeout_sec=30.0,
            max_steps=6,
            alert_snapshot={
                "alert_name": "AIServiceTTFTP99High",
                "ttft_external_process_default_node": "10.11.4.13",
            },
        )

        result = await nodes_module.reason_node(state, llm=llm, registry=build_default_registry())
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result.get("pending_tool_calls"), [])
        self.assertIn("TTFT external probe blocked by SSH authentication failure", str(result.get("plan_missing_reason", "")))


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
