import axios from "axios";

import type {
  Alert,
  AlertCluster,
  ChatMessage,
  ConfigBaseline,
  DiagnosisSession,
  DiagnosisSessionSummary,
  IncidentRecord,
  KnowledgeDocument,
  LearnedPattern,
  LoopResult,
  OntologyEdge,
  OntologyNode,
  RemediationOverview,
  RemediationResult,
  SREApiEnvelope,
  SessionSummary,
  SkillDescriptor,
  ToolChannelsStatusResponse,
  TopologyExplorerResponse,
  TopologyLayer,
  TopologyObjectStatus,
  TopologySnapshot,
  TopologyStatus,
} from "./types";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  timeout: 10000,
});

let hasWarnedAboutDevFallback = false;

api.interceptors.request.use((config) => {
  const traceId = `sre-ui-${Date.now()}`;
  config.headers = config.headers ?? {};
  config.headers["x-trace-id"] = traceId;
  const token = import.meta.env.VITE_API_TOKEN;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    let message = error instanceof Error ? error.message : "Unknown request error";
    if (axios.isAxiosError(error)) {
      const code = error.code ?? "";
      if (code === "ERR_NETWORK") {
        message =
          "Network/CORS error: backend unreachable or blocked by browser policy. Check backend status, VITE_API_BASE_URL, and CORS.";
      } else if (error.response?.status === 401) {
        message = "Unauthorized: invalid or expired bearer token.";
      } else if (error.response?.status) {
        message = `Request failed with status ${error.response.status}.`;
      }
    }
    return Promise.reject(new Error(message));
  },
);

function isHtmlShellPayload(payload: unknown) {
  return typeof payload === "string" && payload.toLowerCase().includes("<!doctype html");
}

function isEnvelope<T>(payload: unknown): payload is SREApiEnvelope<T> {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  const candidate = payload as Partial<SREApiEnvelope<T>>;
  return typeof candidate.success === "boolean" && "data" in candidate;
}

function unwrapPayload<T>(payload: SREApiEnvelope<T> | T): T {
  if (!isEnvelope<T>(payload)) {
    return payload;
  }
  if (!payload.success || payload.data == null) {
    const message = payload.error?.message ?? "API request returned no data";
    throw new Error(message);
  }
  return payload.data;
}

function getRememberedSessionId(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem("sre_session_id") ?? "";
}

function rememberSessionId(sessionId: string): void {
  if (typeof window === "undefined" || !sessionId) {
    return;
  }
  window.localStorage.setItem("sre_session_id", sessionId);
}

function extractDuplicateSessionId(payload: SREApiEnvelope<LoopResult>): string {
  const details = payload.error?.details;
  if (details && typeof details === "object") {
    const sessionId = (details as Record<string, unknown>).session_id;
    if (typeof sessionId === "string" && sessionId.trim()) {
      return sessionId.trim();
    }
  }
  const message = payload.error?.message ?? "";
  const matched = message.match(/session\s+([a-zA-Z0-9:_-]+)/);
  return matched?.[1] ?? "";
}

function normalizeSeverity(value: string): SessionSummary["severity"] {
  const lowered = value.toLowerCase();
  if (lowered === "critical" || lowered === "warning" || lowered === "info") {
    return lowered;
  }
  return "warning";
}

function mapSummaryToDiagnosisSummary(item: SessionSummary): DiagnosisSessionSummary {
  return {
    session_id: item.session_id,
    title: `${item.alert_name} · ${item.severity.toUpperCase()}`,
    summary: item.outcome ? `状态 ${item.status}，结果 ${item.outcome}` : `状态 ${item.status}`,
    started_at: item.updated_at,
    updated_at: item.updated_at,
    status: item.status,
    severity: normalizeSeverity(item.severity),
    alert_name: item.alert_name,
    duration_seconds: item.duration_seconds,
    outcome: item.outcome ?? null,
    triage_priority: null,
    root_cause: null,
    affected_services: [],
  };
}

function mapEntityTypeToExplorerType(entityType: string): "rack" | "node" | "gpu" | "switch" | "service" | "cluster" {
  const value = entityType.toLowerCase();
  if (value.includes("gpu")) {
    return "gpu";
  }
  if (value.includes("switch") || value.includes("port") || value.includes("network")) {
    return "switch";
  }
  if (value.includes("cluster")) {
    return "cluster";
  }
  if (value.includes("service") || value.includes("pod") || value.includes("inference")) {
    return "service";
  }
  if (value.includes("rack")) {
    return "rack";
  }
  return "node";
}

