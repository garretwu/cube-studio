import type {
  Alert,
  AlertCluster,
  ChatMessage,
  ConfigBaseline,
  DiagnosisSession,
  IncidentRecord,
  KnowledgeDocument,
  LearnedPattern,
  LoopResult,
  OntologyEdge,
  OntologyNode,
  SkillDescriptor,
} from "../api/types";

export const topologyNodes: OntologyNode[] = [
  {
    id: "rack-1",
    entity_type: "rack",
    name: "Rack 01",
    properties: { zone: "A1" },
    status: "healthy",
    updated_at: "2026-03-18T12:00:00Z",
  },
  {
    id: "node-gpu-01",
    entity_type: "node",
    name: "node-gpu-01",
    properties: { role: "inference", rack: "rack-1" },
    status: "degraded",
    updated_at: "2026-03-18T12:01:00Z",
  },
  {
    id: "gpu-01",
    entity_type: "gpu",
    name: "GPU-01",
    properties: { model: "H100", utilization: 92 },
    status: "hot",
    updated_at: "2026-03-18T12:01:20Z",
  },
  {
    id: "sw-01",
    entity_type: "switch",
    name: "spine-sw-01",
    properties: { vendor: "H3C", role: "spine" },
    status: "healthy",
    updated_at: "2026-03-18T11:58:00Z",
  },
  {
    id: "svc-vllm",
    entity_type: "inference_service",
    name: "vllm-latency",
    properties: { namespace: "infer" },
    status: "impacted",
    updated_at: "2026-03-18T12:02:00Z",
  },
];

export const topologyEdges: OntologyEdge[] = [
  { source_id: "node-gpu-01", target_id: "rack-1", relation: "part_of", properties: {} },
  { source_id: "gpu-01", target_id: "node-gpu-01", relation: "part_of", properties: {} },
  { source_id: "svc-vllm", target_id: "node-gpu-01", relation: "hosted_on", properties: {} },
  { source_id: "node-gpu-01", target_id: "sw-01", relation: "connected_to", properties: { bandwidth: "200G" } },
];

export const alerts: Alert[] = [
  {
    alert_name: "vllm_latency_high",
    severity: "critical",
    labels: { service: "vllm", instance: "vllm-0", aidc: "aidc-001" },
    annotations: { summary: "p95 latency crossed 600ms", entity: "svc-vllm" },
    starts_at: "2026-03-18T12:00:00Z",
    fingerprint: "fp-001",
    status: "firing",
    source: "alertmanager",
  },
  {
    alert_name: "gpu_thermal_warn",
    severity: "warning",
    labels: { node: "node-gpu-01", gpu: "gpu-01", aidc: "aidc-001" },
    annotations: { summary: "GPU temperature stays above target", entity: "gpu-01" },
    starts_at: "2026-03-18T11:57:00Z",
    fingerprint: "fp-002",
    status: "firing",
    source: "prometheus",
  },
];

export const alertClusters: AlertCluster[] = [
  {
    cluster_id: "cluster-01",
    summary: "Latency spike correlates with thermal pressure on one inference node",
    severity: "critical",
    alerts: ["fp-001", "fp-002"],
  },
];

export const diagnosisSession: DiagnosisSession = {
  session_id: "sess-latency-001",
  alert: alerts[0],
  status: "re_diagnosed",
  duration_seconds: 142,
  outcome: "proposed_fix_ready",
  diagnosis_result: {
    root_cause: "GPU contention from a rogue benchmark process",
    root_cause_layer: "hardware",
    root_cause_entities: ["gpu-01", "node-gpu-01"],
    confidence: 0.93,
    hypotheses: [
      {
        description: "RoCE congestion collapse",
        status: "eliminated",
        evidence_for: ["ECN counters elevated"],
        evidence_against: ["switch queue depth normal"],
        confidence: 0.28,
      },
      {
        description: "GPU contention by rogue process",
        status: "confirmed",
        evidence_for: ["DCGM process list shows gpu-burn", "utilization pinned at 92%"],
        evidence_against: [],
        confidence: 0.93,
      },
    ],
    impact_summary: "One inference shard is saturated, causing p95 latency fan-out across the service.",
    affected_services: ["vllm-latency", "chat-serving"],
    triage_priority: "P1",
    diagnosis_certainty: "confirmed",
  },
  trace: {
    steps: [
      {
        step: 1,
        timestamp: "2026-03-18T12:00:12Z",
        thought: "Correlate the latency alert with topology blast radius.",
        action_type: "tool_call",
        tool_name: "ontology.get_blast_radius",
        tool_params: { entity_id: "svc-vllm" },
        confidence: 0.74,
      },
      {
        tool: "prometheus_query",
        params: { query: "gpu_utilization{node='node-gpu-01'}" },
        result: { value: 0.92, trend: "rising" },
        timestamp: "2026-03-18T12:00:32Z",
      },
      {
        step: 2,
        timestamp: "2026-03-18T12:00:48Z",
        thought: "Thermal and utilization signals point to local GPU contention instead of network loss.",
        action_type: "conclude",
        confidence: 0.93,
      },
    ],
  },
};

