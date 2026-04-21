import axios from "axios";
import type { InternalAxiosRequestConfig } from "axios";

import {
  getAccessTokenSync,
  getAuthRecoveryState,
  recoverAuthSession,
  setAuthErrorKind,
  type AuthErrorKind,
  updateServerBootId,
} from "../auth/tokenManager";

import type {
  Alert,
  AlertCluster,
  ChatMessage,
  ChatDisplayPayload,
  ChatReplyMeta,
  ConfigBaseline,
  DiagnosisSession,
  DiagnosisSessionSummary,
  IncidentRecord,
  KnowledgeBaseDetail,
  KnowledgeBaseSummary,
  KnowledgeDataset,
  KnowledgeDocument,
  KnowledgeSearchHit,
  LearnedPattern,
  LoopResult,
  OntologyEdge,
  OntologyNode,
  RemediationOverview,
  RemediationPlan,
  RemediationResult,
  SREApiEnvelope,
  SessionSummary,
  SessionEvent,
  SkillDescriptor,
  ToolChannelsStatusResponse,
  LLMRuntimeStatus,
  TopologyExplorerResponse,
  TopologyLayer,
  TopologyObjectStatus,
  TopologyObjectType,
  TopologySnapshot,
  TopologyStatus,
} from "./types";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  timeout: 10000,
});
const DIAGNOSE_REQUEST_TIMEOUT_MS = 120000;
const CHAT_REQUEST_TIMEOUT_MS = 120000;
const DIAGNOSIS_SESSION_REQUEST_TIMEOUT_MS = 30000;
const DIAGNOSIS_EVENTS_REQUEST_TIMEOUT_MS = 30000;
const DIAGNOSIS_CHAT_HISTORY_REQUEST_TIMEOUT_MS = 20000;
const DIAGNOSE_SESSION_POLL_MS = 2000;
const DIAGNOSE_SESSION_POLL_ATTEMPTS = 30;
const BLOCKED_ALERT_NAMES = new Set([
  "kubeclienterrors",
  "kubepodcrashlooping",
  "alertmanagerdown",
  "kubecontrollermanagerdown",
  "kubeschedulerdown",
  "prometheusoperatordown",
  "deadmansswitch",
  "targetdown",
  "gpu utilization is high",
  "gpuutilizationhigh",
]);

let hasWarnedAboutDevFallback = false;

type RetriableAxiosRequestConfig = InternalAxiosRequestConfig & {
  _authRetryAttempted?: boolean;
};

export type SessionResolveState = "resolved" | "stale_redirected" | "empty";
export type SessionResolveSource = "url" | "remembered" | "latest" | "none";
export type ActiveSessionResolution = {
  session: DiagnosisSession | null;
  state: SessionResolveState;
  source: SessionResolveSource;
  staleSessionId?: string;
};

export class ApiRequestError extends Error {
  auth_error_kind: AuthErrorKind;

  constructor(message: string, authErrorKind: AuthErrorKind = "unknown") {
    super(message);
    this.name = "ApiRequestError";
    this.auth_error_kind = authErrorKind;
  }
}

function classifyAuthErrorKindFromMessage(value: string): AuthErrorKind {
  const normalized = value.toLowerCase();
  if (normalized.includes("expired")) {
    return "expired";
  }
  if (normalized.includes("signature")) {
    return "invalid_signature";
  }
  if (normalized.includes("missing")) {
    return "missing";
  }
  return "unknown";
}

function normalizeRequestErrorMessage(error: unknown): string {
  let message = error instanceof Error ? error.message : "Unknown request error";
  let authErrorKind: AuthErrorKind = "unknown";
  if (axios.isAxiosError(error)) {
    const code = error.code ?? "";
    const axiosMessage = String(error.message ?? "");
    const isTimeout =
      code === "ECONNABORTED" ||
      axiosMessage.toLowerCase().includes("timeout");
    if (isTimeout) {
      return "Request timed out while waiting for the backend. Please retry.";
    }
    if (code === "ERR_NETWORK") {
      return "Network/CORS error: backend unreachable or blocked by browser policy. Check backend status, VITE_API_BASE_URL, and CORS.";
    }
    if (error.response?.status === 401) {
      const detail = String(
        (error.response.data as { detail?: unknown } | undefined)?.detail ?? "",
      ).trim();
      authErrorKind = classifyAuthErrorKindFromMessage(detail || axiosMessage);
      setAuthErrorKind(authErrorKind);
      if (authErrorKind === "expired") {
        return "Unauthorized: bearer token expired. Attempting refresh.";
      }
      if (authErrorKind === "invalid_signature") {
        return "Unauthorized: bearer token signature invalid (backend may have restarted). Please refresh auth session.";
      }
      if (authErrorKind === "missing") {
        return "Unauthorized: bearer token is missing.";
      }
      return "Unauthorized: invalid bearer token.";
    }
    if (error.response?.status) {
      return `Request failed with status ${error.response.status}.`;
    }
  }
  return message;
}

