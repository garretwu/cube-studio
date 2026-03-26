import type {
  Alert,
  AlertCluster,
  ChatMessage,
  ConfigBaseline,
  DiagnosisSession,
  IncidentRecord,
  KnowledgeDocument,
  LearnedPattern,
  OntologyEdge,
  OntologyNode,
  RemediationOverview,
  SkillDescriptor,
} from "../api/types";

export const topologyNodes: OntologyNode[] = [
  {
    id: "rack-1",
    entity_type: "rack",
    name: "1号机柜",
    properties: { zone: "A1" },
    status: "healthy",
    updated_at: "2026-03-18T12:00:00Z",
  },
  {
    id: "node-gpu-01",
    entity_type: "node",
    name: "node-gpu-01",
    properties: { role: "推理", rack: "rack-1" },
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
    name: "核心交换机 01",
    properties: { vendor: "H3C", role: "核心" },
    status: "healthy",
    updated_at: "2026-03-18T11:58:00Z",
  },
  {
    id: "svc-vllm",
    entity_type: "inference_service",
    name: "vLLM 推理服务",
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
    alert_name: "VLLM 延迟过高",
    severity: "critical",
    labels: { service: "vllm", instance: "vllm-0", aidc: "aidc-001" },
    annotations: { summary: "p95 延迟已超过 600ms", entity: "svc-vllm" },
    starts_at: "2026-03-18T12:00:00Z",
    fingerprint: "fp-001",
    status: "firing",
    source: "alertmanager",
  },
  {
    alert_name: "GPU 温度偏高",
    severity: "warning",
    labels: { node: "node-gpu-01", gpu: "gpu-01", aidc: "aidc-001" },
    annotations: { summary: "GPU 温度持续高于目标阈值", entity: "gpu-01" },
    starts_at: "2026-03-18T11:57:00Z",
    fingerprint: "fp-002",
    status: "firing",
    source: "prometheus",
  },
];

export const alertClusters: AlertCluster[] = [
  {
    cluster_id: "cluster-01",
    summary: "延迟突增与单个推理节点的热压异常高度相关",
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
    root_cause: "异常基准测试进程导致 GPU 资源争用",
    root_cause_layer: "hardware",
    root_cause_entities: ["gpu-01", "node-gpu-01"],
    confidence: 0.93,
    hypotheses: [
      {
        description: "RoCE 网络拥塞导致链路退化",
        status: "eliminated",
        evidence_for: ["ECN 计数器曾短暂升高"],
        evidence_against: ["交换机队列深度维持正常"],
        confidence: 0.28,
      },
      {
        description: "异常进程占满 GPU 资源",
        status: "confirmed",
        evidence_for: ["DCGM 进程列表出现 gpu-burn", "利用率持续锁定在 92%"],
        evidence_against: [],
        confidence: 0.93,
      },
    ],
    impact_summary: "单个推理分片持续饱和，导致整条服务链路上的 p95 延迟被放大。",
    affected_services: ["vllm-latency", "chat-serving"],
    triage_priority: "P1",
    diagnosis_certainty: "confirmed",
  },
  trace: {
    steps: [
      {
        step: 1,
        timestamp: "2026-03-18T12:00:12Z",
        thought: "先把延迟告警与当前拓扑影响半径关联起来，判断是否存在局部资源热点。",
        action_type: "tool_call",
        tool_name: "ontology.get_blast_radius",
        tool_params: { entity_id: "svc-vllm" },
        confidence: 0.74,
      },
      {
        tool: "prometheus_query",
        params: { query: "gpu_utilization{node='node-gpu-01'}" },
        result: { value: 0.92, trend: "上升" },
        timestamp: "2026-03-18T12:00:32Z",
      },
      {
        step: 2,
        timestamp: "2026-03-18T12:00:48Z",
        thought: "温度和利用率信号都指向本地 GPU 资源争用，而不是网络丢包或拥塞。",
        action_type: "conclude",
        confidence: 0.93,
      },
    ],
  },
};

export const remediationOverview: RemediationOverview = {
  session_id: diagnosisSession.session_id,
  approval_required: true,
  plan: {
    plan_id: "plan-rollback-01",
    root_cause: "异常基准测试进程导致 GPU 资源争用",
    description: "先从热点节点排出饱和副本，再终止 gpu-burn 进程，最后通过金丝雀流量恢复业务。",
    estimated_impact: "业务影响较低，排空阶段仅影响单个分片。",
    confidence: 0.88,
    priority: "P1",
    safety_level: "high",
    canary: {
      enabled: true,
      target_percentage: 0.1,
      monitor_duration: 120,
      success_criteria: [
        { metric: "vllm_p95_ms", operator: "<", value: 300 },
        { metric: "gpu_utilization", operator: "<", value: 0.75 },
      ],
    },
    steps: [
      {
        step_id: 1,
        description: "先从热点节点排出一个金丝雀分片。",
        tool: "k8s_cordon_drain",
        params: { node: "node-gpu-01", percentage: 0.1 },
        rollback_tool: "k8s_uncordon",
        verification: { method: "wait", wait_seconds: 30 },
        timeout: 60,
      },
      {
        step_id: 2,
        description: "终止节点上的异常 gpu-burn 进程。",
        tool: "shell_command",
        params: { host: "node-gpu-01", command: "pkill gpu-burn" },
        rollback_tool: null,
        verification: { method: "tool_call", tool: "check_gpu_processes" },
        timeout: 45,
      },
    ],
  },
  progress: {
    status: "awaiting_approval",
    completed_steps: 0,
    total_steps: 2,
    batch_status: [
      { batch: "金丝雀 10%", progress: 30, status: "validating" },
      { batch: "扩容 50%", progress: 0, status: "pending" },
    ],
  },
};

export const initialChatMessages: ChatMessage[] = [
  {
    id: "chat-1",
    role: "assistant",
    content: "我已经带入当前 AIDC 拓扑与最新告警，可以继续帮你诊断问题或生成修复计划。",
    created_at: "2026-03-18T12:04:00Z",
  },
];

export const knowledgeDocuments: KnowledgeDocument[] = [
  {
    id: "kb-1",
    title: "RoCEv2 ECN 检查清单",
    source: "docs/hardware/rocev2-ecn.md",
    category: "hardware",
    excerpt: "RoCEv2 ECN 配置需要在全网交换机上保持一致，避免 RDMA 流量出现拥塞扩散。",
    tags: ["roce", "ecn", "rdma"],
    score: 0.91,
  },
  {
    id: "kb-2",
    title: "GPU 资源争用运行手册",
    source: "knowledge/runbooks/gpu-contention.md",
    category: "runbook",
    excerpt: "终止 gpu-burn，核对 DCGM 进程列表，再通过 10% 金丝雀窗口逐步恢复流量。",
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
    root_cause: "GPU 资源争用",
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
    root_cause: "GPU 资源争用",
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
    root_cause: "GPU 资源争用",
    effective_fix: "先排空热点分片，再终止节点上的 gpu-burn 进程。",
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
    name: "拓扑导航器",
    scope: "builtin",
    summary: "总结影响半径，并高亮机柜、交换机与服务之间的依赖链路。",
    source: "builtin://topology",
    permissions: ["read:ontology", "read:events"],
    match_score: 0.96,
  },
  {
    id: "custom-runbook-rdma",
    name: "RDMA 手册匹配器",
    scope: "custom",
    summary: "将告警模式匹配到 RoCE/ECN 手册，并返回高置信度修复建议。",
    source: "skills://rdma-runbook",
    permissions: ["read:knowledge", "read:metrics"],
    match_score: 0.88,
  },
];
