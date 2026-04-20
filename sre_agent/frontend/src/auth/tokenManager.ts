import type { SREApiEnvelope } from "../api/types";

export type AuthErrorKind = "expired" | "invalid_signature" | "missing" | "unknown";

type AuthTokenPayload = {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
  expires_at?: string;
  refresh_expires_at?: string;
  server_boot_id?: string;
  auth_error_kind?: AuthErrorKind;
};

type AuthStatusPayload = {
  server_boot_id?: string;
  access_token_expires_at?: string | null;
  skew_hint_seconds?: number;
  auth_error_kind?: AuthErrorKind;
};

const ACCESS_TOKEN_STORAGE_KEY = "sre_access_token";
const REFRESH_TOKEN_STORAGE_KEY = "sre_refresh_token";
const ACCESS_EXPIRES_AT_STORAGE_KEY = "sre_access_expires_at";
const SERVER_BOOT_ID_STORAGE_KEY = "sre_server_boot_id";
const AUTH_ERROR_KIND_STORAGE_KEY = "sre_auth_error_kind";
const REFRESH_AHEAD_MS = 5 * 60 * 1000;

let accessToken = "";
let refreshToken = "";
let accessExpiresAt: string | null = null;
let serverBootId = "";
let authErrorKind: AuthErrorKind = "unknown";
let initialized = false;
let refreshInFlight: Promise<string> | null = null;
let monitorTimer: number | null = null;

function isBrowser() {
  return typeof window !== "undefined";
}

function resolveApiBaseUrl(): string {
  const configured = String(import.meta.env.VITE_API_BASE_URL ?? "").trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  if (isBrowser()) {
    return window.location.origin;
  }
  return "";
}

function toAuthKind(value: unknown): AuthErrorKind {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "expired" || normalized === "invalid_signature" || normalized === "missing") {
    return normalized;
  }
  return "unknown";
}

function saveToStorage() {
  if (!isBrowser()) {
    return;
  }
  window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, accessToken);
  window.localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, refreshToken);
  if (accessExpiresAt) {
    window.localStorage.setItem(ACCESS_EXPIRES_AT_STORAGE_KEY, accessExpiresAt);
  } else {
    window.localStorage.removeItem(ACCESS_EXPIRES_AT_STORAGE_KEY);
  }
  if (serverBootId) {
    window.localStorage.setItem(SERVER_BOOT_ID_STORAGE_KEY, serverBootId);
  } else {
    window.localStorage.removeItem(SERVER_BOOT_ID_STORAGE_KEY);
  }
  window.localStorage.setItem(AUTH_ERROR_KIND_STORAGE_KEY, authErrorKind);
}

function loadFromStorage() {
  if (!isBrowser()) {
    return;
  }
  accessToken = window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY) ?? "";
  refreshToken = window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY) ?? "";
  accessExpiresAt = window.localStorage.getItem(ACCESS_EXPIRES_AT_STORAGE_KEY);
  serverBootId = window.localStorage.getItem(SERVER_BOOT_ID_STORAGE_KEY) ?? "";
  authErrorKind = toAuthKind(window.localStorage.getItem(AUTH_ERROR_KIND_STORAGE_KEY));
}

function applyBootstrapEnv() {
  const envAccessToken = String(import.meta.env.VITE_API_TOKEN ?? "").trim();
  const envRefreshToken = String(import.meta.env.VITE_API_REFRESH_TOKEN ?? "").trim();
  if (!accessToken && envAccessToken) {
    accessToken = envAccessToken;
  }
  if (!refreshToken && envRefreshToken) {
    refreshToken = envRefreshToken;
  }
}

function initializeIfNeeded() {
  if (initialized) {
    return;
  }
  loadFromStorage();
  applyBootstrapEnv();
  saveToStorage();
  initialized = true;
}

function extractEnvelopeData<T>(payload: unknown): T {
  if (payload && typeof payload === "object" && "success" in (payload as Record<string, unknown>)) {
    const envelope = payload as SREApiEnvelope<T>;
    if (!envelope.success || envelope.data == null) {
      throw new Error(envelope.error?.message ?? "auth response has no data");
    }
    return envelope.data;
  }
  return payload as T;
}