api.interceptors.request.use((config) => {
  const traceId = `sre-ui-${Date.now()}`;
  config.headers = config.headers ?? {};
  config.headers["x-trace-id"] = traceId;
  const token = getAccessTokenSync();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => {
    const bootId = String(response.headers["x-server-boot-id"] ?? "").trim();
    if (bootId) {
      const changed = updateServerBootId(bootId);
      if (changed) {
        clearRememberedSessionId();
        if (typeof window !== "undefined") {
          window.dispatchEvent(new CustomEvent("sre:boot-id-changed", { detail: { bootId } }));
        }
      }
    }
    return response;
  },
  async (error) => {
    if (getAuthRecoveryState() === "terminal") {
      return Promise.reject(
        new ApiRequestError(
          "Authentication session is unrecoverable. Please re-authenticate or hard refresh the page.",
          "invalid_signature",
        ),
      );
    }
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      const originalConfig = error.config as RetriableAxiosRequestConfig | undefined;
      const requestUrl = String(originalConfig?.url ?? "");
      const isAuthEndpoint =
        requestUrl.includes("/api/auth/refresh") ||
        requestUrl.includes("/api/auth/token") ||
        requestUrl.includes("/api/auth/bootstrap");
      if (originalConfig && !originalConfig._authRetryAttempted && !isAuthEndpoint) {
        originalConfig._authRetryAttempted = true;
        try {
          const recovered = await recoverAuthSession();
          if (recovered) {
            const refreshed = getAccessTokenSync();
            originalConfig.headers = originalConfig.headers ?? {};
            originalConfig.headers.Authorization = `Bearer ${refreshed}`;
            return await api.request(originalConfig);
          }
        } catch {
          // fall through to normalized error
        }
      }
    }
    const message = normalizeRequestErrorMessage(error);
    const authKind = classifyAuthErrorKindFromMessage(message);
    return Promise.reject(new ApiRequestError(message, authKind));
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

function normalizeSessionSummaryList(payload: unknown): SessionSummary[] {
  if (Array.isArray(payload)) {
    return payload.filter((item): item is SessionSummary => !!item && typeof item === "object");
  }
  if (!payload || typeof payload !== "object") {
    return [];
  }

  const candidate = payload as {
    data?: unknown;
    sessions?: unknown;
    items?: unknown;
  };
  const raw = candidate.data ?? candidate.sessions ?? candidate.items;
  if (Array.isArray(raw)) {
    return raw.filter((item): item is SessionSummary => !!item && typeof item === "object");
  }
  return [];
}

function normalizeAlertName(value: string | null | undefined): string {
  return String(value ?? "").trim().toLowerCase();
}

function isBlockedAlert(alert: Alert): boolean {
  const candidates = [
    normalizeAlertName(alert.alert_name),
    normalizeAlertName(alert.labels?.alertname),
  ].filter(Boolean);
  return candidates.some((name) => BLOCKED_ALERT_NAMES.has(name));
}

function filterAlertSnapshot(payload: { alerts: Alert[]; clusters: AlertCluster[] }): { alerts: Alert[]; clusters: AlertCluster[] } {
  const alerts = payload.alerts.filter((alert) => !isBlockedAlert(alert));
  const allowedFingerprints = new Set(alerts.map((alert) => alert.fingerprint));
  const clusters = payload.clusters
    .map((cluster) => ({
      ...cluster,
      alerts: cluster.alerts.filter((fingerprint) => allowedFingerprints.has(fingerprint)),
    }))
    .filter((cluster) => cluster.alerts.length > 0);
  return { alerts, clusters };
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

function clearRememberedSessionId(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem("sre_session_id");
}

function getHttpStatusFromError(error: unknown): number | undefined {
  if (axios.isAxiosError(error)) {
    return error.response?.status;
  }
  if (!(error instanceof Error)) {
    return undefined;
  }
  const matched = /status\s+(\d{3})/i.exec(error.message);
  if (!matched) {
    return undefined;
  }
  const status = Number(matched[1]);
  return Number.isFinite(status) ? status : undefined;
}

function isHttpStatusError(error: unknown, status: number): boolean {
  return getHttpStatusFromError(error) === status;
}

async function getDiagnosisSessionById(sessionId: string): Promise<DiagnosisSession> {
  const response = await api.get<SREApiEnvelope<DiagnosisSession> | DiagnosisSession>(`/api/sessions/${sessionId}`, {
    timeout: DIAGNOSIS_SESSION_REQUEST_TIMEOUT_MS,
  });
  return unwrapPayload(response.data);
}

async function resolveLatestSessionFromSummaries(limit = 50): Promise<DiagnosisSession | null> {
  const sessions = await apiClient.getSessions(limit);
  for (const candidate of sessions) {
    try {
      const session = await getDiagnosisSessionById(candidate.session_id);
      rememberSessionId(session.session_id);
      return session;
    } catch (error) {
      if (isHttpStatusError(error, 404)) {
        continue;
      }
      throw error;
    }
  }
  return null;
}

async function resolveActiveSession(sessionId?: string): Promise<ActiveSessionResolution> {
  const explicit = (sessionId ?? "").trim();
  const remembered = getRememberedSessionId().trim();
  const resolved = explicit || remembered;
  const resolveSource: SessionResolveSource = explicit ? "url" : remembered ? "remembered" : "none";

  if (resolved) {
    try {
      const session = await getDiagnosisSessionById(resolved);
      rememberSessionId(session.session_id);
      return { session, state: "resolved", source: resolveSource };
    } catch (error) {
      if (!isHttpStatusError(error, 404)) {
        throw error;
      }
      clearRememberedSessionId();
      const fallback = await resolveLatestSessionFromSummaries(50);
      if (fallback) {
        return {
          session: fallback,
          state: "stale_redirected",
          source: "latest",
          staleSessionId: resolved,
        };
      }
      return {
        session: null,
        state: "empty",
        source: "none",
        staleSessionId: resolved,
      };
    }
  }

  const latest = await resolveLatestSessionFromSummaries(50);
  if (!latest) {
    return { session: null, state: "empty", source: "none" };
  }
  return { session: latest, state: "resolved", source: "latest" };
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
    title: `${item.alert_name} \u00b7 ${item.severity.toUpperCase()}`,
    summary: item.outcome ? `\u72b6\u6001 ${item.status}\uff0c\u7ed3\u679c ${item.outcome}` : `\u72b6\u6001 ${item.status}`,
    started_at: item.updated_at,
    updated_at: item.updated_at,
    status: item.status,
    severity: normalizeSeverity(item.severity),
    alert_name: item.alert_name,
    fingerprint: item.fingerprint,
    incident_key: item.incident_key ?? null,
    duration_seconds: item.duration_seconds,
    outcome: item.outcome ?? null,
    triage_priority: null,
    root_cause: null,
    affected_services: [],
  };
}

function mapEntityTypeToExplorerType(entityType: string): TopologyObjectType {
  const value = entityType.toLowerCase();
  if (value.includes("bmc")) {
    return "bmc";
  }
  if (value.includes("gpu")) {
    return "gpu";
  }
  if (value.includes("port")) {
    return "port";
  }
  if (value.includes("switch") || value.includes("network")) {
    return "switch";
  }
  if (value.includes("cluster")) {
    return "cluster";
  }
  if (value.includes("pod")) {
    return "pod";
  }
  if (value.includes("service") || value.includes("inference")) {
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
  if (type === "switch" || type === "port" || type === "rack") {
    return "network";
  }
  if (type === "gpu" || type === "node" || type === "bmc") {
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
    const namespace = String((attributes as Record<string, unknown>).namespace ?? "").trim();
    const baseName = String(node.name || node.id).trim();
    const hasNamespacePrefix = baseName.includes("/");
    const displayName =
      (entityType === "pod" || entityType === "service") && namespace && !hasNamespacePrefix
        ? `${namespace}/${baseName}`
        : baseName;
    const region = String((attributes as Record<string, unknown>).region ?? "AIDC-CN");
    const zone = String((attributes as Record<string, unknown>).zone ?? "zone-a");
    const domain = String((attributes as Record<string, unknown>).domain ?? "aidc");
    const cluster = String(
      (attributes as Record<string, unknown>).cluster ?? (attributes as Record<string, unknown>).cluster_id ?? "",
    ).trim();

    return {
      id: node.id,
      name: displayName || node.id,
      type: entityType,
      status: mapNodeStatus(node.status),
      layer: mapLayer(entityType),
      domain,
      region,
      zone,
      cluster: cluster || undefined,
      rack: String((attributes as Record<string, unknown>).rack ?? "") || undefined,
      slot: String((attributes as Record<string, unknown>).slot ?? "") || undefined,
      summary: `${displayName || node.id} (${node.entity_type})`,
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

function normalizeListPayload<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) {
    return payload.filter((item): item is T => !!item && typeof item === "object");
  }
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const candidate = payload as {
    data?: unknown;
    items?: unknown;
    documents?: unknown;
    results?: unknown;
  };
  const raw = candidate.data ?? candidate.items ?? candidate.documents ?? candidate.results;
  if (Array.isArray(raw)) {
    return raw.filter((item): item is T => !!item && typeof item === "object");
  }
  return [];
}

type DiagnosisStreamEvent = {
  type: string;
  session_id?: string;
  data?: Record<string, unknown>;
  [key: string]: unknown;
};

type DiagnosisStreamEventHandler = (event: DiagnosisStreamEvent) => void;

function parseSseChunk(chunk: string, onEvent: DiagnosisStreamEventHandler): void {
  const lines = chunk.split(/\r?\n/);
  const dataLines: string[] = [];
  let eventName = "message";

  lines.forEach((line) => {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim() || "message";
      return;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  });

  if (dataLines.length === 0) {
    return;
  }

  const rawData = dataLines.join("\n").trim();
  if (!rawData) {
    return;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawData);
  } catch {
    parsed = { message: rawData };
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    onEvent({ type: eventName, data: { value: parsed } });
    return;
  }

  const payload = { ...(parsed as Record<string, unknown>) };
  if (typeof payload.type !== "string" || !payload.type.trim()) {
    payload.type = eventName;
  }
  onEvent(payload as DiagnosisStreamEvent);
}

async function consumeSseResponse(
  response: Response,
  onEvent: DiagnosisStreamEventHandler,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.body) {
    throw new Error("SSE stream body is empty.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let hasWarnedAboutChunkParse = false;

  const warnChunkParseError = (rawChunk: string, error: unknown): void => {
    if (hasWarnedAboutChunkParse || !import.meta.env.DEV) {
      return;
    }
    hasWarnedAboutChunkParse = true;
    const preview = rawChunk.slice(0, 240);
    console.warn("streamDiagnosis: failed to parse SSE chunk", {
      error,
      chunkPreview: preview,
    });
  };

  const parseChunkSafely = (rawChunk: string): void => {
    const chunk = rawChunk.trim();
    if (!chunk) {
      return;
    }
    try {
      parseSseChunk(chunk, onEvent);
    } catch (error) {
      warnChunkParseError(rawChunk, error);
    }
  };

  const takeNextChunk = (): string | null => {
    const lfIndex = buffer.indexOf("\n\n");
    const crlfIndex = buffer.indexOf("\r\n\r\n");
    if (lfIndex < 0 && crlfIndex < 0) {
      return null;
    }
    let separatorIndex = lfIndex;
    let separatorLength = 2;
    if (
      crlfIndex >= 0
      && (lfIndex < 0 || crlfIndex < lfIndex)
    ) {
      separatorIndex = crlfIndex;
      separatorLength = 4;
    }
    const chunk = buffer.slice(0, separatorIndex);
    buffer = buffer.slice(separatorIndex + separatorLength);
    return chunk;
  };

  while (true) {
    if (signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    while (true) {
      const chunk = takeNextChunk();
      if (chunk === null) {
        break;
      }
      parseChunkSafely(chunk);
    }
  }

  if (buffer.trim()) {
    parseChunkSafely(buffer);
  }
}

export async function streamDiagnosis(
  alert: Alert,
  extraAlertFingerprints: string[] = [],
  onEvent: DiagnosisStreamEventHandler,
  signal?: AbortSignal,
): Promise<void> {
  if (isBlockedAlert(alert)) {
    throw new Error(`Alert '${alert.alert_name}' is temporarily filtered and cannot be processed.`);
  }

  const params = new URLSearchParams();
  extraAlertFingerprints.forEach((fingerprint) => {
    if (fingerprint) {
      params.append("extra_alert_fingerprints", fingerprint);
    }
  });

  const baseUrl = String(import.meta.env.VITE_API_BASE_URL ?? "").trim();
  const path = "/api/diagnose/stream";
  const url = `${baseUrl}${path}${params.size > 0 ? `?${params.toString()}` : ""}`;
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "Content-Type": "application/json",
    "x-trace-id": `sre-ui-${Date.now()}`,
  };
  const token = getAccessTokenSync();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(alert),
      signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") {
      throw error;
    }
    const session = await apiClient.startDiagnoseAlert(alert, extraAlertFingerprints);
    onEvent({
      type: "diagnosis_started",
      session_id: session.session_id,
      data: {
        alert,
        topology: null,
        variables: {},
        bootstrap_state: "thinking",
        degraded_start: true,
      },
    });
    return;
  }

  if (response.status === 404 || response.status === 405 || response.status === 500) {
    const session = await apiClient.startDiagnoseAlert(alert, extraAlertFingerprints);
    onEvent({
      type: "diagnosis_started",
      session_id: session.session_id,
      data: {
        alert,
        topology: null,
        variables: {},
        bootstrap_state: "thinking",
        degraded_start: true,
      },
    });
    return;
  }

  if (response.status === 401) {
    try {
      const recovered = await recoverAuthSession();
      if (recovered) {
        const refreshed = getAccessTokenSync();
        headers.Authorization = `Bearer ${refreshed}`;
        response = await fetch(url, {
          method: "POST",
          headers,
          body: JSON.stringify(alert),
          signal,
        });
      } else {
        throw new ApiRequestError(
          "Unauthorized: auth session is unrecoverable. Please re-authenticate.",
          "invalid_signature",
        );
      }
    } catch {
      throw new ApiRequestError("Unauthorized: bearer token expired or invalid.", "expired");
    }
  }

  if (!response.ok) {
    throw new ApiRequestError(`Request failed with status ${response.status}.`);
  }

  await consumeSseResponse(response, onEvent, signal);
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
        return filterAlertSnapshot(unwrapPayload(response.data));
      },
      async () => {
        const { getAlertsFallback } = await import("./devFallback");
        return filterAlertSnapshot(getAlertsFallback());
      },
      "getAlerts",
    ),

  handleAlert: async (alert: Alert) => {
    if (isBlockedAlert(alert)) {
      throw new Error(`Alert '${alert.alert_name}' is temporarily filtered and cannot be processed.`);
    }
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

  diagnoseAlert: async (alert: Alert, extraAlertFingerprints?: string[]) => {
    if (isBlockedAlert(alert)) {
      throw new Error(`Alert '${alert.alert_name}' is temporarily filtered and cannot be processed.`);
    }
    const params: Record<string, string | string[]> = {};
    if (extraAlertFingerprints && extraAlertFingerprints.length > 0) {
      params.extra_alert_fingerprints = extraAlertFingerprints;
    }
    try {
      const response = await api.post<SREApiEnvelope<DiagnosisSession> | DiagnosisSession>("/api/diagnose", alert, {
        params,
        timeout: DIAGNOSE_REQUEST_TIMEOUT_MS,
      });
      const session = unwrapPayload(response.data);
      rememberSessionId(session.session_id);
      return session;
    } catch (error) {
      const isTimeout =
        axios.isAxiosError(error) &&
        (error.code === "ECONNABORTED" ||
          String(error.message || "")
            .toLowerCase()
            .includes("timeout"));
      if (!isTimeout) {
        throw error;
      }

      for (let attempt = 0; attempt < DIAGNOSE_SESSION_POLL_ATTEMPTS; attempt += 1) {
        try {
          const summariesResponse = await api.get<SREApiEnvelope<SessionSummary[]> | SessionSummary[]>("/api/sessions", {
            params: { limit: 50 },
          });
          const summaries = normalizeSessionSummaryList(unwrapPayload(summariesResponse.data));
          const matched = summaries.find((item) => item.fingerprint === alert.fingerprint);
          if (matched) {
            const detailResponse = await api.get<SREApiEnvelope<DiagnosisSession> | DiagnosisSession>(`/api/sessions/${matched.session_id}`);
            const session = unwrapPayload(detailResponse.data);
            rememberSessionId(session.session_id);
            return session;
          }
        } catch {
          // best effort polling
        }
        await new Promise((resolve) => {
          globalThis.setTimeout(resolve, DIAGNOSE_SESSION_POLL_MS);
        });
      }

      throw new Error("\u8bca\u65ad\u8bf7\u6c42\u5df2\u63d0\u4ea4\uff0c\u4f46\u4f1a\u8bdd\u5c1a\u672a\u8fd4\u56de\uff1b\u8bf7\u7a0d\u540e\u5728\u8bca\u65ad/\u5386\u53f2\u9891\u9053\u5237\u65b0\u67e5\u770b\u3002");
    }
  },

  startDiagnoseAlert: async (alert: Alert, extraAlertFingerprints?: string[]) => {
    if (isBlockedAlert(alert)) {
      throw new Error(`Alert '${alert.alert_name}' is temporarily filtered and cannot be processed.`);
    }
    const params: Record<string, string | string[]> = {};
    if (extraAlertFingerprints && extraAlertFingerprints.length > 0) {
      params.extra_alert_fingerprints = extraAlertFingerprints;
    }
    const response = await api.post<SREApiEnvelope<DiagnosisSession> | DiagnosisSession>("/api/diagnose/start", alert, {
      params,
      validateStatus: () => true,
    });
    if (response.status === 404) {
      return apiClient.diagnoseAlert(alert, extraAlertFingerprints);
    }
    if (response.status >= 400) {
      throw new Error(`Request failed with status ${response.status}.`);
    }
    const session = unwrapPayload(response.data);
    rememberSessionId(session.session_id);
    return session;
  },

  getSessions: async (limit = 50) => {
    const response = await api.get<SREApiEnvelope<SessionSummary[]> | SessionSummary[]>("/api/sessions", { params: { limit } });
    return normalizeSessionSummaryList(unwrapPayload(response.data));
  },

  getDiagnosisSession: async (sessionId?: string) => {
    const explicit = (sessionId ?? "").trim();
    if (explicit) {
      return getDiagnosisSessionById(explicit);
    }
    const resolved = await resolveActiveSession();
    return resolved.session;
  },

  resolveActiveSession: async (sessionId?: string): Promise<ActiveSessionResolution> => {
    return resolveActiveSession(sessionId);
  },

  getDiagnosisHistorySessions: async () => {
    const sessions = await apiClient.getSessions(50);
    return sessions.map(mapSummaryToDiagnosisSummary);
  },

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

  approveRemediation: async (sessionId: string, approved: boolean, user = "ui-operator", planVersion?: number) => {
    const response = await api.post<SREApiEnvelope<RemediationResult> | RemediationResult>(`/api/remediate/${sessionId}/approve`, {
      approved,
      user,
      plan_version: planVersion,
    });
    return unwrapPayload(response.data);
  },

  reviseRemediationPlan: async (sessionId: string, instruction: string, basePlanVersion?: number) => {
    const response = await api.post<
      SREApiEnvelope<{
        session_id: string;
        plan_version: number;
        plan: RemediationPlan;
        session: DiagnosisSession;
      }>
    >(`/api/remediate/${sessionId}/plan/revise`, {
      instruction,
      base_plan_version: basePlanVersion,
    });
    return unwrapPayload(response.data);
  },

  getSessionEvents: async (sessionId: string, limit = 200, after?: string) => {
    const response = await api.get<SREApiEnvelope<SessionEvent[]> | SessionEvent[]>(`/api/sessions/${sessionId}/events`, {
      params: { limit, after },
      timeout: DIAGNOSIS_EVENTS_REQUEST_TIMEOUT_MS,
    });
    return unwrapPayload(response.data);
  },

  getRemediationOverview: async (sessionId?: string) => {
    const resolved = (sessionId ?? getRememberedSessionId()).trim();
    if (!resolved) {
      throw new Error("session_id is required");
    }
    try {
      const session = await apiClient.getDiagnosisSession(resolved);
      if (!session) {
        throw new Error("session not found");
      }
      const events = await apiClient.getSessionEvents(resolved);
      const currentPlan = session.diagnosis_result?.recommended_fix;
      if (!currentPlan) {
        throw new Error("remediation plan not found");
      }
      const revisedEvents = events.filter((event) => event.type === "plan_revised");
      const latestRevision = revisedEvents.at(-1);
      const planVersionFromPlanId = Number(/-v(\d+)$/.exec(currentPlan.plan_id)?.[1] ?? 1);
      const planVersion = Number((latestRevision?.data?.["plan_version"] as number | undefined) ?? planVersionFromPlanId);
      const remediationEvents = events.filter((event) => event.type === "remediation_progress");
      const latestRemediationEvent = remediationEvents.at(-1);
      const latestStage = String(latestRemediationEvent?.data?.["stage"] ?? "").trim().toLowerCase();
      const latestSucceededEvent = [...remediationEvents]
        .reverse()
        .find((event) => String(event.data?.["stage"] ?? "").trim().toLowerCase() === "execution_succeeded");
      const completedSteps = Number(
        latestSucceededEvent?.data?.["steps_completed"] ??
          (String(session.status ?? "").trim().toLowerCase() === "resolved" ? currentPlan.steps.length : 0),
      );
      const progressStatus = String(session.status || "").trim() || latestStage || "pending";

      // Extract canary batch status from remediation_progress events
      const batchStatusMap = new Map<string, { batch: string; progress: number; status: string }>();
      for (const event of remediationEvents) {
        const data = event.data as Record<string, unknown> | undefined;
        if (!data) continue;
        const stage = String(data.stage ?? "").trim();
        const batch = String(data.batch ?? "").trim();
        if (!batch) continue;
        if (stage === "canary_batch_started") {
          if (!batchStatusMap.has(batch)) {
            batchStatusMap.set(batch, { batch, progress: 0, status: "pending" });
          }
        } else if (stage === "canary_batch_completed") {
          batchStatusMap.set(batch, { batch, progress: 100, status: "resolved" });
        } else if (stage === "canary_check_failed") {
          const existing = batchStatusMap.get(batch);
          batchStatusMap.set(batch, { batch, progress: existing?.progress ?? 0, status: "failed" });
        } else if (stage === "canary_check_passed") {
          const batchCompleted = Number(data.batch_completed ?? 0);
          const batchTotal = Number(data.batch_total ?? 1);
          const pct = batchTotal > 0 ? Math.round((batchCompleted / batchTotal) * 100) : 50;
          batchStatusMap.set(batch, { batch, progress: pct, status: "validating" });
        }
      }
      // Also track remediating/validating for step-level progress when no canary events
      if (batchStatusMap.size === 0 && currentPlan.canary?.enabled) {
        const canaryBatches = Number((currentPlan.canary as Record<string, unknown>)?.max_batches ?? 2);
        for (let i = 1; i <= canaryBatches; i++) {
          const batchLabel = `canary-${i}`;
          if (latestStage === "remediating" || latestStage === "validating") {
            batchStatusMap.set(batchLabel, {
              batch: batchLabel,
              progress: Math.round((completedSteps / currentPlan.steps.length) * 100),
              status: latestStage,
            });
          } else {
            batchStatusMap.set(batchLabel, { batch: batchLabel, progress: 0, status: "pending" });
          }
        }
      }

      return {
        session_id: resolved,
        plan: currentPlan,
        plan_version: planVersion,
        plan_history: revisedEvents.map((event, index) => ({
          version: Number(event.data?.["plan_version"] ?? index + 2),
          plan_id: String(event.data?.["plan_id"] ?? ""),
          revised_at: event.timestamp,
          instruction: String(event.data?.["instruction"] ?? ""),
        })),
        progress: {
          status: progressStatus,
          completed_steps: completedSteps,
          total_steps: currentPlan.steps.length,
          batch_status: Array.from(batchStatusMap.values()),
        },
        timeline: events,
        approval_required: session.status === "approval_required",
      } as RemediationOverview;
    } catch {
      if (import.meta.env.DEV) {
        const { getRemediationOverviewFallback } = await import("./devFallback");
        const fallback = getRemediationOverviewFallback();
        return {
          ...fallback,
          session_id: resolved,
        } as RemediationOverview;
      }
      const loop = await apiClient.getSessionLoop(resolved);
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
        timeline: [],
        approval_required: false,
      } as RemediationOverview;
    }
  },

  postChatMessage: async (sessionOrContent: string, maybeContent?: string) => {
    const content = (maybeContent ?? sessionOrContent).trim();
    if (!content) {
      throw new Error("content is required");
    }
    const response = await api.post<
      | SREApiEnvelope<
          | { reply: string; meta?: ChatReplyMeta | null; display?: ChatDisplayPayload | null }
          | { reply: ChatMessage; meta?: ChatReplyMeta | null; display?: ChatDisplayPayload | null }
        >
      | { reply: string; meta?: ChatReplyMeta | null; display?: ChatDisplayPayload | null }
      | { reply: ChatMessage; meta?: ChatReplyMeta | null; display?: ChatDisplayPayload | null }
    >(
      "/api/chat",
      { content, session_id: maybeContent ? sessionOrContent : undefined },
      { timeout: CHAT_REQUEST_TIMEOUT_MS },
    );
    const payload = unwrapPayload(response.data);
    const responseMeta = payload.meta ?? undefined;
    const responseDisplay = payload.display ?? undefined;
    const replyValue = payload.reply;
    if (typeof replyValue === "string") {
      const metadata: Record<string, unknown> = {};
      if (maybeContent) {
        metadata.session_id = sessionOrContent;
      }
      if (responseMeta) {
        metadata.chat_meta = responseMeta;
      }
      return {
        id: `assistant-${Date.now()}`,
        role: "assistant" as const,
        content: replyValue,
        created_at: new Date().toISOString(),
        metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
        display: responseDisplay,
      };
    }
    if (responseMeta || responseDisplay) {
      return {
        ...replyValue,
        display: responseDisplay ?? replyValue.display,
        metadata: {
          ...(replyValue.metadata ?? {}),
          ...(responseMeta ? { chat_meta: responseMeta } : {}),
        },
      };
    }
    return replyValue;
  },

  getChatHistory: async (sessionId?: string) => {
    const response = await api.get<SREApiEnvelope<ChatMessage[]> | ChatMessage[]>("/api/chat/history", {
      params: sessionId ? { session_id: sessionId } : undefined,
      timeout: DIAGNOSIS_CHAT_HISTORY_REQUEST_TIMEOUT_MS,
    });
    return unwrapPayload(response.data);
  },

  getKnowledgeBases: async () =>
    withDevFallback(
      async () => {
        const response = await api.get<SREApiEnvelope<KnowledgeBaseSummary[]> | { items: KnowledgeBaseSummary[] }>("/api/knowledge/bases");
        if (isEnvelope<KnowledgeBaseSummary[]>(response.data)) {
          return unwrapPayload(response.data);
        }
        return response.data.items;
      },
      async () => {
        const { getKnowledgeBasesFallback } = await import("./devFallback");
        return getKnowledgeBasesFallback();
      },
      "getKnowledgeBases",
    ),

  getKnowledgeBaseDetail: async (knowledgeBaseId: string) =>
    withDevFallback(
      async () => {
        const response = await api.get<SREApiEnvelope<KnowledgeBaseDetail> | KnowledgeBaseDetail>(
          `/api/knowledge/bases/${encodeURIComponent(knowledgeBaseId)}`,
        );
        return unwrapPayload(response.data);
      },
      async () => {
        const { getKnowledgeBaseDetailFallback } = await import("./devFallback");
        return getKnowledgeBaseDetailFallback(knowledgeBaseId);
      },
      "getKnowledgeBaseDetail",
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

  getKnowledgeDatasets: async (keyword?: string, page = 1, limit = 50) =>
    withDevFallback(
      async () => {
        const response = await api.get<
          | SREApiEnvelope<KnowledgeDataset[]>
          | KnowledgeDataset[]
          | { items?: KnowledgeDataset[]; data?: KnowledgeDataset[] }
        >("/api/knowledge/datasets", {
          params: {
            keyword: keyword?.trim() || undefined,
            page: Math.max(1, page),
            limit: Math.max(1, limit),
          },
        });
        if (isEnvelope<KnowledgeDataset[]>(response.data)) {
          return normalizeListPayload<KnowledgeDataset>(unwrapPayload(response.data));
        }
        return normalizeListPayload<KnowledgeDataset>(response.data);
      },
      async () => {
        const { getKnowledgeBasesFallback } = await import("./devFallback");
        return getKnowledgeBasesFallback().map((item) => ({
          id: item.id,
          name: item.name,
          description: item.description,
          document_count: item.document_count,
          status: item.status,
          updated_at: item.updated_at,
        }));
      },
      "getKnowledgeDatasets",
    ),

  getKnowledgeSources: async (keyword?: string, page = 1, limit = 50, datasetId?: string) =>
    withDevFallback(
      async () => {
        const response = await api.get<
          | SREApiEnvelope<KnowledgeDocument[]>
          | KnowledgeDocument[]
          | { documents?: KnowledgeDocument[]; data?: KnowledgeDocument[]; items?: KnowledgeDocument[] }
        >("/api/knowledge/documents", {
          params: {
            keyword: keyword?.trim() || undefined,
            page: Math.max(1, page),
            limit: Math.max(1, limit),
            dataset_id: datasetId?.trim() || undefined,
          },
        });
        if (isEnvelope<KnowledgeDocument[]>(response.data)) {
          return normalizeListPayload<KnowledgeDocument>(unwrapPayload(response.data));
        }
        return normalizeListPayload<KnowledgeDocument>(response.data);
      },
      async () => {
        const { getKnowledgeSourcesFallback } = await import("./devFallback");
        const normalizedKeyword = keyword?.trim().toLowerCase() ?? "";
        return getKnowledgeSourcesFallback().filter((item) => {
          if (!normalizedKeyword) {
            return true;
          }
          const haystack = `${item.title} ${item.excerpt} ${item.tags.join(" ")}`.toLowerCase();
          return haystack.includes(normalizedKeyword);
        });
      },
      "getKnowledgeSources",
    ),

  searchKnowledgeSegments: async (query: string, topK = 5, datasetId?: string) =>
    withDevFallback(
      async () => {
        const response = await api.get<
          SREApiEnvelope<KnowledgeSearchHit[]> | KnowledgeSearchHit[] | { results?: KnowledgeSearchHit[]; data?: KnowledgeSearchHit[] }
        >("/api/knowledge/segments/search", {
          params: {
            query,
            top_k: Math.max(1, topK),
            dataset_id: datasetId?.trim() || undefined,
          },
        });
        if (isEnvelope<KnowledgeSearchHit[]>(response.data)) {
          return normalizeListPayload<KnowledgeSearchHit>(unwrapPayload(response.data));
        }
        return normalizeListPayload<KnowledgeSearchHit>(response.data);
      },
      async () => {
        const { searchKnowledgeFallback } = await import("./devFallback");
        return searchKnowledgeFallback(query).map((item, index) => ({
          id: `segment-${item.id}-${index}`,
          document_id: item.id,
          content: item.excerpt,
          source: item.source,
          score: item.score,
          metadata: { title: item.title, tags: item.tags },
        }));
      },
      "searchKnowledgeSegments",
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

  getSkill: async (skillId: string) =>
    withDevFallback(
      async () => {
        try {
          const response = await api.get<SREApiEnvelope<SkillDescriptor> | SkillDescriptor>(
            `/api/skills/${encodeURIComponent(skillId)}`,
          );
          return unwrapPayload(response.data);
        } catch (error) {
          if (isHttpStatusError(error, 404)) {
            const skills = await apiClient.getSkills();
            const matched = skills.find((skill) => skill.id === skillId);
            if (matched) {
              return matched;
            }
            throw new Error("\u672a\u627e\u5230\u5bf9\u5e94\u6280\u80fd");
          }
          throw error;
        }
      },
      async () => {
        const { getSkillFallback } = await import("./devFallback");
        return getSkillFallback(skillId);
      },
      "getSkill",
    ),

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

  getLLMRuntimeStatus: async () => {
    const response = await api.get<SREApiEnvelope<LLMRuntimeStatus> | LLMRuntimeStatus>("/api/runtime/llm/status");
    return unwrapPayload(response.data);
  },
};

export type ApiClient = typeof apiClient;
