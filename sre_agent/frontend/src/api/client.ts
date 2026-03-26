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
  OntologyEdge,
  OntologyNode,
  RemediationOverview,
  SkillDescriptor,
} from "./types";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  timeout: 10000,
});

api.interceptors.request.use((config) => {
  const traceId = `sre-ui-${Date.now()}`;
  config.headers = config.headers ?? {};
  config.headers["x-trace-id"] = traceId;
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const message = error instanceof Error ? error.message : "未知请求错误";
    return Promise.reject(new Error(message));
  },
);

export const apiClient = {
  getTopology: async () => {
    const response = await api.get<{ nodes: OntologyNode[]; edges: OntologyEdge[]; active_alerts: number; recent_events: string[] }>(
      "/api/ontology",
    );
    return response.data;
  },
  getAlerts: async () => {
    const response = await api.get<{ alerts: Alert[]; clusters: AlertCluster[] }>("/api/alerts");
    return response.data;
  },
  getDiagnosisSession: async () => {
    const response = await api.get<DiagnosisSession>("/api/diagnosis/session/current");
    return response.data;
  },
  getRemediationOverview: async () => {
    const response = await api.get<RemediationOverview>("/api/remediation/overview");
    return response.data;
  },
  approveRemediation: async (sessionId: string, approved: boolean) => {
    const response = await api.post<{ success: boolean; status: string }>(`/api/remediation/${sessionId}/approve`, {
      approved,
    });
    return response.data;
  },
  postChatMessage: async (content: string) => {
    const response = await api.post<{ reply: ChatMessage }>("/api/chat", { content });
    return response.data.reply;
  },
  searchKnowledge: async (query: string, category?: string) => {
    const response = await api.get<{ results: KnowledgeDocument[] }>("/api/knowledge/search", {
      params: { query, category },
    });
    return response.data.results;
  },
  getKnowledgeSources: async () => {
    const response = await api.get<{ documents: KnowledgeDocument[] }>("/api/knowledge/documents");
    return response.data.documents;
  },
  getMemoryIncidents: async (last = 10) => {
    const response = await api.get<IncidentRecord[]>("/api/memory/incidents", { params: { last } });
    return response.data;
  },
  getMemoryPatterns: async () => {
    const response = await api.get<LearnedPattern[]>("/api/memory/patterns");
    return response.data;
  },
  getMemoryBaseline: async () => {
    const response = await api.get<ConfigBaseline>("/api/memory/baseline");
    return response.data;
  },
  getSkills: async () => {
    const response = await api.get<SkillDescriptor[]>("/api/skills");
    return response.data;
  },
};