export const remediationOverview: LoopResult = {
  session_id: diagnosisSession.session_id,
  outcome: "re_diagnosed",
  winning_candidate: {
    root_cause: "GPU contention from a rogue benchmark process",
    confidence: 0.88,
  },
  attempts: [
    {
      candidate: {
        root_cause: "GPU contention from a rogue benchmark process",
        confidence: 0.88,
      },
      remediation_result: {
        plan_id: "plan-rollback-01",
        success: false,
        steps_completed: 0,
        steps_total: 2,
        duration_seconds: 15,
        error: "approval required",
      },
      verification_passed: false,
      rolled_back: false,
      observations: {},
      duration_seconds: 15,
    },
  ],
  total_duration_seconds: 142,
  re_diagnosis_context: null,
};

export const initialChatMessages: ChatMessage[] = [
  {
    id: "chat-1",
    role: "assistant",
    content: "我已经带入当前 AIDC 拓扑和最近告警，可以继续帮你诊断或生成修复计划。",
    created_at: "2026-03-18T12:04:00Z",
  },
];

export const knowledgeDocuments: KnowledgeDocument[] = [
  {
    id: "kb-1",
    title: "RoCEv2 ECN Checklist",
    source: "docs/hardware/rocev2-ecn.md",
    category: "hardware",
    excerpt: "RoCEv2 ECN 配置需要在全网交换机上保持一致，避免 RDMA 流量出现拥塞扩散。",
    tags: ["roce", "ecn", "rdma"],
    score: 0.91,
  },
  {
    id: "kb-2",
    title: "GPU Contention Runbook",
    source: "knowledge/runbooks/gpu-contention.md",
    category: "runbook",
    excerpt: "Kill gpu-burn, verify DCGM process list, and restore traffic through a 10% canary window.",
    tags: ["gpu", "runbook", "latency"],
    score: 0.83,
  },
];

export const incidents: IncidentRecord[] = [
  {
    incident_id: "inc-demo-004",
    aidc_id: "local-aidc",
    timestamp: "2026-03-18T12:03:00Z",
    alert: alerts[0],
    symptoms: ["instance", "namespace", "vllm_latency_high"],
    root_cause: "gpu contention",
    root_cause_layer: "hardware",
    root_cause_entities: ["gpu-01"],
    outcome: "resolved",
    resolution_time_seconds: 182,
  },
  {
    incident_id: "inc-demo-003",
    aidc_id: "local-aidc",
    timestamp: "2026-03-18T12:02:00Z",
    alert: alerts[0],
    symptoms: ["instance", "namespace", "vllm_latency_high"],
    root_cause: "gpu contention",
    root_cause_layer: "hardware",
    root_cause_entities: ["gpu-01"],
    outcome: "resolved",
    resolution_time_seconds: 181,
  },
];

export const learnedPatterns: LearnedPattern[] = [
  {
    pattern_id: "inc-demo-001",
    aidc_id: "local-aidc",
    symptom_signature: ["instance", "namespace", "vllm_latency_high"],
    root_cause: "gpu contention",
    effective_fix: "Drain the hot shard and kill gpu-burn on the node.",
    occurrence_count: 4,
    confidence: 0.75,
    first_seen: "2026-03-18T12:00:00Z",
    last_seen: "2026-03-18T12:03:00Z",
    example_incidents: ["inc-demo-001", "inc-demo-004"],
  },
];

export const baseline: ConfigBaseline = {
  aidc_id: "local-aidc",
  version: 2,
  metric_baselines: { vllm_p95_ms: 180, gpu_temp_c: 78 },
  safety_thresholds: { vllm_p95_warn: 500, gpu_temp_warn: 85 },
  custom_rules: { night_window: "00:00-06:00", noisy_neighbors: ["gpu-burn"] },
  updated_at: "2026-03-18T10:30:00Z",
};

export const skills: SkillDescriptor[] = [
  {
    id: "builtin-topology",
    name: "Topology Navigator",
    scope: "builtin",
    summary: "Summarizes blast radius and highlights dependency chains across racks, switches, and services.",
    source: "builtin://topology",
    permissions: ["read:ontology", "read:events"],
    match_score: 0.96,
  },
  {
    id: "custom-runbook-rdma",
    name: "RDMA Runbook Matcher",
    scope: "custom",
    summary: "Matches alert patterns to RoCE/ECN runbooks and returns high-confidence repair suggestions.",
    source: "skills://rdma-runbook",
    permissions: ["read:knowledge", "read:metrics"],
    match_score: 0.88,
  },
];
