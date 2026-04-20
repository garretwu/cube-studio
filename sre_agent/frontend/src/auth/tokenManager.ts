import type { SREApiEnvelope } from "../api/types";

export type AuthErrorKind = "expired" | "invalid_signature" | "missing" | "unknown";
export type AuthRecoveryState = "healthy" | "recovering" | "terminal";

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
let recoveryState: AuthRecoveryState = "healthy";
let initialized = false;
let refreshInFlight: Promise<string> | null = null;
let recoveryInFlight: Promise<boolean> | null = null;
let monitorTimer: number | null = null;
const recoveryListeners = new Set<(state: AuthRecoveryState) => void>();

class AuthHttpError extends Error {
  status: number;
  authKind: AuthErrorKind;

  constructor(message: string, status: number, authKind: AuthErrorKind) {
    super(message);
    this.name = "AuthHttpError";
    this.status = status;
    this.authKind = authKind;
  }
}

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

function classifyAuthKindFromText(value: string): AuthErrorKind {
  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return "unknown";
  }
  if (normalized.includes("signature")) {
    return "invalid_signature";
  }
  if (normalized.includes("expired")) {
    return "expired";
  }
  if (normalized.includes("missing")) {
    return "missing";
  }
  return "unknown";
}

function setRecoveryState(next: AuthRecoveryState) {
  if (recoveryState === next) {
    return;
  }
  recoveryState = next;
  recoveryListeners.forEach((listener) => {
    try {
      listener(next);
    } catch {
      // no-op: listeners should never break auth flow
    }
  });
}

function saveToStorage() {
  if (!isBrowser()) {
    return;
  }
  if (accessToken) {
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, accessToken);
  } else {
    window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  }
  if (refreshToken) {
    window.localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, refreshToken);
  } else {
    window.localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
  }
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
      const message = String(envelope.error?.message ?? "auth response has no data");
      throw new AuthHttpError(message, 400, classifyAuthKindFromText(message));
    }
    return envelope.data;
  }
  return payload as T;
}

function applyTokenPayload(payload: AuthTokenPayload) {
  accessToken = String(payload.access_token ?? "").trim();
  refreshToken = String(payload.refresh_token ?? refreshToken).trim();
  accessExpiresAt = payload.expires_at ? String(payload.expires_at) : null;
  if (payload.server_boot_id) {
    serverBootId = String(payload.server_boot_id);
  }
  authErrorKind = toAuthKind(payload.auth_error_kind);
  saveToStorage();
}

function clearAuthTokens() {
  accessToken = "";
  refreshToken = "";
  accessExpiresAt = null;
  authErrorKind = "unknown";
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
    throw new AuthHttpError(text || `request failed: ${response.status}`, response.status, classifyAuthKindFromText(text));
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
    throw new AuthHttpError(text || `request failed: ${response.status}`, response.status, classifyAuthKindFromText(text));
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

async function issueBootstrapToken() {
  const issued = await postJson<AuthTokenPayload>("/api/auth/bootstrap", {});
  applyTokenPayload(issued);
  return accessToken;
}

async function issueTokenFromRefreshOrAccess() {
  if (refreshToken) {
    const refreshed = await postJson<AuthTokenPayload>("/api/auth/refresh", { refresh_token: refreshToken });
    applyTokenPayload(refreshed);
    return accessToken;
  }
  if (!accessToken) {
    throw new AuthHttpError("missing access token", 401, "missing");
  }
  const issued = await postJson<AuthTokenPayload>(
    "/api/auth/token",
    {},
    { Authorization: `Bearer ${accessToken}` },
  );
  applyTokenPayload(issued);
  return accessToken;
}

function shouldAttemptBootstrap(error: unknown) {
  if (error instanceof AuthHttpError) {
    if (error.authKind === "invalid_signature" || error.authKind === "missing") {
      return true;
    }
    return error.status === 401;
  }
  return false;
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

export function getAuthRecoveryState(): AuthRecoveryState {
  initializeIfNeeded();
  return recoveryState;
}

export function subscribeAuthRecoveryState(listener: (state: AuthRecoveryState) => void) {
  initializeIfNeeded();
  recoveryListeners.add(listener);
  listener(recoveryState);
  return () => {
    recoveryListeners.delete(listener);
  };
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
  if (recoveryState === "terminal") {
    throw new Error("auth recovery is in terminal state");
  }
  if (refreshInFlight) {
    return refreshInFlight;
  }
  refreshInFlight = (async () => {
    try {
      return await issueTokenFromRefreshOrAccess();
    } catch (error) {
      if (!shouldAttemptBootstrap(error)) {
        throw error;
      }
      clearAuthTokens();
      const bootstrapped = await issueBootstrapToken();
      setRecoveryState("healthy");
      return bootstrapped;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export async function recoverAuthSession() {
  initializeIfNeeded();
  if (recoveryInFlight) {
    return recoveryInFlight;
  }
  recoveryInFlight = (async () => {
    setRecoveryState("recovering");
    try {
      await refreshAccessToken();
      setRecoveryState("healthy");
      return true;
    } catch {
      setRecoveryState("terminal");
      return false;
    } finally {
      recoveryInFlight = null;
    }
  })();
  return recoveryInFlight;
}

export async function checkAuthStatusAndHeal() {
  initializeIfNeeded();
  if (recoveryState === "terminal") {
    return;
  }
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
  if (bootChanged || authErrorKind === "invalid_signature") {
    await recoverAuthSession();
    return;
  }
  if (authErrorKind === "expired" || isTokenExpiringSoon()) {
    try {
      await refreshAccessToken();
    } catch {
      setRecoveryState("terminal");
    }
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
    if (recoveryState === "terminal") {
      return;
    }
    void checkAuthStatusAndHeal().catch(() => undefined);
  }, Math.max(10_000, intervalMs));
  return () => {
    if (monitorTimer !== null) {
      window.clearInterval(monitorTimer);
      monitorTimer = null;
    }
  };
}

export function hardReload() {
  initializeIfNeeded();
  clearAuthTokens();
  if (isBrowser()) {
    window.location.reload();
  }
}

export function __resetTokenManagerForTests() {
  accessToken = "";
  refreshToken = "";
  accessExpiresAt = null;
  serverBootId = "";
  authErrorKind = "unknown";
  setRecoveryState("healthy");
  initialized = false;
  refreshInFlight = null;
  recoveryInFlight = null;
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
  recoveryListeners.clear();
}
