import type { WSEventType } from "./generated/backend-contract";

export type Severity = "critical" | "warning" | "info";
export type AlertStatus = "firing" | "resolved" | "silenced";
export type EventType = WSEventType;

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
  summary?: string | null;
  description?: string | null;
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
  snapshot_id?: string | null;
  last_synced_at?: string | null;
  sync_state?: "idle" | "syncing" | "ready" | "degraded" | "error";
};

export type TopologyStatus = {
  snapshot_id?: string | null;
  sync_state: "idle" | "syncing" | "ready" | "degraded" | "error";
  mode: string;
  last_synced_at?: string | null;
  last_started_at?: string | null;
  last_error?: string | null;
  scanner_counts: Record<string, { nodes: number; edges: number }>;
};

export type TopologyObjectType = "rack" | "node" | "gpu" | "switch" | "port" | "bmc" | "service" | "pod" | "cluster";
export type TopologyObjectStatus = "healthy" | "abnormal" | "impacted" | "maintenance";
export type TopologyLayer = "physical" | "network" | "compute" | "service";
export type TopologyImpactLevel = "low" | "medium" | "high";

export type TopologySite = {
  id: string;
  name: string;
  region: string;
  zone: string;
  domain: string;
  summary: string;
};

export type TopologyObject = {
  id: string;
  name: string;
  type: TopologyObjectType;
  status: TopologyObjectStatus;
  layer: TopologyLayer;
  domain: string;
  region: string;
  zone: string;
  cluster?: string;
  rack?: string;
  slot?: string;
  summary: string;
  tags: string[];
  updatedAt: string;
  metrics?: Record<string, string | number | null>;
  attributes: Record<string, unknown>;
};

export type TopologyRelation = {
  id: string;
  source: string;
  target: string;
  relationType: "contains" | "runs_on" | "connects_to" | "depends_on" | "uplink_to" | "aggregated";
  status: TopologyObjectStatus;
  isCritical: boolean;
  impactLevel: TopologyImpactLevel;
  label?: string;
  isAggregated?: boolean;
};

export type TopologyPath = {
  id: string;
  entryNodeId: string;
  rootCauseNodeId: string;
  affectedNodeIds: string[];
  edgeIds: string[];
  impactLevel: TopologyImpactLevel;
  status: "active" | "inactive";
  summary: string;
};

export type TopologyExplorerResponse = {
  site: TopologySite;
  nodes: TopologyObject[];
  edges: TopologyRelation[];
  paths: TopologyPath[];
  lastUpdated: string;
  sync_state?: "idle" | "syncing" | "ready" | "degraded" | "error";
  last_error?: string | null;
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

export type AuthErrorKind = "expired" | "invalid_signature" | "missing" | "unknown";

export type AuthTokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: "Bearer";
  expires_at: string;
  refresh_expires_at: string;
  server_boot_id: string;
  auth_error_kind: AuthErrorKind;
};

export type AuthStatusResponse = {
  server_boot_id: string;
  access_token_expires_at?: string | null;
  skew_hint_seconds: number;
  auth_error_kind: AuthErrorKind;
};

export type ThinkingStep = {
  step: number;
  timestamp: string;
  thought: string;
  action_type: "tool_call" | "conclude" | "remediate";
  thought_key?: string | null;
  stage?: string | null;
  tool_name?: string | null;
  tool_params?: Record<string, unknown> | null;
  confidence?: number | null;
  next_action?: string | null;
  thought_duration_sec?: number | null;
};

export type StreamingToolCall = {
  tool: string;
  params: Record<string, unknown>;
  round_id?: string | null;
  round_seq?: number | null;
  thought_key?: string | null;
  run_id?: string | null;
  node?: string | null;
};

export type LiveThinkingBlock = {
  round_id?: string | null;
  round_seq?: number | null;
  stream_seq?: number | null;
  thought_key: string;
  run_id?: string | null;
  node?: string | null;
  timestamp: string;
  content: string;
  status: "thinking" | "completed";
  thought_duration_sec?: number | null;
  next_action?: string | null;
  tool_name?: string | null;
  active_tools: StreamingToolCall[];
};

