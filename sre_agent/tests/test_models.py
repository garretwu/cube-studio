from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

import sre_agent.models as models
from sre_agent.models.alert import Alert, AlertSeverity, AlertStatus
from sre_agent.models.common import ErrorCode, SREError, SREResponse, SafetyLevel
from sre_agent.models.diagnosis import (
    DiagnosisResult,
    DiagnosisSession,
    Hypothesis,
    Observation,
    RemediationAlertReview,
    RemediationAlertSnapshot,
    RemediationCheckSnapshot,
    RemediationEvidence,
    RemediationMetricReview,
    RemediationMetricSnapshot,
    PropagationStep,
    RankedRootCause,
    ThinkingStep,
    ThinkingTrace,
)
from sre_agent.models.events import EventType, WSEvent
from sre_agent.models.memory import ConfigBaseline, IncidentRecord, LearnedPattern
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType, Relationship
from sre_agent.models.remediation import (
    CandidateAttempt,
    CanaryCondition,
    CanaryConfig,
    LoopResult,
    RemediationAction,
    RemediationPlan,
    RemediationResult,
    RemediationStep,
    VerificationCondition,
    VerificationConfig,
)


def _dt(hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 3, 17, hour, minute, second, tzinfo=UTC)


def _sample_alert() -> Alert:
    return Alert(
        alert_name="NCCLTimeout",
        severity=AlertSeverity.CRITICAL,
        labels={"alertname": "NCCLTimeout", "instance": "gpu-1-1", "severity": "critical"},
        annotations={"summary": "NCCL timeout", "description": "collective timeout"},
        starts_at=_dt(12, 0, 0),
        ends_at=_dt(12, 5, 0),
        fingerprint="fp-nccl-timeout",
        status=AlertStatus.FIRING,
    )


def _sample_action(step_id: int = 1) -> RemediationAction:
    return RemediationAction(
        step_id=step_id,
        description="Terminate rogue gpu-burn process",
        tool="kill_process",
        params={"node": "gpu-1-1", "pid": 12847},
        rollback_tool="restart_process",
        rollback_params={"node": "gpu-1-1", "name": "gpu-burn"},
        verification=VerificationConfig(
            method="promql",
            query='histogram_quantile(0.95, sum(rate(vllm_request_latency_bucket[1m])) by (le))',
            condition=VerificationCondition(field="value", operator="<", value=500),
        ),
        timeout=120,
    )


def _sample_plan() -> RemediationPlan:
    return RemediationPlan(
        plan_id="plan-001",
        root_cause="GPU contention",
        description="Restore stable inference latency by removing rogue process",
        steps=[_sample_action(1)],
        canary=CanaryConfig(
            enabled=True,
            target_percentage=0.1,
            monitor_duration=120,
            success_criteria=[CanaryCondition(metric="vllm_p95_ms", operator="<", value=500)],
            criteria_mode="all",
            max_batches=3,
        ),
        estimated_impact="Temporary inference jitter during process cleanup",
        confidence=0.9,
        priority="P0",
        safety_level=SafetyLevel.HIGH,
    )


def _sample_diagnosis(plan: RemediationPlan) -> DiagnosisResult:
    candidate = RankedRootCause(
        rank=1,
        root_cause="GPU contention",
        root_cause_layer="hardware",
        root_cause_entities=["node:gpu-1-1", "gpu:gpu-1-1:0"],
        confidence=0.92,
        evidence_summary="gpu-burn occupied GPU-0 with abnormal utilization",
        recommended_fix=plan,
        distinguishing_verification="Kill gpu-burn and verify p95 drops below 500ms",
    )
    return DiagnosisResult(
        root_cause="GPU contention",
        root_cause_layer="hardware",
        root_cause_entities=["node:gpu-1-1", "gpu:gpu-1-1:0"],
        confidence=0.92,
        hypotheses=[
            Hypothesis(
                description="Unmanaged process occupies GPU",
                status="confirmed",
                evidence_for=["process list contains gpu-burn"],
                evidence_against=[],
                confidence=0.92,
            )
        ],
        propagation_chain=[
            PropagationStep(
                entity_id="gpu:gpu-1-1:0",
                entity_type="gpu",
                metric="gpu_util",
                value_before=35.0,
                value_after=98.0,
                description="GPU utilization spike due to rogue process",
            )
        ],
        impact_summary="Inference latency increased due to GPU contention",
        affected_services=["service:vllm"],
        recommended_fix=plan,
        triage_priority="P0",
        ranked_candidates=[candidate],
        diagnosis_certainty="confirmed",
    )


