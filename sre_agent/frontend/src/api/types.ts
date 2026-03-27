export type Severity = "critical" | "warning" | "info";
export type AlertStatus = "firing" | "resolved" | "silenced";
export type EventType =
  | "thinking_step"
  | "tool_call"
  | "tool_result"
  | "diagnosis_result"
  | "approval_required"
  | "loop_start"
  | "loop_progress"
  | "remediation_progress"
  | "alert"
  | "error"
  | "done";

export type Alert = {
  alert_name: string;
  severity: Severity;
  labels: Record<string, string>;
  annotations: Record<string, string>;
  starts_at: string;
  ends_at?: string | null;
  fingerprint: string;
  status: AlertStatus;
  source?: string | null;
};

export type AlertCluster = {
  cluster_id: string;
  summary: string;
  severity: Severity;
  alerts: string[];
};

export type OntologyNode = {
  id: string;
  entity_type: string;
  name?: string | null;
  properties: Record<string, unknown>;
  status?: string | null;
  updated_at: string;
};

export type OntologyEdge = {
  source_id: string;
  target_id: string;
  relation: string;
  properties: Record<string, unknown>;
};

export type TopologySnapshot = {
  nodes: OntologyNode[];
  edges: OntologyEdge[];
  active_alerts: number;
  recent_events: string[];
};

export type SREApiEnvelope<T> = {
  success: boolean;
  data: T | null;
  error?: {
    code: string;
    message: string;
    details?: unknown;
    trace_id?: string;
  } | null;
  trace_id: string;
  timestamp: string;
};

export type ThinkingStep = {
  step: number;
  timestamp: string;
  thought: string;
  action_type: "tool_call" | "conclude" | "remediate";
  tool_name?: string | null;
  tool_params?: Record<string, unknown> | null;
  confidence?: number | null;
};

export type Observation = {
  tool: string;
  params: Record<string, unknown>;
  result: Record<string, unknown>;
  timestamp: string;
};

export type Hypothesis = {
  description: string;
  status: "testing" | "confirmed" | "eliminated";
  evidence_for: string[];
  evidence_against: string[];
  confidence: number;
};

export type DiagnosisResult = {
  root_cause: string;
  root_cause_layer: string;
  root_cause_entities: string[];
  confidence: number;
  hypotheses: Hypothesis[];
  impact_summary: string;
  affected_services: string[];
  triage_priority: "P0" | "P1" | "P2" | "P3";
  diagnosis_certainty: "confirmed" | "probable" | "ambiguous";
};

export type DiagnosisSession = {
  session_id: string;
  alert: Alert;
  status: string;
  diagnosis_result?: DiagnosisResult | null;
  trace?: { steps: Array<ThinkingStep | Observation> } | null;
  duration_seconds: number;
  outcome?: string | null;
};

export type RemediationResult = {
  plan_id: string;
  success: boolean;
  steps_completed: number;
  steps_total: number;
  duration_seconds: number;
  error?: string | null;
};

export type CandidateAttempt = {
  candidate: {
    root_cause: string;
    confidence: number;
  };
  remediation_result: RemediationResult;
  verification_passed: boolean;
  rolled_back: boolean;
  observations?: Record<string, unknown>;
  duration_seconds: number;
};

export type LoopResult = {
  session_id: string;
  outcome: "resolved" | "partially_resolved" | "exhausted" | "escalated" | "re_diagnosed";
  winning_candidate?: {
    root_cause: string;
    confidence: number;
  } | null;
  attempts: CandidateAttempt[];
  total_duration_seconds: number;
  re_diagnosis_context?: Record<string, unknown> | null;
};

export type VerificationConfig = {
  method: "promql" | "tool_call" | "wait";
  query?: string | null;
  tool?: string | null;
  wait_seconds?: number;
};

export type RemediationAction = {
  step_id: number;
  description: string;
  tool: string;
  params: Record<string, unknown>;
  rollback_tool?: string | null;
  verification: VerificationConfig;
  timeout: number;
};

export type CanaryCondition = {
  metric: string;
  operator: string;
  value: string | number;
};

export type RemediationPlan = {
  plan_id: string;
  root_cause: string;
  description: string;
  steps: RemediationAction[];
  canary?: {
    enabled: boolean;
    target_percentage: number;
    monitor_duration: number;
    success_criteria: CanaryCondition[];
  } | null;
  estimated_impact: string;
  confidence: number;
  priority: "P0" | "P1" | "P2";
  safety_level?: string;
};

export type RemediationOverview = {
  session_id: string;
  plan: RemediationPlan;
  progress: {
    status: string;
    completed_steps: number;
    total_steps: number;
    batch_status: Array<{ batch: string; progress: number; status: string }>;
  };
  approval_required: boolean;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  created_at: string;
  tool_name?: string;
  metadata?: Record<string, unknown>;
};

export type KnowledgeDocument = {
  id: string;
  title: string;
  source: string;
  category: string;
  excerpt: string;
  tags: string[];
  score?: number;
};

export type IncidentRecord = {
  incident_id: string;
  aidc_id: string;
  timestamp: string;
  alert: Alert;
  symptoms: string[];
  root_cause: string;
  root_cause_layer: string;
  root_cause_entities: string[];
  outcome: "resolved" | "partially_resolved" | "failed" | "escalated";
  resolution_time_seconds: number;
};

export type LearnedPattern = {
  pattern_id: string;
  aidc_id: string;
  symptom_signature: string[];
  root_cause: string;
  effective_fix: string;
  occurrence_count: number;
  confidence: number;
  first_seen: string;
  last_seen: string;
  example_incidents: string[];
};

export type ConfigBaseline = {
  aidc_id: string;
  version: number;
  metric_baselines: Record<string, string | number>;
  safety_thresholds: Record<string, string | number>;
  custom_rules: Record<string, unknown>;
  updated_at: string;
};

export type SkillDescriptor = {
  id: string;
  name: string;
  scope: "builtin" | "custom";
  summary: string;
  source: string;
  permissions: string[];
  match_score: number;
};

export type WSEvent = {
  schema_version: string;
  type: EventType;
  session_id: string;
  timestamp: string;
  data: Record<string, unknown>;
};