function applyTokenPayload(payload: AuthTokenPayload) {
  accessToken = String(payload.access_token ?? "").trim();
  refreshToken = String(payload.refresh_token ?? refreshToken).trim();
  accessExpiresAt = payload.expires_at ? String(payload.expires_at) : accessExpiresAt;
  if (payload.server_boot_id) {
    serverBootId = String(payload.server_boot_id);
  }
  authErrorKind = toAuthKind(payload.auth_error_kind);
  saveToStorage();
}

async function postJson<T>(path: string, body: Record<string, unknown>, headers: Record<string, string> = {}): Promise<T> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `request failed: ${response.status}`);
  }
  const payload = await response.json();
  return extractEnvelopeData<T>(payload);
}

async function getJson<T>(path: string, headers: Record<string, string> = {}): Promise<T> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}${path}`, {
    method: "GET",
    headers,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `request failed: ${response.status}`);
  }
  const payload = await response.json();
  return extractEnvelopeData<T>(payload);
}

function isTokenExpiringSoon() {
  if (!accessExpiresAt) {
    return false;
  }
  const expiresAtMs = Date.parse(accessExpiresAt);
  if (!Number.isFinite(expiresAtMs)) {
    return false;
  }
  return expiresAtMs - Date.now() <= REFRESH_AHEAD_MS;
}

export function getAccessTokenSync() {
  initializeIfNeeded();
  return accessToken;
}

export function hasAccessToken() {
  return getAccessTokenSync().trim().length > 0;
}

export function setAuthErrorKind(kind: AuthErrorKind) {
  initializeIfNeeded();
  authErrorKind = kind;
  saveToStorage();
}

export function getAuthErrorKind(): AuthErrorKind {
  initializeIfNeeded();
  return authErrorKind;
}

export function updateServerBootId(nextBootId: string) {
  initializeIfNeeded();
  const normalized = String(nextBootId ?? "").trim();
  if (!normalized) {
    return false;
  }
  const changed = Boolean(serverBootId) && serverBootId !== normalized;
  serverBootId = normalized;
  saveToStorage();
  return changed;
}

export async function refreshAccessToken() {
  initializeIfNeeded();
  if (refreshInFlight) {
    return refreshInFlight;
  }
  refreshInFlight = (async () => {
    try {
      if (refreshToken) {
        const refreshed = await postJson<AuthTokenPayload>("/api/auth/refresh", { refresh_token: refreshToken });
        applyTokenPayload(refreshed);
        return accessToken;
      }
      if (!accessToken) {
        throw new Error("missing access token");
      }
      const issued = await postJson<AuthTokenPayload>(
        "/api/auth/token",
        {},
        { Authorization: `Bearer ${accessToken}` },
      );
      applyTokenPayload(issued);
      return accessToken;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export async function checkAuthStatusAndHeal() {
  initializeIfNeeded();
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const status = await getJson<AuthStatusPayload>("/api/auth/status", headers);
  const bootChanged = status.server_boot_id ? updateServerBootId(status.server_boot_id) : false;
  authErrorKind = toAuthKind(status.auth_error_kind);
  if (status.access_token_expires_at) {
    accessExpiresAt = String(status.access_token_expires_at);
  }
  saveToStorage();
  if (bootChanged || authErrorKind === "expired" || authErrorKind === "invalid_signature" || isTokenExpiringSoon()) {
    await refreshAccessToken();
  }
}

export function startAuthSessionMonitor(intervalMs = 60_000) {
  if (!isBrowser()) {
    return () => undefined;
  }
  initializeIfNeeded();
  if (monitorTimer !== null) {
    window.clearInterval(monitorTimer);
    monitorTimer = null;
  }
  monitorTimer = window.setInterval(() => {
    void checkAuthStatusAndHeal().catch(() => undefined);
  }, Math.max(10_000, intervalMs));
  return () => {
    if (monitorTimer !== null) {
      window.clearInterval(monitorTimer);
      monitorTimer = null;
    }
  };
}

export function __resetTokenManagerForTests() {
  accessToken = "";
  refreshToken = "";
  accessExpiresAt = null;
  serverBootId = "";
  authErrorKind = "unknown";
  initialized = false;
  refreshInFlight = null;
  if (monitorTimer !== null && isBrowser()) {
    window.clearInterval(monitorTimer);
  }
  monitorTimer = null;
  if (isBrowser()) {
    window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(ACCESS_EXPIRES_AT_STORAGE_KEY);
    window.localStorage.removeItem(SERVER_BOOT_ID_STORAGE_KEY);
    window.localStorage.removeItem(AUTH_ERROR_KIND_STORAGE_KEY);
  }
}
