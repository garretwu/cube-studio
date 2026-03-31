import axios from "axios";

import type {
  Alert,
  AlertCluster,
  ChatMessage,
  DiagnosisSession,
  DiagnosisSessionSummary,
  KnowledgeDocument,
  OntologyEdge,
  OntologyNode,
  RemediationOverview,
  SkillDescriptor,
  TopologyExplorerResponse,
} from "./types";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  timeout: 10000,
});

let hasWarnedAboutDevFallback = false;

function isHtmlShellPayload(payload: unknown) {
  return (
    typeof payload === "string" &&
    payload.toLowerCase().includes("<!doctype html")
  );
}

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
      console.warn("MSW/browser mock 未就绪，已回退到本地 mock 数据。");
    }

    console.warn(`API ${label} 请求失败，使用本地 mock 回退。`, error);
    return fallback();
  }
}

export const apiClient = {
  getTopology: async () => {
    return withDevFallback(
      async () => {
        const response = await api.get<{ nodes: OntologyNode[]; edges: OntologyEdge[]; active_alerts: number; recent_events: string[] }>(
          "/api/ontology",
        );
        return response.data;
      },
      async () => {
        const { getTopologyFallback } = await import("./devFallback");
        return getTopologyFallback();
      },
      "getTopology",
    );
  },
  getTopologyExplorer: async () => {
    return withDevFallback(
      async () => {
        const response = await api.get<TopologyExplorerResponse>("/api/topology-explorer");
        return response.data;
      },
      async () => {
        const { getTopologyExplorerFallback } = await import("./devFallback");
        return getTopologyExplorerFallback();
      },
      "getTopologyExplorer",
    );
  },
  getAlerts: async () => {
    return withDevFallback(
      async () => {
        const response = await api.get<{ alerts: Alert[]; clusters: AlertCluster[] }>("/api/alerts");
        return response.data;
      },
      async () => {
        const { getAlertsFallback } = await import("./devFallback");
        return getAlertsFallback();
      },
      "getAlerts",
    );
  },
  getDiagnosisSession: async (sessionId?: string) => {
    return withDevFallback(
      async () => {
        const response = await api.get<DiagnosisSession>("/api/diagnosis/session/current", {
          params: sessionId ? { session_id: sessionId } : undefined,
        });
        return response.data;
      },
      async () => {
        const { getDiagnosisSessionFallback } = await import("./devFallback");
        return getDiagnosisSessionFallback();
      },
      "getDiagnosisSession",
    );
  },
  getDiagnosisHistorySessions: async () => {
    return withDevFallback(
      async () => {
        const response = await api.get<DiagnosisSessionSummary[]>("/api/diagnosis/sessions");
        return response.data;
      },
      async () => {
        const { getDiagnosisHistorySessionsFallback } = await import("./devFallback");
        return getDiagnosisHistorySessionsFallback();
      },
      "getDiagnosisHistorySessions",
    );
  },
  getChatHistory: async (sessionId?: string) => {
    return withDevFallback(
      async () => {
        const response = await api.get<ChatMessage[]>("/api/chat/history", {
          params: sessionId ? { session_id: sessionId } : undefined,
        });
        return response.data;
      },
      async () => {
        const { getChatHistoryFallback } = await import("./devFallback");
        return getChatHistoryFallback(sessionId);
      },
      "getChatHistory",
    );
  },
  getRemediationOverview: async () => {
    return withDevFallback(
      async () => {
        const response = await api.get<RemediationOverview>("/api/remediation/overview");
        return response.data;
      },
      async () => {
        const { getRemediationOverviewFallback } = await import("./devFallback");
        return getRemediationOverviewFallback();
      },
      "getRemediationOverview",
    );
  },
  approveRemediation: async (sessionId: string, approved: boolean) => {
    return withDevFallback(
      async () => {
        const response = await api.post<{ success: boolean; status: string }>(`/api/remediation/${sessionId}/approve`, {
          approved,
        });
        return response.data;
      },
      async () => {
        const { approveRemediationFallback } = await import("./devFallback");
        return approveRemediationFallback(sessionId, approved);
      },
      "approveRemediation",
    );
  },
  postChatMessage: async (sessionId: string, content: string) => {
    return withDevFallback(
      async () => {
        const response = await api.post<{ reply: ChatMessage }>("/api/chat", {
          content,
          search_text: content,
          session_id: sessionId,
        });
        return response.data.reply;
      },
      async () => {
        const { postChatMessageFallback } = await import("./devFallback");
        return postChatMessageFallback(sessionId, content);
      },
      "postChatMessage",
    );
  },
  searchKnowledge: async (query: string, category?: string) => {
    return withDevFallback(
      async () => {
        const response = await api.get<{ results: KnowledgeDocument[] }>("/api/knowledge/search", {
          params: { query, category },
        });
        return response.data.results;
      },
      async () => {
        const { searchKnowledgeFallback } = await import("./devFallback");
        return searchKnowledgeFallback(query, category);
      },
      "searchKnowledge",
    );
  },
  getKnowledgeSources: async () => {
    return withDevFallback(
      async () => {
        const response = await api.get<{ documents: KnowledgeDocument[] }>("/api/knowledge/documents");
        return response.data.documents;
      },
      async () => {
        const { getKnowledgeSourcesFallback } = await import("./devFallback");
        return getKnowledgeSourcesFallback();
      },
      "getKnowledgeSources",
    );
  },
  getSkills: async () => {
    return withDevFallback(
      async () => {
        const response = await api.get<SkillDescriptor[]>("/api/skills");
        return response.data;
      },
      async () => {
        const { getSkillsFallback } = await import("./devFallback");
        return getSkillsFallback();
      },
      "getSkills",
    );
  },
};