class TestModelsUnit:
    def test_unit_public_exports_are_locked(self) -> None:
        expected = {
            "Alert",
            "AlertSeverity",
            "AlertStatus",
            "DiagnosisResult",
            "DiagnosisSession",
            "RemediationPlan",
            "RemediationAction",
            "LoopResult",
            "OntologyNode",
            "OntologyEdge",
            "IncidentRecord",
            "LearnedPattern",
            "ConfigBaseline",
            "WSEvent",
            "EventType",
            "SREResponse",
            "SREError",
            "ErrorCode",
            "SafetyLevel",
        }
        assert expected.issubset(set(models.__all__))

    def test_unit_alias_parsing_for_alertmanager_payload(self) -> None:
        payload = {
            "labels": {"alertname": "NodeDown", "severity": "warning", "instance": "gpu-1-2"},
            "annotations": {"summary": "node down"},
            "startsAt": _dt(13, 0, 0).isoformat(),
            "endsAt": _dt(13, 1, 0).isoformat(),
            "fingerprint": "fp-node-down",
            "status": {"state": "firing"},
        }
        alert = Alert.model_validate(payload)
        assert alert.alert_name == "NodeDown"
        assert alert.severity == AlertSeverity.WARNING
        assert alert.status == AlertStatus.FIRING

    def test_unit_alias_parsing_for_ws_payload_field(self) -> None:
        event = WSEvent.model_validate(
            {
                "type": "thinking_step",
                "session_id": "sess-001",
                "payload": {"step": 1, "thought": "collect metrics"},
            }
        )
        assert event.data["step"] == 1

    def test_unit_forbid_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            Alert(
                alert_name="BadAlert",
                severity="critical",
                labels={},
                annotations={},
                starts_at=_dt(),
                fingerprint="fp-bad",
                status="firing",
                unexpected="boom",
            )

    def test_unit_frozen_models_block_mutation(self) -> None:
        alert = _sample_alert()
        with pytest.raises((ValidationError, TypeError)):
            alert.alert_name = "Changed"

    def test_unit_legacy_safety_level_mapping(self) -> None:
        assert SafetyLevel("read_only") == SafetyLevel.READONLY
        assert SafetyLevel("write_confirm") == SafetyLevel.HIGH
        assert SafetyLevel("write_blocked") == SafetyLevel.CRITICAL

    def test_unit_diagnosis_session_compatibility_aliases(self) -> None:
        plan = _sample_plan()
        diagnosis = _sample_diagnosis(plan)
        session = DiagnosisSession.model_validate(
            {
                "session_id": "sess-compat-1",
                "alert": _sample_alert().model_dump(mode="json"),
                "status": "diagnosed",
                "diagnosis": diagnosis.model_dump(mode="json"),
            }
        )
        assert session.diagnosis_result is not None
        assert session.diagnosis is not None
        assert session.proposed_plan is not None
        assert session.proposed_plan.plan_id == plan.plan_id
        assert session.duration_seconds == 0

    def test_unit_validation_invariants(self) -> None:
        with pytest.raises(ValidationError):
            Alert(
                alert_name="bad-time-order",
                severity="critical",
                labels={},
                annotations={},
                starts_at=_dt(14, 0, 0),
                ends_at=_dt(13, 59, 59),
                fingerprint="fp-time-order",
                status="firing",
            )

        with pytest.raises(ValidationError):
            CanaryConfig(
                enabled=True,
                target_percentage=1.1,
                monitor_duration=120,
                success_criteria=[CanaryCondition(metric="p95", operator="<", value=500)],
            )

        with pytest.raises(ValidationError):
            DiagnosisResult(
                root_cause="x",
                root_cause_layer="hardware",
                root_cause_entities=[],
                confidence=0.5,
                hypotheses=[],
                propagation_chain=[],
                impact_summary="x",
                affected_services=[],
                recommended_fix=None,
                triage_priority="P1",
                ranked_candidates=[],
                diagnosis_certainty="confirmed",
            )

    def test_unit_no_shared_mutable_defaults(self) -> None:
        r1 = RemediationResult(
            plan_id="plan-a",
            success=False,
            steps_completed=0,
            steps_total=1,
            error="failed",
        )
        r2 = RemediationResult(
            plan_id="plan-b",
            success=False,
            steps_completed=0,
            steps_total=1,
            error="failed",
        )
        assert r1.verification_results is not r2.verification_results

    @pytest.mark.parametrize(
        "model_instance",
        [
            _sample_alert(),
            _sample_action(1),
            _sample_plan(),
            OntologyNode(id="node-1", entity_type=EntityType.NODE, name="gpu-1-1"),
            OntologyEdge(source_id="node-1", target_id="rack-a", relation=RelationType.PART_OF),
            ConfigBaseline(
                aidc_id="aidc-001",
                metric_baselines={"vllm_p95_ms": 180},
                safety_thresholds={"max_canary_pct": 0.1},
                custom_rules={"block_bmc_write": True},
            ),
            WSEvent(type=EventType.ALERT, session_id="sess-serialize", data={"x": 1}),
            SREError(code=ErrorCode.INTERNAL_ERROR, message="boom"),
        ],
    )
    def test_unit_roundtrip_serialization(self, model_instance: object) -> None:
        json_text = model_instance.model_dump_json()  # type: ignore[attr-defined]
        restored = type(model_instance).model_validate_json(json_text)  # type: ignore[attr-defined]
        assert restored == model_instance