export type LiveFinalAnswerBlock = {
  id: string;
  timestamp: string;
  content: string;
  status: "streaming" | "completed";
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

export type PropagationStep = {
  entity_id: string;
  entity_type: string;
  metric: string;
  value_before: number | string;
  value_after: number | string;
  description: string;
};

export type RankedRootCause = {
  rank: number;
  root_cause: string;
  root_cause_layer: "hardware" | "network" | "os" | "platform" | "service";
  root_cause_entities?: string[];
  confidence: number;
  evidence_summary: string;
  recommended_fix?: RemediationPlan | null;
  distinguishing_verification?: string | null;
};

export type DiagnosisResult = {
  root_cause: string;
  root_cause_layer: string;
  root_cause_entities: string[];
  confidence: number;
  next_action?: string | null;
  hypotheses: Hypothesis[];
  propagation_chain?: PropagationStep[];
  impact_summary: string;
  affected_services: string[];
  triage_priority: "P0" | "P1" | "P2" | "P3";
  ranked_candidates?: RankedRootCause[];
  diagnosis_certainty: "confirmed" | "probable" | "ambiguous";
  recommended_fix?: RemediationPlan | null;
};

export type DiagnosisBootstrapAlertItem = {
  id?: string;
  alert_name: string;
  severity: Severity;
  source_entity?: string | null;
  starts_at?: string | null;
  summary?: string | null;
};

export type DiagnosisBootstrapImpact = {
  object_count: number;
  service_count: number;
  affected_entities?: string[];
  affected_services?: string[];
  blast_radius_summary?: string | null;
};

export type DiagnosisSessionBootstrap = {
  session_name?: string | null;
  started_at?: string | null;
  related_alerts?: {
    count: number;
    items: DiagnosisBootstrapAlertItem[];
  } | null;
  impact?: DiagnosisBootstrapImpact | null;
};

export type DiagnosisSession = {
  session_id: string;
  alert: Alert;
  status: string;
  diagnosis_result?: DiagnosisResult | null;
  trace?: { steps: Array<ThinkingStep | Observation> } | null;
  bootstrap?: DiagnosisSessionBootstrap | null;
  re_diagnosis_round?: number;
  duration_seconds: number;
  outcome?: string | null;
  remediation_evidence?: RemediationEvidence | null;
};

export type SessionEvent = {
  schema_version: string;
  type: EventType;
  session_id: string;
  timestamp: string;
  data: Record<string, unknown>;
};

/** Data payload for the diagnosis_started event. */
export type DiagnosisStartedData = {
  alert: {
    alert_name: string;
    severity: string;
    labels: Record<string, string>;
    annotations?: Record<string, string>;
    fingerprint?: string;
    summary?: string;
    description?: string;
    source?: string;
    status?: string;
  } | null;
  topology: {
    roots: string[];
    affected_count: number;
    affected_entities: { id: string; type: string; name?: string }[];
    summary: string;
    direct_relations?: Array<{
      source: string;
      target: string;
      target_type: string;
      target_name: string;
      relation: string;
      direction: "in" | "out";
    }>;
  } | null;
  variables: Record<string, unknown>;
  bootstrap_state?: "thinking";
  degraded_start?: boolean;
};

export type SessionSummary = {
  session_id: string;
  status: string;
  alert_name: string;
  severity: Severity;
  fingerprint: string;
  incident_key?: string | null;
  affected_services?: string[];
  outcome?: string | null;
  duration_seconds: number;
  updated_at: string;
};

export type DiagnosisSessionSummary = {
  session_id: string;
  title: string;
  summary: string;
  started_at: string;
  updated_at: string;
  status: string;
  severity: Severity;
  alert_name: string;
  fingerprint?: string | null;
  incident_key?: string | null;
  duration_seconds: number;
  outcome?: string | null;
  triage_priority?: DiagnosisResult["triage_priority"] | null;
  root_cause?: string | null;
  affected_services: string[];
  re_diagnosis_round?: number;
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
  command?: string | null;
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
    max_batches?: number | null;
    success_criteria: CanaryCondition[];
  } | null;
  estimated_impact: string;
  confidence: number;
  priority: "P0" | "P1" | "P2";
  safety_level?: string;
};

export type RemediationAlertSnapshot = {
  fingerprint: string;
  alert_name: string;
  status: string;
  is_firing: boolean;
  collected_at: string;
  available: boolean;
  error?: string | null;
};

export type RemediationMetricSnapshot = {
  metric_key: string;
  query: string;
  value?: string | number | boolean | null;
  condition?: Record<string, unknown> | null;
  collected_at: string;
  available: boolean;
  error?: string | null;
};

export type RemediationCheckSnapshot = {
  alert?: RemediationAlertSnapshot | null;
  metrics: RemediationMetricSnapshot[];
  collected_at: string;
};

export type RemediationAlertReview = {
  fingerprint: string;
  alert_name: string;
  before_status: string;
  after_status: string;
  cleared: boolean;
  reviewed_at: string;
};

export type RemediationMetricReview = {
  metric_key: string;
  query: string;
  before_value?: string | number | boolean | null;
  after_value?: string | number | boolean | null;
  condition?: Record<string, unknown> | null;
  improved: boolean;
  available: boolean;
  error?: string | null;
  reviewed_at: string;
};

export type RemediationEvidence = {
  pre_check?: RemediationCheckSnapshot | null;
  post_check?: RemediationCheckSnapshot | null;
  alert_review?: RemediationAlertReview | null;
  metric_reviews: RemediationMetricReview[];
  alert_cleared?: boolean | null;
  metrics_improved?: boolean | null;
  collected_at: string;
};

