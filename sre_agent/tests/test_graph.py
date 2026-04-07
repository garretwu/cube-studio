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


class TestGraphUnit(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual(result["status"], "timeout")
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


if __name__ == "__main__":
    unittest.main()