class TestModelsIntegration:
    def test_integration_cross_model_contract_chain(self) -> None:
        alert = _sample_alert()
        plan = _sample_plan()
        diagnosis = _sample_diagnosis(plan)
        trace = ThinkingTrace(
            steps=[
                ThinkingStep(step=1, thought="Inspect GPU metrics", action_type="tool_call", tool_name="get_gpu_metrics"),
                Observation(tool="get_gpu_metrics", params={"node": "gpu-1-1"}, result={"gpu_util": 98}),
            ]
        )
        session = DiagnosisSession(
            session_id="sess-integr-1",
            alert=alert,
            status="diagnosed",
            diagnosis_result=diagnosis,
            trace=trace,
            re_diagnosis_round=0,
            remediation_evidence=RemediationEvidence(
                pre_check=RemediationCheckSnapshot(
                    alert=RemediationAlertSnapshot(
                        fingerprint=alert.fingerprint,
                        alert_name=alert.alert_name,
                        status="firing",
                        is_firing=True,
                    ),
                    metrics=[
                        RemediationMetricSnapshot(
                            metric_key="vllm_p95_ms",
                            query="vllm_request_latency_p95",
                            value=650,
                        )
                    ],
                ),
                post_check=RemediationCheckSnapshot(
                    alert=RemediationAlertSnapshot(
                        fingerprint=alert.fingerprint,
                        alert_name=alert.alert_name,
                        status="resolved",
                        is_firing=False,
                    ),
                    metrics=[
                        RemediationMetricSnapshot(
                            metric_key="vllm_p95_ms",
                            query="vllm_request_latency_p95",
                            value=320,
                        )
                    ],
                ),
                alert_review=RemediationAlertReview(
                    fingerprint=alert.fingerprint,
                    alert_name=alert.alert_name,
                    before_status="firing",
                    after_status="resolved",
                    cleared=True,
                ),
                metric_reviews=[
                    RemediationMetricReview(
                        metric_key="vllm_p95_ms",
                        query="vllm_request_latency_p95",
                        before_value=650,
                        after_value=320,
                        improved=True,
                        available=True,
                    )
                ],
                alert_cleared=True,
                metrics_improved=True,
            ),
        )
        rem_result = RemediationResult(
            plan_id=plan.plan_id,
            success=True,
            steps_completed=1,
            steps_total=1,
            verification_results=[{"metric": "vllm_p95_ms", "value": 320}],
            duration_seconds=95,
        )
        attempt = CandidateAttempt(
            candidate=diagnosis.ranked_candidates[0].model_dump(mode="json"),
            remediation_result=rem_result,
            verification_passed=True,
            rolled_back=False,
            observations={"before": {"p95": 650}, "after": {"p95": 320}},
            duration_seconds=95,
        )
        loop_result = LoopResult(
            session_id=session.session_id,
            outcome="resolved",
            winning_candidate=diagnosis.ranked_candidates[0].model_dump(mode="json"),
            attempts=[attempt],
            total_duration_seconds=95,
        )
        response = SREResponse[LoopResult](success=True, data=loop_result, trace_id="trace-integr-1")
        ws_event = WSEvent(
            type=EventType.DIAGNOSIS_RESULT,
            session_id=session.session_id,
            data={"session": session.model_dump(mode="json"), "response": response.model_dump(mode="json")},
        )

        restored = SREResponse[LoopResult].model_validate_json(response.model_dump_json())
        restored_event = WSEvent.model_validate_json(ws_event.model_dump_json())
        assert restored.success is True
        assert restored.data is not None and restored.data.outcome == "resolved"
        assert restored_event.data["response"]["success"] is True
        assert session.remediation_evidence is not None
        assert session.remediation_evidence.alert_review is not None
        assert session.remediation_evidence.alert_review.cleared is True

    def test_integration_compatibility_aliases_work_across_domains(self) -> None:
        plan = RemediationPlan.model_validate(
            {
                "plan_id": "plan-compat",
                "root_cause": "network jitter",
                "description": "rebalance route weights",
                "actions": [
                    {
                        "step_id": 1,
                        "description": "adjust route",
                        "tool": "update_route",
                        "params": {"switch": "sw-1"},
                        "verification": {"method": "wait", "wait_seconds": 1},
                    }
                ],
                "estimated_impact": "small",
                "confidence": 0.7,
                "priority": "P1",
            }
        )
        edge = Relationship.model_validate({"source": "gpu-1", "target": "sw-1", "type": "connected_to"})
        event = WSEvent.model_validate(
            {"type": "loop_progress", "session_id": "sess-compat", "payload": {"plan": plan.model_dump(mode="json")}}
        )

        assert isinstance(plan.steps[0], RemediationStep)
        assert edge.relation == RelationType.CONNECTED_TO
        assert event.data["plan"]["plan_id"] == "plan-compat"