export type RemediationOverview = {
  session_id: string;
  plan: RemediationPlan;
  affected_services?: string[];
  plan_version?: number;
  plan_history?: Array<{ version: number; plan_id: string; revised_at?: string; instruction?: string }>;
  progress: {
    status: string;
    completed_steps: number;
    total_steps: number;
    batch_status: Array<{ batch: string; progress: number; status: string }>;
  };
  timeline?: SessionEvent[];
  approval_required: boolean;
  baseline_review?: RemediationEvidence | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  created_at: string;
  tool_name?: string;
  metadata?: Record<string, unknown>;
  display?: ChatDisplayPayload;
};

export type ChatReplyMeta = {
  context_applied?: boolean;
  session_id?: string | null;
  trace_steps_used?: number;
  context_tokens_estimate?: number;
};

export type ChatDisplayPayload = {
  answer: string;
  thinking_raw?: string | null;
  has_thinking?: boolean;
};

export type DiagnosisAuditEventKind =
  | "approval_result"
  | "canary_progress"
  | "execution_progress"
  | "metric_feedback"
  | "alert_recovery"
  | "session_closed";

export type DiagnosisAuditSource =
  | "optimistic"
  | "event"
  | "local_audit";

export type DiagnosisAuditTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "info";

export type DiagnosisLocalAuditRecord = {
  id: string;
  sessionId: string;
  eventKind: DiagnosisAuditEventKind;
  source: DiagnosisAuditSource;
  dedupeKey: string;
  timestamp: string;
  summary: string;
  details: string[];
  statusTone: DiagnosisAuditTone;
  progress?: {
    label: string;
    value: number;
    helper?: string;
  };
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

export type KnowledgeDataset = {
  id: string;
  name: string;
  description?: string;
  document_count?: number;
  word_count?: number;
  created_at?: string | null;
  updated_at?: string | null;
  status?: string;
};

export type KnowledgeDocumentDetail = KnowledgeDocument & {
  created_at?: string | null;
  updated_at?: string | null;
  status?: string;
  metadata?: Record<string, unknown>;
};

export type KnowledgeSegment = {
  id: string;
  document_id: string;
  content: string;
  status?: string;
  source?: string;
  position?: number | string | null;
  score?: number;
  metadata?: Record<string, unknown>;
};

export type KnowledgeSearchHit = KnowledgeSegment;

export type KnowledgeBaseScope = "shared" | "private";
export type KnowledgeBaseStatus = "enabled" | "disabled";
export type KnowledgeIndexStatus = "ready" | "indexing" | "failed" | "pending";
export type KnowledgeDocSourceType = "file" | "manual" | "link";

export type KnowledgeDocumentPreviewSection = {
  id: string;
  heading: string;
  body: string;
};

export type KnowledgeDocumentPreview = {
  title: string;
  description: string;
  source_label: string;
  source_uri?: string | null;
  tags: string[];
  updated_at: string;
  sections: KnowledgeDocumentPreviewSection[];
  warning?: string | null;
};

export type KnowledgeBaseDocument = {
  id: string;
  title: string;
  source_type: KnowledgeDocSourceType;
  source_label: string;
  file_name: string;
  size_bytes: number | null;
  index_status: KnowledgeIndexStatus;
  status: KnowledgeBaseStatus;
  updated_at: string;
  preview_summary: string;
  tags: string[];
  preview: KnowledgeDocumentPreview;
};

export type KnowledgeBaseSummary = {
  id: string;
  name: string;
  code: string;
  scope: KnowledgeBaseScope;
  document_count: number;
  storage_bytes: number;
  index_status: KnowledgeIndexStatus;
  status: KnowledgeBaseStatus;
  updated_at: string;
  description: string;
};

export type KnowledgeBaseDetail = KnowledgeBaseSummary & {
  knowledge_base_id: string;
  created_at: string;
  indexed_at: string | null;
  documents: KnowledgeBaseDocument[];
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
  status?: "available" | "unavailable";
  updated_at?: string;
  lifecycle_status?: "draft" | "published";
  file_name?: string | null;
  markdown_content?: string | null;
};

export type ToolChannelStatus = {
  name: string;
  health: "ready" | "degraded" | "unavailable" | "disabled";
  required_by_tools: string[];
  enabled: boolean;
  mode: string;
  last_error?: string | null;
  last_checked_at?: string | null;
};

export type ToolChannelsStatusResponse = {
  runtime_mode: "strict" | "degraded";
  channels: ToolChannelStatus[];
};

export type LLMRuntimeStatus = {
  required: boolean;
  ready: boolean;
  api_key_configured: boolean;
  api_key_source: string;
  api_key_length: number;
  model: string;
  base_url?: string | null;
  reason?: string | null;
};

export type WSEvent = {
  schema_version: string;
  type: EventType;
  session_id: string;
  timestamp: string;
  data: Record<string, unknown>;
};
