from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

import sre_agent.agent as agent_module
from sre_agent.agent import run_diagnosis, run_diagnosis_stream
from sre_agent.agent.nodes import (
    _normalize_hypotheses_payload,
    _normalize_remediation_plan_payload,
    initialize_state,
    reason_node,
)
from sre_agent.agent.prompts import build_system_prompt
from sre_agent.models.diagnosis import DiagnosisResult, ThinkingTrace
from sre_agent.models.remediation import RemediationPlan
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


def _happy_context() -> ToolExecutionContext:
    from lib.channels.kubernetes import K8sChannel

    return ToolExecutionContext(
        channels={
            "k8s": K8sChannel(client=_FakeK8sClient()),
            "prometheus": _FakePrometheus(),
            "ssh": _FakeSSHChannel(),
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


class _FakeLLM:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.index = 0
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append(
            {
                "messages": messages,
                "tools": [],
                "tool_choice": "none",
            }
        )
        response = self.responses[self.index]
        self.index += 1
        return response

    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> _FakeBoundLLM:
        return _FakeBoundLLM(self, tools, tool_choice)


class _SlowLLM:
    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> "_SlowLLM":
        _ = tools, tool_choice
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        _ = messages
        import asyncio

        await asyncio.sleep(0.2)
        return AIMessage(content="{}")


class _ContextWindowBoundLLM:
    def __init__(self, parent: "_ContextWindowGuardLLM", tools: list[Any], tool_choice: str) -> None:
        self._parent = parent
        self._tools = tools
        self._tool_choice = tool_choice

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        total_chars = sum(len(str(getattr(message, "content", "") or "")) for message in messages)
        self._parent.message_sizes.append(total_chars)
        self._parent.calls.append(
            {
                "messages": messages,
                "tools": self._tools,
                "tool_choice": self._tool_choice,
            }
        )
        if total_chars > self._parent.limit_chars:
            raise RuntimeError(
                f"Error code: 400 - invalid params, context window exceeds limit ({self._parent.limit_chars})"
            )
        response = self._parent.responses[self._parent.index]
        self._parent.index += 1
        return response


class _ContextWindowGuardLLM(_FakeLLM):
    def __init__(self, responses: list[AIMessage], *, limit_chars: int) -> None:
        super().__init__(responses)
        self.limit_chars = limit_chars
        self.message_sizes: list[int] = []

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        total_chars = sum(len(str(getattr(message, "content", "") or "")) for message in messages)
        self.message_sizes.append(total_chars)
        self.calls.append(
            {
                "messages": messages,
                "tools": [],
                "tool_choice": "none",
            }
        )
        if total_chars > self.limit_chars:
            raise RuntimeError(
                f"Error code: 400 - invalid params, context window exceeds limit ({self.limit_chars})"
            )
        response = self.responses[self.index]
        self.index += 1
        return response

    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> _ContextWindowBoundLLM:
        return _ContextWindowBoundLLM(self, tools, tool_choice)


class _AlwaysContextOverflowLLM:
    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> "_AlwaysContextOverflowLLM":
        _ = tools, tool_choice
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        _ = messages
        raise RuntimeError("Error code: 400 - invalid params, context window exceeds limit (2013)")


class _ToolIdStrictBoundLLM:
    def __init__(self, parent: "_ToolIdStrictLLM", tools: list[Any], tool_choice: str) -> None:
        self._parent = parent
        self._tools = tools
        self._tool_choice = tool_choice

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self._parent._validate_tool_message_refs(messages)
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


class _ToolIdStrictLLM(_FakeLLM):
    def _validate_tool_message_refs(self, messages: list[Any]) -> None:
        seen_tool_call_ids: set[str] = set()
        for message in messages:
            if isinstance(message, AIMessage):
                for call in list(getattr(message, "tool_calls", []) or []):
                    if not isinstance(call, dict):
                        continue
                    call_id = str(call.get("id", "")).strip()
                    if call_id:
                        seen_tool_call_ids.add(call_id)
                continue
            if isinstance(message, ToolMessage):
                tool_call_id = str(getattr(message, "tool_call_id", "")).strip()
                if not tool_call_id or tool_call_id not in seen_tool_call_ids:
                    raise RuntimeError(
                        "Error code: 400 - {'type': 'error', 'error': {'type': 'bad_request_error', "
                        f"'message': \"invalid params, tool result's tool id({tool_call_id}) not found (2013)\", "
                        "'http_code': '400'}}"
                    )

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self._validate_tool_message_refs(messages)
        return await super().ainvoke(messages)

    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> _ToolIdStrictBoundLLM:
        return _ToolIdStrictBoundLLM(self, tools, tool_choice)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    return str(content or "").strip()


class _ChatContentStrictBoundLLM:
    def __init__(self, parent: "_ChatContentStrictLLM", tools: list[Any], tool_choice: str) -> None:
        self._parent = parent
        self._tools = tools
        self._tool_choice = tool_choice

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return await self._parent._invoke(messages, tools=self._tools, tool_choice=self._tool_choice)


class _ChatContentStrictLLM(_FakeLLM):
    def _validate_non_empty_chat(self, messages: list[Any]) -> None:
        has_non_system_text = any(
            _message_text(message)
            for message in messages
            if not isinstance(message, SystemMessage)
        )
        if not has_non_system_text:
            raise RuntimeError(
                "Error code: 400 - {'type': 'error', 'error': {'type': 'bad_request_error', "
                "'message': 'invalid params, chat content is empty (2013)', 'http_code': '400'}}"
            )

    async def _invoke(self, messages: list[Any], *, tools: list[Any], tool_choice: str) -> AIMessage:
        self._validate_non_empty_chat(messages)
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        response = self.responses[self.index]
        self.index += 1
        return response

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return await self._invoke(messages, tools=[], tool_choice="none")

    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> _ChatContentStrictBoundLLM:
        return _ChatContentStrictBoundLLM(self, tools, tool_choice)


class _StateRebuiltPromptLLM(_ChatContentStrictLLM):
    def __init__(self, responses: list[AIMessage], *, required_fragments: list[str]) -> None:
        super().__init__(responses)
        self.required_fragments = required_fragments

    async def _invoke(self, messages: list[Any], *, tools: list[Any], tool_choice: str) -> AIMessage:
        self._validate_non_empty_chat(messages)
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        joined = "\n".join(_message_text(message) for message in messages if not isinstance(message, SystemMessage))
        for fragment in self.required_fragments:
            if fragment not in joined:
                raise AssertionError(f"missing guaranteed-fidelity fragment: {fragment}")
        response = self.responses[self.index]
        self.index += 1
        return response


class _NeverInvokeLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> "_NeverInvokeLLM":
        _ = tools, tool_choice
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append({"messages": messages})
        raise AssertionError("provider should not be called when guaranteed-fidelity overflow fails locally")


class _FirstCallErrorLLM(_FakeLLM):
    def __init__(self, responses: list[AIMessage], *, error: Exception) -> None:
        super().__init__(responses)
        self._error = error
        self._raised = False

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append(
            {
                "messages": messages,
                "tools": [],
                "tool_choice": "none",
            }
        )
        if not self._raised:
            self._raised = True
            raise self._error
        response = self.responses[self.index]
        self.index += 1
        return response


class TestGraphUnit(unittest.IsolatedAsyncioTestCase):
    def test_agent_package_exports_stream_runner(self) -> None:
        self.assertIn("run_diagnosis_stream", agent_module.__all__)
        self.assertIs(run_diagnosis_stream, agent_module.run_diagnosis_stream)

    async def test_reason_node_applies_context_budget_before_llm_call(self) -> None:
        llm = _ContextWindowGuardLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Use compact context and conclude.",
                            "diagnosis": {
                                "root_cause": "Context budget applied successfully",
                                "root_cause_layer": "platform",
                                "root_cause_entities": [],
                                "confidence": 0.7,
                                "impact_summary": "Reasoning continued within the configured context budget.",
                                "affected_services": ["platform"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                )
            ],
            limit_chars=2800,
        )
        long_query = "Network latency investigation details. " * 600
        result = await run_diagnosis(
            query=long_query,
            context=_happy_context(),
            variables={"promql": "up"},
            llm=llm,
            reasoning_context_strategy="transcript_compact",
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            reason_context_char_budget=2400,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "diagnosed")
        self.assertTrue(llm.message_sizes)
        self.assertLessEqual(max(llm.message_sizes), 2800)

    async def test_reason_context_window_error_adds_overflow_trace(self) -> None:
        result = await run_diagnosis(
            query="Diagnose platform health quickly.",
            context=_happy_context(),
            variables={"promql": "up"},
            llm=_AlwaysContextOverflowLLM(),
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("context window limit reached", result["summary"] or "")
        self.assertTrue(
            any(
                item.get("type") == "thought"
                and item.get("tool_params", {}).get("kind") == "reason_context_overflow"
                for item in result.get("trace_items", [])
            )
        )

    async def test_act_node_truncates_large_tool_message_content(self) -> None:
        class _HugeK8sClient:
            def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
                _ = namespace, label_selector
                return [{"name": f"pod-{idx}", "namespace": "default"} for idx in range(800)]

        from lib.channels.kubernetes import K8sChannel

        context = ToolExecutionContext(
            channels={
                "k8s": K8sChannel(client=_HugeK8sClient()),
                "prometheus": _FakePrometheus(),
                "ssh": _FakeSSHChannel(),
            }
        )
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Need pod inventory first.",
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
                    content=json.dumps(
                        {
                            "thought": "Collected enough evidence from pod list.",
                            "diagnosis": {
                                "root_cause": "No active outage detected",
                                "root_cause_layer": "platform",
                                "root_cause_entities": [],
                                "confidence": 0.6,
                                "impact_summary": "Large tool payload was truncated and diagnosis still completed.",
                                "affected_services": ["platform"],
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
            query="Inspect pods and conclude.",
            context=context,
            variables={},
            llm=llm,
            tool_message_char_limit=320,
            reason_context_char_budget=2400,
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            allowed_tool_names=["k8s.list_pods"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "diagnosed")
        tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        self.assertTrue(tool_messages)
        self.assertLessEqual(max(len(str(message.content or "")) for message in tool_messages), 320)

    async def test_reason_context_budget_removes_orphan_tool_messages(self) -> None:
        llm = _ToolIdStrictLLM(
            [
                AIMessage(
                    content="Need metric evidence before concluding.",
                    tool_calls=[
                        {
                            "name": "prometheus.query_instant",
                            "args": {"promql": "up"},
                            "id": "call_function_btfb2dzm2n9c_3",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The compact context remains valid and no orphan tool result was sent.",
                            "diagnosis": {
                                "root_cause": "No active outage detected",
                                "root_cause_layer": "platform",
                                "root_cause_entities": [],
                                "confidence": 0.73,
                                "impact_summary": "Reasoning succeeded without invalid tool_call_id references.",
                                "affected_services": ["platform"],
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
            query="Investigate with strict context trimming. " * 80,
            context=_happy_context(),
            variables={"promql": "up"},
            llm=llm,
            reasoning_context_strategy="transcript_compact",
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            reason_context_char_budget=620,
            reason_preserve_recent_messages=1,
            tool_message_char_limit=240,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "diagnosed")
        self.assertGreaterEqual(len(llm.calls), 2)
        second_call_messages = llm.calls[1]["messages"]
        seen_tool_call_ids: set[str] = set()
        for message in second_call_messages:
            if isinstance(message, AIMessage):
                for call in list(getattr(message, "tool_calls", []) or []):
                    if isinstance(call, dict) and str(call.get("id", "")).strip():
                        seen_tool_call_ids.add(str(call.get("id", "")).strip())
                continue
            if isinstance(message, ToolMessage):
                self.assertIn(str(getattr(message, "tool_call_id", "")).strip(), seen_tool_call_ids)
        self.assertTrue(
            any(
                _message_text(message)
                for message in second_call_messages
                if not isinstance(message, SystemMessage)
            )
        )

    async def test_reason_prompt_injects_fallback_before_provider_empty_chat_error(self) -> None:
        llm = _ChatContentStrictLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Fallback prompt preserved enough context to conclude.",
                            "diagnosis": {
                                "root_cause": "No active issue detected",
                                "root_cause_layer": "platform",
                                "root_cause_entities": [],
                                "confidence": 0.51,
                                "impact_summary": "Prompt fallback prevented an empty-chat provider error.",
                                "affected_services": ["platform"],
                                "triage_priority": "P3",
                                "diagnosis_certainty": "ambiguous",
                            },
                            "remediation_plan": None,
                        }
                    )
                )
            ]
        )
        from sre_agent.skills import SkillPolicy, SkillRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            empty_skill_root = Path(tmpdir) / "empty-skills"
            empty_skill_root.mkdir(parents=True, exist_ok=True)
            result = await run_diagnosis(
                query="   ",
                context=_happy_context(),
                variables={},
                llm=llm,
                reasoning_context_strategy="transcript_compact",
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                allowed_tool_names=["prometheus.query_instant"],
                checkpoint_dir=None,
                skill_registry=SkillRegistry(root=empty_skill_root),
                skill_policy=SkillPolicy(),
            )
        self.assertEqual(result["status"], "diagnosed")
        self.assertTrue(llm.calls)
        self.assertTrue(
            any(
                _message_text(message)
                for message in llm.calls[0]["messages"]
                if not isinstance(message, SystemMessage)
            )
        )
        self.assertTrue(result["llm_interactions"][0]["prompt_fallback_used"])

    async def test_skill_summary_prefix_is_preserved_under_tight_budget(self) -> None:
        class _HugeK8sClient:
            def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
                _ = label_selector
                return [
                    {"name": f"pod-{idx}", "namespace": namespace, "status": {"phase": "Running"}}
                    for idx in range(900)
                ]

        from lib.channels.kubernetes import K8sChannel
        from sre_agent.skills import SkillPolicy, SkillRegistry

        context = ToolExecutionContext(
            channels={
                "k8s": K8sChannel(client=_HugeK8sClient()),
                "prometheus": _FakePrometheus(),
                "ssh": _FakeSSHChannel(),
            }
        )
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "This alert should use the custom network skill first.",
                            "skill_call": {
                                "skill_id": "custom-network-check",
                                "reason": "The reusable network skill matches the alert and should compact evidence.",
                            },
                        }
                    )
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The compact skill summary retained enough context to conclude.",
                            "diagnosis": {
                                "root_cause": "No active network incident confirmed",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["worker-03"],
                                "confidence": 0.62,
                                "impact_summary": "Compact skill evidence remained readable under tight context budget.",
                                "affected_services": ["network"],
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
            skill_dir = skill_root / "network-check"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: Network Check
description: Compact network skill summary regression fixture.
---

## Runtime Metadata
```yaml
id: custom-network-check
scope: custom
permissions:
  - read:k8s
tags:
  - network
  - latency
```

## Steps
```yaml
- description: List candidate pods.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
```
""",
                encoding="utf-8",
            )
            result = await run_diagnosis(
                query="alertname NetworkLatencyHigh100ms summary network latency average RTT is high",
                context=context,
                variables={"namespace": "service", "node": "worker-03"},
                llm=llm,
                reasoning_context_strategy="transcript_compact",
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                reason_context_char_budget=620,
                reason_preserve_recent_messages=1,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["k8s.list_pods"],
                skill_registry=SkillRegistry(root=skill_root),
                skill_policy=SkillPolicy(),
            )
        self.assertEqual(result["status"], "diagnosed")
        self.assertGreaterEqual(len(llm.calls), 2)
        joined = "\n".join(_message_text(message) for message in llm.calls[1]["messages"])
        self.assertIn("Skill result: skill_id=custom-network-check", joined)
        self.assertNotIn("Skill execution result for bui", joined)

    async def test_network_skill_followup_keeps_context_and_avoids_bui_regression(self) -> None:
        class _HugeK8sClient:
            def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
                _ = label_selector
                return [
                    {"name": f"pod-{idx}", "namespace": namespace, "status": {"phase": "Running"}}
                    for idx in range(700)
                ]

        class _NetworkScenarioLLM(_ChatContentStrictLLM):
            async def _invoke(self, messages: list[Any], *, tools: list[Any], tool_choice: str) -> AIMessage:
                self._validate_non_empty_chat(messages)
                self.calls.append(
                    {
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": tool_choice,
                    }
                )
                index = len(self.calls) - 1
                joined = "\n".join(_message_text(message) for message in messages if not isinstance(message, SystemMessage))
                if index == 0:
                    return AIMessage(
                        content=json.dumps(
                            {
                                "thought": "Use the builtin network skill first.",
                                "skill_call": {
                                    "skill_id": "builtin-network-diagnosis",
                                    "reason": "This is a direct network RTT alert.",
                                },
                            }
                        )
                    )
                if index == 1:
                    if "Skill result: skill_id=builtin-network-diagnosis" not in joined:
                        raise AssertionError("skill summary prefix was lost from the prompt")
                    if "Skill execution result for bui" in joined:
                        raise AssertionError("prompt corruption regressed to truncated 'bui' skill summary")
                    if "NetworkLatencyHigh100ms" not in joined:
                        raise AssertionError("original alert context was lost from the follow-up prompt")
                    return AIMessage(
                        content="Use the original RTT metric as the next evidence step.",
                        tool_calls=[
                            {
                                "name": "prometheus.query_instant",
                                "args": {"promql": 'probe_icmp_duration_seconds{instance="10.11.0.12"}'},
                                "id": "call-network-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                return AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The follow-up reasoning stayed anchored to the network alert.",
                            "diagnosis": {
                                "root_cause": "Network latency alert still requires infrastructure-side investigation",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["10.11.0.12", "worker-03"],
                                "confidence": 0.68,
                                "impact_summary": "Follow-up reasoning avoided truncated skill context and stayed on the RTT signal.",
                                "affected_services": ["network"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                )

        from lib.channels.kubernetes import K8sChannel
        from sre_agent.skills import SkillPolicy, SkillRegistry

        context = ToolExecutionContext(
            channels={
                "k8s": K8sChannel(client=_HugeK8sClient()),
                "prometheus": _FakePrometheus(),
                "ssh": _FakeSSHChannel(),
            }
        )
        llm = _NetworkScenarioLLM([])
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "builtin-network-diagnosis"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: Network Diagnosis
description: Reproduce the network skill follow-up path.
---

## Runtime Metadata
```yaml
id: builtin-network-diagnosis
scope: builtin
permissions:
  - read:k8s
tags:
  - network
  - latency
```

## Steps
```yaml
- description: List service pods.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
```
""",
                encoding="utf-8",
            )
            result = await run_diagnosis(
                query=(
                    "Diagnose alert 'NetworkLatencyHigh100ms' with severity 'warning'. "
                    "Summary: Network latency average RTT is high (>50ms). "
                    "Description: ICMP average RTT on 10.11.0.12 over the last 1 minute is above 50ms. "
                    "Current value=201.04563599999997ms.. Use available tools to identify root cause and produce ranked candidates."
                ),
                context=context,
                variables={
                    "namespace": "service",
                    "node": "worker-03",
                    "promql": 'probe_icmp_duration_seconds{instance="10.11.0.12"}',
                },
                llm=llm,
                reasoning_context_strategy="transcript_compact",
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                reason_context_char_budget=620,
                reason_preserve_recent_messages=1,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["k8s.list_pods", "prometheus.query_instant"],
                skill_registry=SkillRegistry(root=skill_root),
                skill_policy=SkillPolicy(),
            )
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["tool_runs"][0]["tool"], "k8s.list_pods")
        self.assertEqual(result["tool_runs"][1]["tool"], "prometheus.query_instant")
        self.assertFalse("chat content is empty" in str(result.get("summary") or ""))

    async def test_state_rebuilt_reason_prompt_keeps_canonical_context_without_transcript(self) -> None:
        from sre_agent.skills import SkillPolicy, SkillRegistry

        llm = _StateRebuiltPromptLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Canonical state-rebuilt evidence stayed intact without transcript history.",
                            "diagnosis": {
                                "root_cause": "Network telemetry remains the most likely explanation",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["worker-03", "10.11.0.12"],
                                "confidence": 0.74,
                                "impact_summary": "Reasoning used canonical alert, diagnosis, skill, and tool evidence from state.",
                                "affected_services": ["network"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                )
            ],
            required_fragments=[
                "Original query and runtime variables:",
                "alertname NetworkLatencyHigh100ms",
                '"root_cause": "Earlier NIC queue pressure was suspected"',
                '"skill_id": "builtin-network-diagnosis"',
                '"tool": "k8s.list_pods"',
                '"summary": "pods=1; non_running=0; phases=Running:1"',
                '"artifact_ref": "session:state-rebuilt-ctx-1:tool:skill:1:k8s.list_pods"',
                '"tool": "prometheus.query_instant"',
                '"summary": "scalar=1.0"',
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_skill_root = Path(tmpdir) / "empty-skills"
            empty_skill_root.mkdir(parents=True, exist_ok=True)
            state = initialize_state(
                query="alertname NetworkLatencyHigh100ms summary network RTT is high",
                variables={"namespace": "service", "node": "worker-03"},
                session_id="state-rebuilt-ctx-1",
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                max_steps=5,
                reasoning_context_strategy="state_rebuilt",
                reasoning_overflow_behavior="fail",
                reasoning_input_target_tokens=180000,
                reasoning_model_family="MiniMax-M2.7",
                reason_context_char_budget=620,
                tool_message_char_limit=240,
                reason_preserve_recent_messages=1,
                checkpoint_dir=None,
                allowed_tool_names=["prometheus.query_instant"],
                alert_snapshot={"alert_name": "NetworkLatencyHigh100ms", "annotations": {"summary": "network RTT is high"}},
                topology_context={"summary": "worker-03 is connected through sw-200g"},
                extra_alerts=[{"alert_name": "NodePacketDropsHigh", "severity": "warning"}],
            )
            state["messages"] = []
            state["diagnosis_result"] = {
                "root_cause": "Earlier NIC queue pressure was suspected",
                "root_cause_layer": "network",
                "root_cause_entities": ["worker-03"],
                "confidence": 0.41,
                "impact_summary": "Intermediate hypothesis before fresh reasoning.",
                "affected_services": ["network"],
                "triage_priority": "P2",
                "diagnosis_certainty": "ambiguous",
            }
            state["skill_runs"] = [
                {
                    "step": 1,
                    "skill_id": "builtin-network-diagnosis",
                    "status": "success",
                    "summary": "success: 1 step(s) succeeded",
                    "selection_reason": "Reusable network RTT workflow matched the alert.",
                    "tool_runs": [
                        {
                            "step": 1,
                            "source": "skill",
                            "tool": "k8s.list_pods",
                            "params": {"namespace": "service"},
                            "success": True,
                            "data": [{"name": "svc-a"}],
                            "error": "",
                            "skill_id": "builtin-network-diagnosis",
                            "prompt_summary": "pods=1; non_running=0; phases=Running:1",
                            "artifact_ref": "session:state-rebuilt-ctx-1:tool:skill:1:k8s.list_pods",
                            "data_kind": "list",
                            "item_count": 1,
                            "key_fields": {"pod_count": 1, "non_running": 0, "phases": {"Running": 1}},
                        }
                    ],
                    "prompt_summary": (
                        "status=success; tools=1; tool_names=k8s.list_pods; "
                        "summary=success: 1 step(s) succeeded"
                    ),
                    "artifact_ref": "session:state-rebuilt-ctx-1:skill:1:builtin-network-diagnosis",
                }
            ]
            state["tool_runs"] = [
                {
                    "step": 1,
                    "source": "skill",
                    "tool": "k8s.list_pods",
                    "params": {"namespace": "service"},
                    "success": True,
                    "data": [{"name": "svc-a"}],
                    "error": "",
                    "skill_id": "builtin-network-diagnosis",
                    "prompt_summary": "pods=1; non_running=0; phases=Running:1",
                    "artifact_ref": "session:state-rebuilt-ctx-1:tool:skill:1:k8s.list_pods",
                    "data_kind": "list",
                    "item_count": 1,
                    "key_fields": {"pod_count": 1, "non_running": 0, "phases": {"Running": 1}},
                },
                {
                    "step": 2,
                    "source": "tool",
                    "tool": "prometheus.query_instant",
                    "params": {"promql": 'probe_icmp_duration_seconds{instance="10.11.0.12"}'},
                    "success": True,
                    "data": 1.0,
                    "error": "",
                    "skill_id": None,
                    "prompt_summary": "scalar=1.0",
                    "artifact_ref": "session:state-rebuilt-ctx-1:tool:tool:2:prometheus.query_instant",
                    "data_kind": "number",
                    "item_count": None,
                    "key_fields": {"result_shape": "scalar", "sample_count": 1, "value": 1.0},
                },
            ]
            state["step_count"] = 1
            result = await reason_node(
                state,
                llm=llm,
                registry=build_default_registry(),
                skill_registry=SkillRegistry(root=empty_skill_root),
                skill_policy=SkillPolicy(),
            )
        self.assertEqual(result["status"], "diagnosed")
        self.assertTrue(llm.calls)
        joined = "\n".join(
            _message_text(message)
            for message in llm.calls[0]["messages"]
            if not isinstance(message, SystemMessage)
        )
        self.assertNotIn('"data": [{"name": "svc-a"}]', joined)
        self.assertNotIn('"tool_runs": [{"data":', joined)
        prompt_metadata = result["llm_interactions"][0]["prompt_metadata"]
        self.assertEqual(prompt_metadata["prompt_mode"], "state_rebuilt_full")
        self.assertEqual(prompt_metadata["tool_run_count"], 2)
        self.assertEqual(prompt_metadata["skill_run_count"], 1)

    async def test_guaranteed_fidelity_overflow_fails_before_provider_call(self) -> None:
        llm = _NeverInvokeLLM()
        result = await run_diagnosis(
            query="Diagnose sustained network latency. " * 120,
            context=_happy_context(),
            variables={
                "namespace": "service",
                "large_blob": "x" * 4000,
            },
            llm=llm,
            reasoning_context_strategy="state_rebuilt",
            reasoning_overflow_behavior="fail",
            reasoning_input_target_tokens=64,
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("guaranteed-fidelity context exceeded safe model budget", result["summary"] or "")
        self.assertFalse(llm.calls)
        self.assertTrue(
            any(
                item.get("type") == "thought"
                and item.get("tool_params", {}).get("mode") == "guaranteed_fidelity"
                for item in result.get("trace_items", [])
            )
        )
        self.assertTrue(result["llm_interactions"])
        prompt_metadata = result["llm_interactions"][0]["prompt_metadata"]
        self.assertTrue(prompt_metadata["provider_invocation_skipped"])
        self.assertTrue(prompt_metadata["dominant_section"])
        self.assertIn("dominant=", result["summary"] or "")
        overflow_trace = next(
            item for item in result.get("trace_items", [])
            if item.get("tool_params", {}).get("kind") == "reason_context_overflow"
        )
        self.assertTrue(overflow_trace["tool_params"]["dominant_section"])

    async def test_guaranteed_fidelity_overflow_reports_largest_evidence_items(self) -> None:
        from sre_agent.skills import SkillPolicy, SkillRegistry

        llm = _NeverInvokeLLM()
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_skill_root = Path(tmpdir) / "empty-skills"
            empty_skill_root.mkdir(parents=True, exist_ok=True)
            state = initialize_state(
                query="Diagnose sustained RDMA anomalies on worker-03.",
                variables={"node": "worker-03"},
                session_id="overflow-diagnostics-1",
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                max_steps=5,
                reasoning_context_strategy="state_rebuilt",
                reasoning_overflow_behavior="fail",
                reasoning_input_target_tokens=32,
                reasoning_model_family="MiniMax-M2.7",
                reason_context_char_budget=620,
                tool_message_char_limit=240,
                reason_preserve_recent_messages=1,
                checkpoint_dir=None,
                allowed_tool_names=["network.get_rdma_stats"],
            )
            state["tool_runs"] = [
                {
                    "step": 1,
                    "source": "tool",
                    "tool": "network.get_rdma_stats",
                    "params": {"node": "worker-03"},
                    "success": True,
                    "data": {"output": "raw-rdma-dump-begin\nmlx5_error_counter_1=1"},
                    "error": "",
                    "skill_id": None,
                    "prompt_summary": "rdma_hotspots=" + ", ".join(
                        f"mlx5_error_counter_{idx}={idx}" for idx in range(40, 0, -1)
                    ),
                    "artifact_ref": "session:overflow-diagnostics-1:tool:tool:1:network.get_rdma_stats",
                    "data_kind": "object",
                    "item_count": 1,
                    "key_fields": {"abnormal_count": 40},
                }
            ]
            result = await reason_node(
                state,
                llm=llm,
                registry=build_default_registry(),
                skill_registry=SkillRegistry(root=empty_skill_root),
                skill_policy=SkillPolicy(),
            )
        self.assertEqual(result["status"], "failed")
        prompt_metadata = result["llm_interactions"][0]["prompt_metadata"]
        self.assertTrue(prompt_metadata["provider_invocation_skipped"])
        self.assertTrue(prompt_metadata["largest_evidence_items"])
        self.assertEqual(prompt_metadata["dominant_item"], "tool:network.get_rdma_stats#1")
        overflow_trace = next(
            item for item in result.get("trace_items", [])
            if item.get("tool_params", {}).get("kind") == "reason_context_overflow"
        )
        self.assertTrue(overflow_trace["tool_params"]["largest_evidence_items"])
        self.assertEqual(
            overflow_trace["tool_params"]["largest_evidence_items"][0]["label"],
            "tool:network.get_rdma_stats#1",
        )

    async def test_state_rebuilt_network_followup_preserves_canonical_sections(self) -> None:
        class _StateRebuiltNetworkLLM(_ChatContentStrictLLM):
            async def _invoke(self, messages: list[Any], *, tools: list[Any], tool_choice: str) -> AIMessage:
                self._validate_non_empty_chat(messages)
                self.calls.append(
                    {
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": tool_choice,
                    }
                )
                index = len(self.calls) - 1
                joined = "\n".join(_message_text(message) for message in messages if not isinstance(message, SystemMessage))
                if index == 0:
                    return AIMessage(
                        content=json.dumps(
                            {
                                "thought": "Use the builtin network skill first.",
                                "skill_call": {
                                    "skill_id": "builtin-network-diagnosis",
                                    "reason": "This is a direct network RTT alert.",
                                },
                            }
                        )
                    )
                if index == 1:
                    if "Skill execution history:" not in joined or '"skill_id": "builtin-network-diagnosis"' not in joined:
                        raise AssertionError("state-rebuilt prompt lost canonical skill history")
                    if "Tool evidence ledger:" not in joined or '"tool": "k8s.list_pods"' not in joined:
                        raise AssertionError("state-rebuilt prompt lost canonical skill tool evidence")
                    if '"summary": "pods=500; non_running=0; phases=Running:500"' not in joined:
                        raise AssertionError("state-rebuilt prompt lost compact k8s summary")
                    if "pod-499" in joined or '"data": [{' in joined:
                        raise AssertionError("state-rebuilt prompt leaked raw pod payloads")
                    if "Alert context:" not in joined or "NetworkLatencyHigh100ms" not in joined:
                        raise AssertionError("state-rebuilt prompt lost canonical alert context")
                    return AIMessage(
                        content="Collect the RTT metric directly.",
                        tool_calls=[
                            {
                                "name": "prometheus.query_instant",
                                "args": {"promql": 'probe_icmp_duration_seconds{instance="10.11.0.12"}'},
                                "id": "call-state-rebuilt-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                if '"tool": "prometheus.query_instant"' not in joined or '"source": "tool"' not in joined:
                    raise AssertionError("state-rebuilt prompt lost direct tool evidence from the ledger")
                if '"summary": "scalar=1.0"' not in joined:
                    raise AssertionError("state-rebuilt prompt lost compact direct tool summary")
                if "pod-499" in joined or '"data": [{' in joined:
                    raise AssertionError("state-rebuilt prompt leaked raw payloads after follow-up tool call")
                return AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Canonical state kept both skill and direct tool evidence available.",
                            "diagnosis": {
                                "root_cause": "Network RTT remains elevated and needs infrastructure follow-up",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["worker-03", "10.11.0.12"],
                                "confidence": 0.71,
                                "impact_summary": "Every loop used state-rebuilt canonical alert, skill, and tool evidence.",
                                "affected_services": ["network"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                )

        from lib.channels.kubernetes import K8sChannel
        from sre_agent.skills import SkillPolicy, SkillRegistry

        class _HugeK8sClient:
            def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
                _ = label_selector
                return [
                    {"name": f"pod-{idx}", "namespace": namespace, "status": {"phase": "Running"}}
                    for idx in range(500)
                ]

        context = ToolExecutionContext(
            channels={
                "k8s": K8sChannel(client=_HugeK8sClient()),
                "prometheus": _FakePrometheus(),
                "ssh": _FakeSSHChannel(),
            }
        )
        llm = _StateRebuiltNetworkLLM([])
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "builtin-network-diagnosis"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: Network Diagnosis
description: Reproduce the network skill follow-up path in state-rebuilt mode.
---

## Runtime Metadata
```yaml
id: builtin-network-diagnosis
scope: builtin
permissions:
  - read:k8s
tags:
  - network
  - latency
```

## Steps
```yaml
- description: List service pods.
  tool: k8s.list_pods
  params:
    namespace: "${namespace}"
```
""",
                encoding="utf-8",
            )
            result = await run_diagnosis(
                query=(
                    "Diagnose alert 'NetworkLatencyHigh100ms' with severity 'warning'. "
                    "Summary: Network latency average RTT is high (>50ms). "
                    "Description: ICMP average RTT on 10.11.0.12 over the last 1 minute is above 50ms."
                ),
                context=context,
                variables={
                    "namespace": "service",
                    "node": "worker-03",
                    "promql": 'probe_icmp_duration_seconds{instance="10.11.0.12"}',
                },
                llm=llm,
                reasoning_context_strategy="state_rebuilt",
                reasoning_overflow_behavior="fail",
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["k8s.list_pods", "prometheus.query_instant"],
                skill_registry=SkillRegistry(root=skill_root),
                skill_policy=SkillPolicy(),
            )
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["tool_runs"][0]["source"], "skill")
        self.assertEqual(result["tool_runs"][1]["source"], "tool")
        self.assertEqual(result["tool_runs"][0]["skill_id"], "builtin-network-diagnosis")
        self.assertEqual(result["tool_runs"][0]["prompt_summary"], "pods=500; non_running=0; phases=Running:500")
        self.assertTrue(result["tool_runs"][0]["artifact_ref"].startswith("session:"))
        self.assertEqual(result["llm_interactions"][2]["prompt_metadata"]["prompt_mode"], "state_rebuilt_full")
        self.assertEqual(result["llm_interactions"][3]["prompt_metadata"]["prompt_mode"], "state_rebuilt_full")

    async def test_state_rebuilt_rdma_tool_prompt_uses_summary_and_preserves_raw_artifact(self) -> None:
        class _HugeRDMAFollowupLLM(_ChatContentStrictLLM):
            async def _invoke(self, messages: list[Any], *, tools: list[Any], tool_choice: str) -> AIMessage:
                self._validate_non_empty_chat(messages)
                self.calls.append(
                    {
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": tool_choice,
                    }
                )
                index = len(self.calls) - 1
                joined = "\n".join(_message_text(message) for message in messages if not isinstance(message, SystemMessage))
                if index == 0:
                    return AIMessage(
                        content="Inspect RDMA counters directly.",
                        tool_calls=[
                            {
                                "name": "network.get_rdma_stats",
                                "args": {"node": "worker-01"},
                                "id": "call-rdma-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                if "rdma_hotspots=" not in joined:
                    raise AssertionError("state-rebuilt prompt lost compact rdma summary")
                if "session:" not in joined or ":tool:tool:1:network.get_rdma_stats" not in joined:
                    raise AssertionError("state-rebuilt prompt lost rdma artifact reference")
                if "raw-rdma-dump-begin" in joined or '"data": {' in joined:
                    raise AssertionError("state-rebuilt prompt leaked raw rdma payload")
                return AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Compact RDMA evidence is enough for follow-up reasoning.",
                            "diagnosis": {
                                "root_cause": "RDMA retransmit errors are elevated on worker-01",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["worker-01"],
                                "confidence": 0.69,
                                "impact_summary": "Reasoning used a compact RDMA summary while raw artifacts stayed in state.",
                                "affected_services": ["network"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                )

        class _HugeRDMAChannel:
            async def run_command(self, node: str, command: str, use_sudo: bool = False) -> Any:
                _ = node, command, use_sudo

                class Result:
                    success = True
                    output = "raw-rdma-dump-begin\n" + "\n".join(
                        [f"mlx5_error_counter_{idx}={idx}" for idx in range(1, 280)]
                        + [f"rx_packets_{idx}={idx}" for idx in range(1, 280)]
                    )
                    error = ""

                return Result()

        llm = _HugeRDMAFollowupLLM([])
        context = ToolExecutionContext(
            channels={
                "ssh": _HugeRDMAChannel(),
                "prometheus": _FakePrometheus(),
            }
        )
        result = await run_diagnosis(
            query="Diagnose RDMA latency spikes on worker-01.",
            context=context,
            variables={"node": "worker-01"},
            llm=llm,
            reasoning_context_strategy="state_rebuilt",
            reasoning_overflow_behavior="fail",
            reasoning_input_target_tokens=180000,
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            checkpoint_dir=None,
            allowed_tool_names=["network.get_rdma_stats"],
        )
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["tool_runs"][0]["tool"], "network.get_rdma_stats")
        self.assertIn("raw-rdma-dump-begin", result["tool_runs"][0]["data"]["output"])
        self.assertTrue(result["tool_runs"][0]["prompt_summary"].startswith("rdma_hotspots="))
        self.assertEqual(
            result["tool_runs"][0]["artifact_ref"],
            f"session:{result['session_id']}:tool:tool:1:network.get_rdma_stats",
        )
        prompt_metadata = result["llm_interactions"][1]["prompt_metadata"]
        self.assertEqual(prompt_metadata["prompt_mode"], "state_rebuilt_full")
        self.assertFalse(prompt_metadata["provider_invocation_skipped"])

    async def test_react_can_select_and_execute_skill_before_concluding(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "This looks like a repeated Prometheus triage pattern, so use a reusable skill first.",
                            "skill_call": {
                                "skill_id": "custom-prometheus-triage",
                                "reason": "The alert only needs a quick Prometheus confirmation before concluding.",
                            },
                        }
                    )
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The skill evidence confirms the metric is healthy enough to conclude.",
                            "diagnosis": {
                                "root_cause": "No active platform issue detected",
                                "root_cause_layer": "platform",
                                "root_cause_entities": ["cluster:default"],
                                "confidence": 0.82,
                                "impact_summary": "Skill-collected evidence does not show an active issue.",
                                "affected_services": ["platform"],
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
            skill_dir = skill_root / "prometheus-triage"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: Prometheus Triage
description: Confirm a single Prometheus metric before broader diagnosis.
---

## Runtime Metadata
```yaml
id: custom-prometheus-triage
scope: custom
permissions:
  - read:metrics
tags:
  - prometheus
  - metric
```

## Steps
```yaml
- description: Query Prometheus metric.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
```
""",
                encoding="utf-8",
            )
            from sre_agent.skills import SkillPolicy, SkillRegistry

            result = await run_diagnosis(
                query="Use prometheus metric triage for this platform alert.",
                context=_happy_context(),
                variables={"promql": "up"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["prometheus.query_instant"],
                skill_registry=SkillRegistry(root=skill_root),
                skill_policy=SkillPolicy(),
            )
            self.assertEqual(result["status"], "diagnosed")
            self.assertEqual(result["tool_runs"][0]["tool"], "prometheus.query_instant")
            self.assertEqual(result["skill_catalog"], ["custom-prometheus-triage"])
            self.assertIsNone(result["selected_skill_id"])
            self.assertTrue(any(item.get("mode") == "skill_execution" for item in result["llm_interactions"]))
            self.assertTrue(
                any(
                    item.get("action") == "tool_call" and item.get("tool_params", {}).get("kind") == "skill"
                    for item in result["trace_items"]
                    if item.get("type") == "thought"
                )
            )

    async def test_first_turn_runs_skill_selection_subround_when_skill_match_is_strong(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "This vLLM latency alert strongly matches the reusable skill.",
                            "skill_call": {
                                "skill_id": "builtin-vllm-diagnosis",
                                "reason": "The alert semantics align strongly with the vLLM latency playbook.",
                            },
                        }
                    )
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The skill evidence is sufficient to conclude.",
                            "diagnosis": {
                                "root_cause": "Reusable vLLM diagnosis skill completed triage",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.8,
                                "impact_summary": "The reusable skill gathered enough evidence to conclude.",
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
        from sre_agent.skills import SkillPolicy, SkillRegistry

        result = await run_diagnosis(
            query=(
                "alertname VLLMInterTokenLatencyP95High "
                "summary vLLM inter-token latency p95 is high "
                "service qwen3-32b-fp8-202602261 topology inference_service gpu"
            ),
            context=_happy_context(),
            variables={"promql": "up", "namespace": "service", "node": "worker-01"},
            llm=llm,
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            allowed_tool_names=["prometheus.query_instant", "k8s.list_pods", "gpu.get_metrics"],
            skill_registry=SkillRegistry(),
            skill_policy=SkillPolicy(),
        )
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(llm.calls[0]["tool_choice"], "none")
        self.assertTrue(any(item.get("mode") == "skill_selection" for item in result["llm_interactions"]))
        self.assertTrue(any(item.get("mode") == "skill_execution" for item in result["llm_interactions"]))

    async def test_react_infers_skill_selection_from_free_form_text_before_tool_calls(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=(
                        "This alert matches the reusable skill builtin-vllm-diagnosis. "
                        "I will select that skill first before making direct tool calls."
                    ),
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
                            "thought": "The skill evidence confirms a stable diagnosis.",
                            "diagnosis": {
                                "root_cause": "vLLM latency triage completed via reusable skill",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["service:test"],
                                "confidence": 0.8,
                                "impact_summary": "Reusable skill gathered enough evidence to conclude the issue.",
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
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: vLLM Latency Diagnosis
description: Diagnose vLLM latency issues with a reusable skill.
---

## Runtime Metadata
```yaml
id: builtin-vllm-diagnosis
scope: builtin
permissions:
  - read:metrics
tags:
  - vllm
  - latency
  - gpu
```

## Steps
```yaml
- description: Query Prometheus metric.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
```
""",
                encoding="utf-8",
            )
            from sre_agent.skills import SkillPolicy, SkillRegistry

            result = await run_diagnosis(
                query="VLLMInterTokenLatencyP95High alert for vllm service latency issue.",
                context=_happy_context(),
                variables={"promql": "up"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["prometheus.query_instant"],
                skill_registry=SkillRegistry(root=skill_root),
                skill_policy=SkillPolicy(),
            )
            self.assertEqual(result["status"], "diagnosed")
            self.assertEqual(result["tool_runs"][0]["tool"], "prometheus.query_instant")
            self.assertTrue(any(item.get("mode") == "skill_execution" for item in result["llm_interactions"]))

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
            self.assertIn("response_message", result["llm_interactions"][0])
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

    async def test_skill_selection_compatibility_error_fails_open_and_continues_reasoning(self) -> None:
        llm = _FirstCallErrorLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Fallback reasoning after skill selection degradation.",
                            "diagnosis": {
                                "root_cause": "Gateway returned incompatible response format during skill selection",
                                "root_cause_layer": "platform",
                                "root_cause_entities": [],
                                "confidence": 0.52,
                                "impact_summary": "Diagnosis continued without reusable skill selection.",
                                "affected_services": ["platform"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "ambiguous",
                            },
                            "remediation_plan": None,
                        }
                    )
                )
            ],
            error=TypeError("Received response with null value for 'choices'."),
        )
        from sre_agent.skills import SkillPolicy, SkillRegistry

        result = await run_diagnosis(
            query=(
                "alertname VLLMInterTokenLatencyP95High "
                "summary vLLM inter-token latency p95 is high "
                "service qwen3-32b-fp8-202602261 topology inference_service gpu"
            ),
            context=_happy_context(),
            variables={"promql": "up", "namespace": "service", "node": "worker-01"},
            llm=llm,
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            allowed_tool_names=["prometheus.query_instant", "k8s.list_pods", "gpu.get_metrics"],
            skill_registry=SkillRegistry(),
            skill_policy=SkillPolicy(),
        )
        self.assertEqual(result["status"], "diagnosed")
        self.assertTrue(result["skill_selection_attempted"])
        self.assertIsNone(result["selected_skill_id"])
        self.assertTrue(
            any(
                item.get("type") == "thought"
                and item.get("tool_params", {}).get("kind") == "skill_selection_degraded"
                for item in result["trace_items"]
            )
        )

    async def test_skill_selection_non_compatibility_error_remains_failed(self) -> None:
        llm = _FirstCallErrorLLM([], error=RuntimeError("network timeout during skill selection"))
        from sre_agent.skills import SkillPolicy, SkillRegistry

        result = await run_diagnosis(
            query=(
                "alertname VLLMInterTokenLatencyP95High "
                "summary vLLM inter-token latency p95 is high "
                "service qwen3-32b-fp8-202602261 topology inference_service gpu"
            ),
            context=_happy_context(),
            variables={"promql": "up", "namespace": "service", "node": "worker-01"},
            llm=llm,
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            allowed_tool_names=["prometheus.query_instant", "k8s.list_pods", "gpu.get_metrics"],
            skill_registry=SkillRegistry(),
            skill_policy=SkillPolicy(),
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("skill selection step failed", str(result["summary"]))
        self.assertIn("network timeout during skill selection", str(result["summary"]))

    async def test_no_skills_path_is_unchanged_and_reasoning_still_completes(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "No reusable skills are available; proceed with direct diagnosis reasoning.",
                            "diagnosis": {
                                "root_cause": "No active platform issue detected",
                                "root_cause_layer": "platform",
                                "root_cause_entities": [],
                                "confidence": 0.83,
                                "impact_summary": "Health signals are stable and no incident is active.",
                                "affected_services": ["platform"],
                                "triage_priority": "P3",
                                "diagnosis_certainty": "probable",
                            },
                            "remediation_plan": None,
                        }
                    )
                )
            ]
        )
        from sre_agent.skills import SkillPolicy, SkillRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            empty_skill_root = Path(tmpdir) / "empty-skills"
            empty_skill_root.mkdir(parents=True, exist_ok=True)
            result = await run_diagnosis(
                query="Investigate current platform status.",
                context=_happy_context(),
                variables={},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                allowed_tool_names=["prometheus.query_instant"],
                skill_registry=SkillRegistry(root=empty_skill_root),
                skill_policy=SkillPolicy(),
            )
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["skill_catalog"], [])
        self.assertIsNone(result["selected_skill_id"])
        self.assertTrue(result["skill_selection_attempted"])

    async def test_skill_missing_variable_degrades_and_falls_back_to_reasoning(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Select the network skill first for this latency alert.",
                            "skill_call": {
                                "skill_id": "custom-network-check",
                                "reason": "This alert should run reusable network checks before free-form reasoning.",
                            },
                        }
                    )
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Skill execution degraded due to missing variable; continue with general diagnosis.",
                            "diagnosis": {
                                "root_cause": "Likely transient network jitter; tc injection evidence unavailable in current context",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["worker-01"],
                                "confidence": 0.44,
                                "impact_summary": "Diagnosis continued despite missing runtime variable for reusable skill.",
                                "affected_services": ["network"],
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
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "network-check"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: Network Check
description: Reusable network checks for latency alerts.
---

## Runtime Metadata
```yaml
id: custom-network-check
scope: custom
permissions:
  - read:metrics
  - read:network
tags:
  - network
  - latency
```

## Steps
```yaml
- description: Query network latency metric.
  tool: prometheus.query_instant
  params:
    promql: "${promql}"
- description: Check rdma status.
  tool: network.get_rdma_stats
  params:
    node: "${node}"
```
""",
                encoding="utf-8",
            )
            from sre_agent.skills import SkillPolicy, SkillRegistry

            result = await run_diagnosis(
                query=(
                    "alertname NetworkLatencyHigh100ms "
                    "summary network latency is high on rdma path "
                    "instance 10.11.0.12 phase rtt"
                ),
                context=_happy_context(),
                variables={"node": "worker-01", "namespace": "service"},
                llm=llm,
                step_timeout_sec=5.0,
                total_timeout_sec=10.0,
                checkpoint_dir=tmpdir,
                allowed_tool_names=["prometheus.query_instant", "network.get_rdma_stats"],
                skill_registry=SkillRegistry(root=skill_root),
                skill_policy=SkillPolicy(),
            )
        self.assertEqual(result["status"], "diagnosed")
        self.assertTrue(
            any(
                item.get("type") == "thought"
                and item.get("tool_params", {}).get("kind") == "skill_execution_degraded"
                for item in result["trace_items"]
            )
        )

    async def test_realtime_trace_callback_emits_incremental_ws_events(self) -> None:
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
                            "thought": "Prometheus metrics are stable.",
                            "diagnosis": {
                                "root_cause": "No active issue",
                                "root_cause_layer": "platform",
                                "root_cause_entities": ["cluster:default"],
                                "confidence": 0.9,
                                "impact_summary": "No active degradation observed.",
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
        streamed_events: list[dict[str, Any]] = []

        async def _capture(event: dict[str, Any]) -> None:
            streamed_events.append(event)

        result = await run_diagnosis(
            query="Investigate platform health without changing anything.",
            context=_happy_context(),
            variables={"promql": "up"},
            llm=llm,
            step_timeout_sec=5.0,
            total_timeout_sec=10.0,
            checkpoint_dir=None,
            allowed_tool_names=["prometheus.query_instant"],
            trace_callback=_capture,
        )
        self.assertEqual(result["status"], "diagnosed")
        self.assertGreaterEqual(len(streamed_events), 4)
        event_types = [event.get("type") for event in streamed_events[:4]]
        self.assertEqual(event_types, ["tool_call", "tool_result", "thinking_step", "diagnosis_result"])
        for event in streamed_events[:4]:
            self.assertEqual(event.get("session_id"), result["session_id"])

    async def test_prompt_renders_write_tool_reference_without_binding_write_tools(self) -> None:
        registry = build_default_registry()
        prompt = build_system_prompt(registry, allowed_tool_names=["prometheus.query_instant"])
        self.assertIn("prometheus.query_instant", prompt)
        self.assertIn("k8s.apply_manifest", prompt)
        self.assertIn("not callable in this phase", prompt)
        self.assertIn("network.clear_tc_qdisc", prompt)
        self.assertIn("Every remediation step `params` object must explicitly contain all required fields", prompt)
        self.assertIn("tc qdisc/netem cleanup actions", prompt)

        preferred_prompt = build_system_prompt(
            registry,
            allowed_tool_names=["prometheus.query_instant"],
            preferred_skill={
                "id": "builtin-vllm-diagnosis",
                "summary": "Diagnose vLLM latency incidents by checking latency metrics, namespace pod state, and node GPU utilization.",
                "match_score": 0.0246,
            },
        )
        self.assertIn("Current turn guidance:", preferred_prompt)
        self.assertIn("prefer returning a `skill_call`", preferred_prompt)

        llm = _FakeLLM(
            [
                AIMessage(
                    content="Use Prometheus.",
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
                            "thought": "No action required.",
                            "diagnosis": {
                                "root_cause": "No issue",
                                "root_cause_layer": "platform",
                                "root_cause_entities": [],
                                "confidence": 0.9,
                                "impact_summary": "No issue found.",
                                "affected_services": [],
                                "triage_priority": "P3",
                                "diagnosis_certainty": "confirmed",
                            },
                            "remediation_plan": None,
                        }
                    )
                ),
            ]
        )
        await run_diagnosis(
            query="Read only diagnosis.",
            context=_happy_context(),
            variables={"promql": "up"},
            llm=llm,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        bound_tool_names = [tool.name for tool in llm.calls[0]["tools"]]
        self.assertEqual(bound_tool_names, ["prometheus.query_instant"])

    async def test_valid_remediation_plan_shape_is_captured_but_not_executed(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "Evidence already indicates a configuration issue.",
                            "diagnosis": {
                                "root_cause": "RDMA queue pair mismatch",
                                "root_cause_layer": "network",
                                "root_cause_entities": ["node:worker-01"],
                                "confidence": 0.86,
                                "impact_summary": "Observed signs are consistent with RDMA misconfiguration.",
                                "affected_services": ["rdma"],
                                "triage_priority": "P1",
                                "diagnosis_certainty": "confirmed",
                            },
                            "remediation_plan": {
                                "plan_id": "plan-rdma-1",
                                "root_cause": "RDMA queue pair mismatch",
                                "description": "Proposed recovery plan only.",
                                "steps": [
                                    {
                                        "step_id": 1,
                                        "description": "Cordon node before maintenance.",
                                        "tool": "k8s.cordon_node",
                                        "params": {"node": "worker-01"},
                                        "verification": {
                                            "method": "wait",
                                            "wait_seconds": 30,
                                        },
                                        "timeout": 60,
                                    }
                                ],
                                "estimated_impact": "One node temporarily unavailable.",
                                "confidence": 0.8,
                                "priority": "P1",
                                "safety_level": "high",
                            },
                        }
                    )
                )
            ]
        )
        result = await run_diagnosis(
            query="Diagnose only and propose a fix.",
            context=_happy_context(),
            variables={},
            llm=llm,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "diagnosed")
        plan = RemediationPlan.model_validate(result["remediation_plan"])
        self.assertEqual(plan.steps[0].tool, "k8s.cordon_node")
        self.assertEqual(result["tool_runs"], [])

    async def test_diagnosis_hypotheses_are_padded_to_at_least_three(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "A single dominant hypothesis is present.",
                            "diagnosis": {
                                "root_cause": "Rogue gpu_burn process on worker-03",
                                "root_cause_layer": "platform",
                                "root_cause_entities": ["worker-03", "gpu_burn"],
                                "confidence": 0.95,
                                "hypotheses": [
                                    {
                                        "description": "Rogue gpu_burn process on worker-03",
                                        "status": "confirmed",
                                        "evidence_for": ["gpu process list contains fi_gpu_burn"],
                                        "evidence_against": [],
                                        "confidence": 0.95,
                                    }
                                ],
                                "impact_summary": "GPU contention increased latency.",
                                "affected_services": ["qwen"],
                                "triage_priority": "P0",
                                "diagnosis_certainty": "confirmed",
                            },
                            "remediation_plan": None,
                        }
                    )
                )
            ]
        )
        result = await run_diagnosis(
            query="Diagnose the latency issue with read-only tools.",
            context=_happy_context(),
            variables={},
            llm=llm,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )
        diagnosis = DiagnosisResult.model_validate(result["diagnosis_result"])
        self.assertGreaterEqual(len(diagnosis.hypotheses), 3)
        self.assertEqual(diagnosis.hypotheses[0].description, "Rogue gpu_burn process on worker-03")

    async def test_partial_remediation_plan_is_normalized_into_valid_schema(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The crash loop is clear enough to propose a conservative recovery action.",
                            "diagnosis": {
                                "root_cause": "CrashLoopBackOff in cpu-nginx pod",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["cpu-nginx-cf8c8c75b-2zlgj"],
                                "confidence": 0.88,
                                "impact_summary": "The service is unstable because the pod is restarting repeatedly.",
                                "affected_services": ["cpu-nginx"],
                                "triage_priority": "P1",
                                "diagnosis_certainty": "confirmed",
                            },
                            "remediation_plan": {
                                "actions": [
                                    {
                                        "description": "Restart the failing pod in a controlled manner.",
                                        "tool": "k8s.delete_pod",
                                        "params": {
                                            "namespace": "default",
                                            "pod_name": "cpu-nginx-cf8c8c75b-2zlgj",
                                        },
                                    }
                                ]
                            },
                        }
                    )
                )
            ]
        )

        result = await run_diagnosis(
            query="Diagnose only and propose a fix.",
            context=_happy_context(),
            variables={},
            llm=llm,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )

        self.assertEqual(result["status"], "diagnosed")
        plan = RemediationPlan.model_validate(result["remediation_plan"])
        self.assertEqual(plan.plan_id[:9], "proposal-")
        self.assertEqual(plan.root_cause, "CrashLoopBackOff in cpu-nginx pod")
        self.assertEqual(plan.priority, "P1")
        self.assertEqual(plan.steps[0].step_id, 1)
        self.assertEqual(plan.steps[0].verification.method, "wait")
        self.assertEqual(plan.steps[0].verification.wait_seconds, 30)
        self.assertEqual(
            result["diagnosis_result"]["recommended_fix"]["plan_id"],
            result["remediation_plan"]["plan_id"],
        )

    async def test_delete_pod_pod_selector_is_normalized_to_label_selector(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The contention source is isolated enough to propose a single cleanup action.",
                            "diagnosis": {
                                "root_cause": "GPU contention from test pod",
                                "root_cause_layer": "platform",
                                "root_cause_entities": ["worker-03", "gpu:0"],
                                "confidence": 0.9,
                                "impact_summary": "A test pod is consuming GPU resources and degrading inference.",
                                "affected_services": ["qwen3"],
                                "triage_priority": "P1",
                                "diagnosis_certainty": "confirmed",
                            },
                            "remediation_plan": {
                                "actions": [
                                    {
                                        "description": "Remove the test pod that created GPU contention.",
                                        "tool": "k8s.delete_pod",
                                        "params": {
                                            "namespace": "service",
                                            "pod_selector": "app=fi-gpu-burn-gpu-contention",
                                        },
                                    }
                                ]
                            },
                        }
                    )
                )
            ]
        )

        result = await run_diagnosis(
            query="Diagnose only and propose a fix.",
            context=_happy_context(),
            variables={},
            llm=llm,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )

        self.assertEqual(result["status"], "diagnosed")
        plan = RemediationPlan.model_validate(result["remediation_plan"])
        self.assertEqual(plan.steps[0].tool, "k8s.delete_pod")
        self.assertEqual(plan.steps[0].params["namespace"], "service")
        self.assertEqual(plan.steps[0].params["label_selector"], "app=fi-gpu-burn-gpu-contention")
        self.assertNotIn("pod_selector", plan.steps[0].params)

    async def test_missing_remediation_plan_is_still_allowed(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thought": "The evidence is not strong enough to safely recommend a write action.",
                            "diagnosis": {
                                "root_cause": "Possible transient service instability",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["pod-a"],
                                "confidence": 0.74,
                                "impact_summary": "Signals suggest an issue but not enough to propose a safe remediation.",
                                "affected_services": ["service-a"],
                                "triage_priority": "P2",
                                "diagnosis_certainty": "ambiguous",
                            },
                            "remediation_plan": None,
                        }
                    )
                )
            ]
        )

        result = await run_diagnosis(
            query="Diagnose only and propose a fix if safe.",
            context=_happy_context(),
            variables={},
            llm=llm,
            allowed_tool_names=["prometheus.query_instant"],
            checkpoint_dir=None,
        )

        self.assertEqual(result["status"], "diagnosed")
        self.assertIsNone(result["remediation_plan"])
        self.assertIsNone(result["diagnosis_result"]["recommended_fix"])

    async def test_step_timeout_returns_timeout_state(self) -> None:
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

    async def test_tool_failure_propagates_into_graph_state(self) -> None:
        llm = _FakeLLM(
            [
                AIMessage(
                    content="Need to inspect pods.",
                    tool_calls=[
                        {
                            "name": "k8s.list_pods",
                            "args": {"namespace": "default"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
        bad_context = ToolExecutionContext(
            channels={
                "k8s": _FakeK8sClient(),
                "prometheus": _FakePrometheus(),
                "ssh": _FakeSSHChannel(),
            }
        )
        result = await run_diagnosis(
            query="Inspect pods.",
            context=bad_context,
            variables={},
            llm=llm,
            allowed_tool_names=["k8s.list_pods"],
            checkpoint_dir=None,
        )
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["tool_runs"])
        self.assertFalse(result["tool_runs"][0]["success"])


class TestDiagnosisLocalization(unittest.TestCase):
    def test_hypothesis_defaults_use_chinese_descriptions(self) -> None:
        hypotheses = _normalize_hypotheses_payload(
            {
                "root_cause": "GPU 争用",
                "root_cause_layer": "platform",
                "impact_summary": "auth-svc 的 p95 时延持续升高",
                "confidence": 0.82,
                "diagnosis_certainty": "probable",
            }
        )

        descriptions = [item["description"] for item in hypotheses]
        self.assertIn("GPU 争用", descriptions)
        self.assertIn("网络或 RDMA 退化导致时延升高", descriptions)
        self.assertIn("常规工作负载上涨或 service 侧饱和", descriptions)

    def test_remediation_defaults_use_chinese_descriptions(self) -> None:
        diagnosis = DiagnosisResult.model_validate(
            {
                "root_cause": "worker-03 节点 GPU 争用",
                "root_cause_layer": "platform",
                "root_cause_entities": ["worker-03", "gpu-0"],
                "confidence": 0.91,
                "hypotheses": [],
                "impact_summary": "auth-svc 时延升高，吞吐下降",
                "affected_services": ["auth-svc"],
                "triage_priority": "P1",
                "diagnosis_certainty": "confirmed",
            }
        )

        plan = _normalize_remediation_plan_payload(
            raw_plan={
                "steps": [
                    {
                        "tool": "k8s.cordon_node",
                        "params": {"node": "worker-03"},
                    }
                ]
            },
            diagnosis=diagnosis,
            session_id="sess-localize-1",
            registry=build_default_registry(),
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(
            plan.description,
            "基于现有诊断证据生成的 proposal-only 修复方案，当前尚未执行任何写入动作。",
        )
        self.assertEqual(
            plan.steps[0].description,
            "针对 worker-03 节点 GPU 争用 的候选修复步骤 1",
        )

    def test_remediation_normalization_infers_tc_qdisc_node_and_iface(self) -> None:
        diagnosis = DiagnosisResult.model_validate(
            {
                "root_cause": "worker-03 节点 netem delay 残留",
                "root_cause_layer": "network",
                "root_cause_entities": ["10.11.0.12", "worker-03"],
                "confidence": 0.86,
                "hypotheses": [],
                "impact_summary": "roce 网卡残留 netem delay 规则导致时延升高",
                "affected_services": ["network"],
                "triage_priority": "P1",
                "diagnosis_certainty": "confirmed",
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            inventory = Path(tmpdir) / "inventory.yaml"
            inventory.write_text(
                """
inventory:
  workers:
    - name: worker-03
      ssh:
        host: 10.11.0.12
""".strip(),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SRE_SSH_INVENTORY_PATH": str(inventory)}, clear=False):
                plan = _normalize_remediation_plan_payload(
                    raw_plan={
                        "steps": [
                            {
                                "tool": "network.clear_tc_qdisc",
                                "description": "使用 SSH 在节点 10.11.0.12 上执行 tc qdisc del dev roce parent 8016:10，移除 netem delay 规则",
                                "params": {},
                            }
                        ]
                    },
                    diagnosis=diagnosis,
                    session_id="sess-netem-1",
                    registry=build_default_registry(),
                )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.steps[0].params["node"], "worker-03")
        self.assertEqual(plan.steps[0].params["iface"], "roce")
        self.assertEqual(plan.steps[0].params["parent"], "8016:10")

    def test_remediation_normalization_rejects_tc_qdisc_step_with_wrong_tool(self) -> None:
        diagnosis = DiagnosisResult.model_validate(
            {
                "root_cause": "worker-03 节点 netem delay 残留",
                "root_cause_layer": "network",
                "root_cause_entities": ["10.11.0.12", "worker-03"],
                "confidence": 0.86,
                "hypotheses": [],
                "impact_summary": "roce 网卡残留 netem delay 规则导致时延升高",
                "affected_services": ["network"],
                "triage_priority": "P1",
                "diagnosis_certainty": "confirmed",
            }
        )
        plan = _normalize_remediation_plan_payload(
            raw_plan={
                "steps": [
                    {
                        "tool": "kill_process",
                        "description": "使用 SSH 在节点 10.11.0.12 上执行 tc qdisc del dev roce parent 8016:10，移除 netem delay 规则",
                        "params": {},
                    }
                ]
            },
            diagnosis=diagnosis,
            session_id="sess-netem-2",
            registry=build_default_registry(),
        )

        self.assertIsNone(plan)


if __name__ == "__main__":
    unittest.main()