function mapNodeStatus(status: string | null | undefined): TopologyObjectStatus {
  const value = String(status ?? "").toLowerCase();
  if (value.includes("degraded") || value.includes("warning") || value.includes("error") || value.includes("down")) {
    return "abnormal";
  }
  if (value.includes("impact")) {
    return "impacted";
  }
  if (value.includes("maint")) {
    return "maintenance";
  }
  return "healthy";
}

function mapLayer(type: ReturnType<typeof mapEntityTypeToExplorerType>): TopologyLayer {
  if (type === "switch" || type === "rack") {
    return "network";
  }
  if (type === "gpu" || type === "node") {
    return "compute";
  }
  if (type === "cluster") {
    return "physical";
  }
  return "service";
}

function mapRelationType(relation: string): "contains" | "runs_on" | "connects_to" | "depends_on" | "uplink_to" | "aggregated" {
  const value = relation.toLowerCase();
  if (value.includes("contain") || value.includes("part_of")) {
    return "contains";
  }
  if (value.includes("hosted") || value.includes("runs_on") || value.includes("run_on")) {
    return "runs_on";
  }
  if (value.includes("uplink")) {
    return "uplink_to";
  }
  if (value.includes("connect")) {
    return "connects_to";
  }
  if (value.includes("aggreg")) {
    return "aggregated";
  }
  return "depends_on";
}

function buildTopologyExplorerFromSnapshot(snapshot: TopologySnapshot): TopologyExplorerResponse {
  const nodes = snapshot.nodes.map((node) => {
    const entityType = mapEntityTypeToExplorerType(node.entity_type);
    const attributes = typeof node.properties === "object" && node.properties ? node.properties : {};
    const region = String((attributes as Record<string, unknown>).region ?? "AIDC-CN");
    const zone = String((attributes as Record<string, unknown>).zone ?? "zone-a");
    const domain = String((attributes as Record<string, unknown>).domain ?? "aidc");

    return {
      id: node.id,
      name: node.name || node.id,
      type: entityType,
      status: mapNodeStatus(node.status),
      layer: mapLayer(entityType),
      domain,
      region,
      zone,
      cluster: String((attributes as Record<string, unknown>).cluster ?? "") || undefined,
      rack: String((attributes as Record<string, unknown>).rack ?? "") || undefined,
      slot: String((attributes as Record<string, unknown>).slot ?? "") || undefined,
      summary: `${node.name || node.id} (${node.entity_type})`,
      tags: [],
      updatedAt: node.updated_at,
      metrics: undefined,
      attributes,
    };
  });

  const edges = snapshot.edges.map((edge, index) => ({
    id: `edge-${index}-${edge.source_id}-${edge.target_id}`,
    source: edge.source_id,
    target: edge.target_id,
    relationType: mapRelationType(edge.relation),
    status: "healthy" as const,
    isCritical: false,
    impactLevel: "low" as const,
    label: edge.relation,
    isAggregated: false,
  }));

  return {
    site: {
      id: "aidc-site",
      name: "AIDC Site",
      region: "AIDC-CN",
      zone: "zone-a",
      domain: "aidc",
      summary: "Topology explorer snapshot mapped from /api/topology",
    },
    nodes,
    edges,
    paths: [],
    lastUpdated: snapshot.last_synced_at ?? new Date().toISOString(),
  };
}

async function withDevFallback<T>(request: () => Promise<T>, fallback: () => Promise<T>, label: string) {
  try {
    const result = await request();
    if (isHtmlShellPayload(result)) {
      throw new Error(`Unexpected HTML payload received for ${label}`);
    }
    return result;
  } catch (error) {
    if (!import.meta.env.DEV) {
      throw error;
    }

    if (!hasWarnedAboutDevFallback) {
      hasWarnedAboutDevFallback = true;
      console.warn("MSW/browser mock not ready, using local fallback data.");
    }

    console.warn(`API ${label} request failed, using local fallback.`, error);
    return fallback();
  }
}