class TestModelsE2E:
    def test_e2e_confirmed_resolution_flow(self) -> None:
        alert = _sample_alert()
        plan = _sample_plan()
        diagnosis = _sample_diagnosis(plan)

        session = DiagnosisSession.create(alert).model_copy(
            update={
                "status": "diagnosed",
                "diagnosis_result": diagnosis,
                "trace": ThinkingTrace(
                    steps=[ThinkingStep(step=1, thought="confirmed GPU contention", action_type="conclude", confidence=0.92)]
                ),
            }
        )
        rem_result = RemediationResult(
            plan_id=plan.plan_id,
            success=True,
            steps_completed=1,
            steps_total=1,
            verification_results=[{"metric": "vllm_p95_ms", "value": 300}],
            duration_seconds=80,
        )
        loop_result = LoopResult(
            session_id=session.session_id,
            outcome="resolved",
            winning_candidate=diagnosis.ranked_candidates[0].model_dump(mode="json"),
            attempts=[
                CandidateAttempt(
                    candidate=diagnosis.ranked_candidates[0].model_dump(mode="json"),
                    remediation_result=rem_result,
                    verification_passed=True,
                    rolled_back=False,
                    observations={"before": {"p95": 650}, "after": {"p95": 300}},
                    duration_seconds=80,
                )
            ],
            total_duration_seconds=80,
        )
        response = SREResponse[LoopResult](success=True, data=loop_result, trace_id="trace-e2e-confirmed")
        event = WSEvent(type=EventType.DONE, session_id=session.session_id, data=response.model_dump(mode="json"))

        assert response.data is not None and response.data.outcome == "resolved"
        assert event.type == EventType.DONE

    def test_e2e_ambiguous_loop_and_re_diagnosis_flow(self) -> None:
        alert = _sample_alert()
        plan_a = _sample_plan()
        plan_b = _sample_plan().model_copy(update={"plan_id": "plan-002", "root_cause": "ECN mismatch"})
        c1 = RankedRootCause(
            rank=1,
            root_cause="GPU contention",
            root_cause_layer="hardware",
            root_cause_entities=["node:gpu-1-1"],
            confidence=0.66,
            evidence_summary="gpu utilization abnormal",
            recommended_fix=plan_a,
            distinguishing_verification="kill gpu-burn and check p95",
        )
        c2 = RankedRootCause(
            rank=2,
            root_cause="ECN mismatch",
            root_cause_layer="network",
            root_cause_entities=["switch:sw-200g"],
            confidence=0.61,
            evidence_summary="ecn counters abnormal",
            recommended_fix=plan_b,
            distinguishing_verification="fix ecn threshold and check retransmits",
        )
        diagnosis = DiagnosisResult(
            root_cause="GPU contention",
            root_cause_layer="hardware",
            root_cause_entities=["node:gpu-1-1"],
            confidence=0.66,
            hypotheses=[],
            propagation_chain=[],
            impact_summary="multiple candidates still plausible",
            affected_services=["service:vllm"],
            recommended_fix=plan_a,
            triage_priority="P1",
            ranked_candidates=[c1, c2],
            diagnosis_certainty="ambiguous",
        )
        session = DiagnosisSession(
            session_id="sess-e2e-ambiguous",
            alert=alert,
            status="re_diagnosed",
            diagnosis_result=diagnosis,
            trace=ThinkingTrace(
                steps=[
                    ThinkingStep(step=1, thought="first candidate failed", action_type="remediate", confidence=0.66),
                    Observation(tool="verify_p95", params={"window": "2m"}, result={"value": 620}),
                ]
            ),
            re_diagnosis_round=1,
            outcome="re_diagnosed",
        )

        fail_result = RemediationResult(
            plan_id=plan_a.plan_id,
            success=False,
            steps_completed=1,
            steps_total=1,
            failed_step=plan_a.steps[0],
            rolled_back=True,
            verification_results=[{"metric": "vllm_p95_ms", "value": 620}],
            duration_seconds=100,
            error="verification failed",
        )
        loop_result = LoopResult(
            session_id=session.session_id,
            outcome="re_diagnosed",
            attempts=[
                CandidateAttempt(
                    candidate=c1.model_dump(mode="json"),
                    remediation_result=fail_result,
                    verification_passed=False,
                    rolled_back=True,
                    observations={"before": {"p95": 650}, "after": {"p95": 620}},
                    duration_seconds=100,
                )
            ],
            total_duration_seconds=100,
            re_diagnosis_context={
                "failed_candidates": [c1.model_dump(mode="json")],
                "new_observations": {"p95": 620},
            },
        )
        response = SREResponse[LoopResult](success=True, data=loop_result, trace_id="trace-e2e-ambiguous")
        event = WSEvent(type=EventType.LOOP_PROGRESS, session_id=session.session_id, data=response.model_dump(mode="json"))

        assert response.data is not None
        assert response.data.outcome == "re_diagnosed"
        assert event.data["data"]["attempts"][0]["rolled_back"] is True

    def test_e2e_error_flow_with_contract_enforcement(self) -> None:
        error = SREError(
            code=ErrorCode.REMEDIATION_EXECUTION_FAILED,
            message="cannot reach switch controller",
            details={"switch": "sw-200g"},
            trace_id="trace-error-1",
        )
        failed_response = SREResponse[LoopResult](success=False, error=error, trace_id="trace-error-1")
        error_event = WSEvent(
            type=EventType.ERROR,
            session_id="sess-e2e-error",
            data={"error": failed_response.error.model_dump(mode="json"), "trace_id": failed_response.trace_id},
        )

        with pytest.raises(ValidationError):
            SREResponse[LoopResult](success=False, data=None, error=None, trace_id="trace-invalid")

        assert failed_response.success is False
        assert error_event.type == EventType.ERROR
        assert error_event.data["error"]["code"] == ErrorCode.REMEDIATION_EXECUTION_FAILED.value

    def test_e2e_memory_contract_objects(self) -> None:
        alert = _sample_alert()
        plan = _sample_plan()
        diagnosis = _sample_diagnosis(plan)
        incident = IncidentRecord(
            incident_id="inc-001",
            aidc_id="aidc-east-1",
            timestamp=_dt(15, 0, 0),
            alert=alert,
            symptoms=["p95>500", "gpu util skew"],
            diagnosis_trace=[step.to_dict() for step in ThinkingTrace(steps=[ThinkingStep(step=1, thought="collect", action_type="tool_call")]).steps],
            root_cause=diagnosis.root_cause,
            root_cause_layer=diagnosis.root_cause_layer,
            root_cause_entities=diagnosis.root_cause_entities,
            hypotheses_tested=diagnosis.hypotheses,
            remediation_applied=plan,
            outcome="resolved",
            resolution_time_seconds=300,
            engineer_feedback="verified in canary",
            tags=["vllm", "gpu"],
        )
        pattern = LearnedPattern(
            pattern_id="pat-001",
            aidc_id="aidc-east-1",
            symptom_signature=["p95>500", "gpu util skew"],
            root_cause="GPU contention",
            effective_fix="kill rogue gpu-burn",
            occurrence_count=4,
            confidence=0.9,
            first_seen=_dt(10, 0, 0),
            last_seen=_dt(10, 10, 0),
            example_incidents=["inc-001"],
            half_life_days=90.0,
        )
        now = pattern.last_seen + timedelta(days=90)
        decayed = pattern.effective_confidence(now)
        assert incident.outcome == "resolved"
        assert 0.44 <= decayed <= 0.46
        assert pattern.should_suggest(pattern.last_seen) is True
