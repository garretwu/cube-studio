import axios from "axios";

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
  RemediationResult,
  SREApiEnvelope,
  SessionSummary,
  SkillDescriptor,
  TopologySnapshot,
} from "./types";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  timeout: 10000,
});

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
    const message = error instanceof Error ? error.message : "未知请求错误";
    return Promise.reject(new Error(message));
  },
);

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

export const apiClient = {
  getTopology: async () => {
    const response = await api.get<SREApiEnvelope<TopologySnapshot>>("/api/topology");
    return unwrapPayload(response.data);
  },
  getAlerts: async () => {
    const response = await api.get<SREApiEnvelope<{ alerts: Alert[]; clusters: AlertCluster[] }> | { alerts: Alert[]; clusters: AlertCluster[] }>(
      "/api/alerts",
    );
    return unwrapPayload(response.data);
  },
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
    const response = await api.get<SREApiEnvelope<SessionSummary[]>>("/api/sessions", { params: { limit } });
    return unwrapPayload(response.data);
  },
  getDiagnosisSession: async (sessionId?: string) => {
    const resolved = (sessionId ?? getRememberedSessionId()).trim();
    if (!resolved) {
      throw new Error("session_id is required");
    }
    const response = await api.get<SREApiEnvelope<DiagnosisSession>>(`/api/sessions/${resolved}`);
    const session = unwrapPayload(response.data);
    rememberSessionId(session.session_id);
    return session;
  },
  getSessionLoop: async (sessionId?: string) => {
    const resolved = (sessionId ?? getRememberedSessionId()).trim();
    if (!resolved) {
      throw new Error("session_id is required");
    }
    const response = await api.get<SREApiEnvelope<LoopResult>>(`/api/sessions/${resolved}/loop`);
    const loop = unwrapPayload(response.data);
    rememberSessionId(loop.session_id);
    return loop;
  },
  approveRemediation: async (sessionId: string, approved: boolean, user = "ui-operator") => {
    const response = await api.post<SREApiEnvelope<RemediationResult>>(`/api/remediate/${sessionId}/approve`, {
      approved,
      user,
    });
    return unwrapPayload(response.data);
  },
  postChatMessage: async (content: string) => {
    const response = await api.post<SREApiEnvelope<{ reply: string }> | { reply: string }>("/api/chat", { content });
    const payload = unwrapPayload(response.data);
    const text = payload.reply;
    const reply: ChatMessage = {
      id: `assistant-${Date.now()}`,
      role: "assistant",
      content: text,
      created_at: new Date().toISOString(),
    };
    return reply;
  },
  searchKnowledge: async (query: string, category?: string) => {
    const response = await api.get<SREApiEnvelope<KnowledgeDocument[]> | { results: KnowledgeDocument[] }>("/api/knowledge/search", {
      params: { query, category },
    });
    if (isEnvelope<KnowledgeDocument[]>(response.data)) {
      return unwrapPayload(response.data);
    }
    return response.data.results;
  },
  getKnowledgeSources: async () => {
    const response = await api.get<SREApiEnvelope<KnowledgeDocument[]> | { documents: KnowledgeDocument[] }>("/api/knowledge/documents");
    if (isEnvelope<KnowledgeDocument[]>(response.data)) {
      return unwrapPayload(response.data);
    }
    return response.data.documents;
  },
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
  getSkills: async () => {
    const response = await api.get<SREApiEnvelope<SkillDescriptor[]> | SkillDescriptor[]>("/api/skills");
    return unwrapPayload(response.data);
  },
};