export const apiClient = {
  getTopology: async () =>
    withDevFallback(
      async () => {
        const response = await api.get<SREApiEnvelope<TopologySnapshot> | TopologySnapshot>("/api/topology");
        return unwrapPayload(response.data);
      },
      async () => {
        const { getTopologyFallback } = await import("./devFallback");
        return getTopologyFallback();
      },
      "getTopology",
    ),

  getTopologyStatus: async () => {
    const response = await api.get<SREApiEnvelope<TopologyStatus> | TopologyStatus>("/api/topology/status");
    return unwrapPayload(response.data);
  },

  triggerTopologyDiscover: async () => {
    const response = await api.post<SREApiEnvelope<TopologyStatus> | TopologyStatus>("/api/topology/discover");
    return unwrapPayload(response.data);
  },

  getTopologyExplorer: async () => {
    try {
      const response = await api.get<SREApiEnvelope<TopologyExplorerResponse> | TopologyExplorerResponse>("/api/topology-explorer");
      return unwrapPayload(response.data);
    } catch (primaryError) {
      try {
        const snapshot = await apiClient.getTopology();
        return buildTopologyExplorerFromSnapshot(snapshot);
      } catch {
        if (import.meta.env.DEV) {
          const { getTopologyExplorerFallback } = await import("./devFallback");
          return getTopologyExplorerFallback();
        }
        throw primaryError;
      }
    }
  },

  getAlerts: async () =>
    withDevFallback(
      async () => {
        const response = await api.get<SREApiEnvelope<{ alerts: Alert[]; clusters: AlertCluster[] }> | { alerts: Alert[]; clusters: AlertCluster[] }>(
          "/api/alerts",
        );
        return unwrapPayload(response.data);
      },
      async () => {
        const { getAlertsFallback } = await import("./devFallback");
        return getAlertsFallback();
      },
      "getAlerts",
    ),

  handleAlert: async (alert: Alert) => {
    const response = await api.post<SREApiEnvelope<LoopResult>>("/api/handle", alert);
    const payload = response.data;
    if (!isEnvelope<LoopResult>(payload)) {
      throw new Error("invalid /api/handle response");
    }
    if (payload.data?.session_id) {
      rememberSessionId(payload.data.session_id);
      return payload.data.session_id;
    }
    if (payload.error?.code === "ALERT_DUPLICATE") {
      const sessionId = extractDuplicateSessionId(payload);
      if (sessionId) {
        rememberSessionId(sessionId);
        return sessionId;
      }
    }
    throw new Error(payload.error?.message ?? "handle alert returned no session_id");
  },

  getSessions: async (limit = 50) => {
    const response = await api.get<SREApiEnvelope<SessionSummary[]> | SessionSummary[]>("/api/sessions", { params: { limit } });
    return unwrapPayload(response.data);
  },

  getDiagnosisSession: async (sessionId?: string) =>
    withDevFallback(
      async () => {
        const resolved = (sessionId ?? getRememberedSessionId()).trim();
        if (resolved) {
          const response = await api.get<SREApiEnvelope<DiagnosisSession> | DiagnosisSession>(`/api/sessions/${resolved}`);
          const session = unwrapPayload(response.data);
          rememberSessionId(session.session_id);
          return session;
        }

        const sessions = await apiClient.getSessions(1);
        if (!sessions.length) {
          throw new Error("no diagnosis session available");
        }
        const latest = sessions[0];
        const response = await api.get<SREApiEnvelope<DiagnosisSession> | DiagnosisSession>(`/api/sessions/${latest.session_id}`);
        const session = unwrapPayload(response.data);
        rememberSessionId(session.session_id);
        return session;
      },
      async () => {
        const { getDiagnosisSessionFallback } = await import("./devFallback");
        return getDiagnosisSessionFallback();
      },
      "getDiagnosisSession",
    ),

  getDiagnosisHistorySessions: async () =>
    withDevFallback(
      async () => {
        const sessions = await apiClient.getSessions(50);
        return sessions.map(mapSummaryToDiagnosisSummary);
      },
      async () => {
        const { getDiagnosisHistorySessionsFallback } = await import("./devFallback");
        return getDiagnosisHistorySessionsFallback();
      },
      "getDiagnosisHistorySessions",
    ),

  getSessionLoop: async (sessionId?: string) => {
    const resolved = (sessionId ?? getRememberedSessionId()).trim();
    if (!resolved) {
      throw new Error("session_id is required");
    }
    const response = await api.get<SREApiEnvelope<LoopResult> | LoopResult>(`/api/sessions/${resolved}/loop`);
    const loop = unwrapPayload(response.data);
    rememberSessionId(loop.session_id);
    return loop;
  },

  approveRemediation: async (sessionId: string, approved: boolean, user = "ui-operator") => {
    const response = await api.post<SREApiEnvelope<RemediationResult> | RemediationResult>(`/api/remediate/${sessionId}/approve`, {
      approved,
      user,
    });
    return unwrapPayload(response.data);
  },

  getRemediationOverview: async (sessionId?: string) => {
    const loop = await apiClient.getSessionLoop(sessionId);
    return {
      session_id: loop.session_id,
      plan: {
        plan_id: loop.session_id,
        root_cause: loop.winning_candidate?.root_cause ?? "pending",
        description: "Derived from loop result",
        steps: [],
        estimated_impact: "unknown",
        confidence: loop.winning_candidate?.confidence ?? 0,
        priority: "P2" as const,
      },
      progress: {
        status: loop.outcome,
        completed_steps: loop.attempts.length,
        total_steps: loop.attempts.length,
        batch_status: [],
      },
      approval_required: false,
    } as RemediationOverview;
  },

  postChatMessage: async (sessionOrContent: string, maybeContent?: string) =>
    withDevFallback(
      async () => {
        const content = (maybeContent ?? sessionOrContent).trim();
        if (!content) {
          throw new Error("content is required");
        }
        const response = await api.post<SREApiEnvelope<{ reply: string } | { reply: ChatMessage }> | { reply: string } | { reply: ChatMessage }>(
          "/api/chat",
          { content },
        );
        const payload = unwrapPayload(response.data);
        const replyValue = payload.reply;
        if (typeof replyValue === "string") {
          return {
            id: `assistant-${Date.now()}`,
            role: "assistant" as const,
            content: replyValue,
            created_at: new Date().toISOString(),
            metadata: maybeContent ? { session_id: sessionOrContent } : undefined,
          };
        }
        return replyValue;
      },
      async () => {
        const { postChatMessageFallback } = await import("./devFallback");
        if (maybeContent) {
          return postChatMessageFallback(sessionOrContent, maybeContent);
        }
        return postChatMessageFallback("", sessionOrContent);
      },
      "postChatMessage",
    ),

  getChatHistory: async (sessionId?: string) =>
    withDevFallback(
      async () => {
        const response = await api.get<SREApiEnvelope<ChatMessage[]> | ChatMessage[]>("/api/chat/history", {
          params: sessionId ? { session_id: sessionId } : undefined,
        });
        return unwrapPayload(response.data);
      },
      async () => {
        const { getChatHistoryFallback } = await import("./devFallback");
        return getChatHistoryFallback(sessionId);
      },
      "getChatHistory",
    ),

  searchKnowledge: async (query: string, category?: string) =>
    withDevFallback(
      async () => {
        const response = await api.get<SREApiEnvelope<KnowledgeDocument[]> | { results: KnowledgeDocument[] }>("/api/knowledge/search", {
          params: { query, category },
        });
        if (isEnvelope<KnowledgeDocument[]>(response.data)) {
          return unwrapPayload(response.data);
        }
        return response.data.results;
      },
      async () => {
        const { searchKnowledgeFallback } = await import("./devFallback");
        return searchKnowledgeFallback(query, category);
      },
      "searchKnowledge",
    ),

  getKnowledgeSources: async () =>
    withDevFallback(
      async () => {
        const response = await api.get<SREApiEnvelope<KnowledgeDocument[]> | { documents: KnowledgeDocument[] }>("/api/knowledge/documents");
        if (isEnvelope<KnowledgeDocument[]>(response.data)) {
          return unwrapPayload(response.data);
        }
        return response.data.documents;
      },
      async () => {
        const { getKnowledgeSourcesFallback } = await import("./devFallback");
        return getKnowledgeSourcesFallback();
      },
      "getKnowledgeSources",
    ),

  getMemoryIncidents: async (last = 10) => {
    const response = await api.get<SREApiEnvelope<IncidentRecord[]> | IncidentRecord[]>("/api/memory/incidents", { params: { last } });
    return unwrapPayload(response.data);
  },

  getMemoryPatterns: async () => {
    const response = await api.get<SREApiEnvelope<LearnedPattern[]> | LearnedPattern[]>("/api/memory/patterns");
    return unwrapPayload(response.data);
  },

  getMemoryBaseline: async () => {
    const response = await api.get<SREApiEnvelope<ConfigBaseline> | ConfigBaseline>("/api/memory/baseline");
    return unwrapPayload(response.data);
  },

  getSkills: async () =>
    withDevFallback(
      async () => {
        const response = await api.get<SREApiEnvelope<SkillDescriptor[]> | SkillDescriptor[]>("/api/skills");
        return unwrapPayload(response.data);
      },
      async () => {
        const { getSkillsFallback } = await import("./devFallback");
        return getSkillsFallback();
      },
      "getSkills",
    ),

  getToolChannelsStatus: async () => {
    const response = await api.get<SREApiEnvelope<ToolChannelsStatusResponse> | ToolChannelsStatusResponse>("/api/tools/channels/status");
    return unwrapPayload(response.data);
  },
};

export type ApiClient = typeof apiClient;
