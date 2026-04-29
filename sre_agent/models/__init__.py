"""Stable shared model contracts for the SRE agent subsystem."""

from sre_agent.models.alert import Alert, AlertSeverity, AlertStatus
from sre_agent.models.common import ErrorCode, SREError, SREResponse, SafetyLevel, StrictFrozenModel
from sre_agent.models.diagnosis import (
    DiagnosedRootCause,
    DiagnosisResult,
    DiagnosisSession,
    Hypothesis,
    Observation,
    PropagationStep,
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
    rebuild_remediation_models,
)

# Resolve cross-module forward refs after both diagnosis/remediation symbols are imported.
rebuild_remediation_models(DiagnosedRootCause)

__all__ = [
    "StrictFrozenModel",
    "ErrorCode",
    "SafetyLevel",
    "SREError",
    "SREResponse",
    "AlertSeverity",
    "AlertStatus",
    "Alert",
    "ThinkingStep",
    "Observation",
    "ThinkingTrace",
    "Hypothesis",
    "PropagationStep",
    "DiagnosedRootCause",
    "DiagnosisResult",
    "DiagnosisSession",
    "VerificationCondition",
    "VerificationConfig",
    "CanaryCondition",
    "CanaryConfig",
    "RemediationAction",
    "RemediationStep",
    "RemediationPlan",
    "RemediationResult",
    "CandidateAttempt",
    "LoopResult",
    "EntityType",
    "RelationType",
    "OntologyNode",
    "OntologyEdge",
    "Relationship",
    "IncidentRecord",
    "LearnedPattern",
    "ConfigBaseline",
    "EventType",
    "WSEvent",
]
