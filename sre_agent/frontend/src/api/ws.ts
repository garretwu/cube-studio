import type { WSEvent } from "./types";
import { WS_EVENT_TYPES } from "./generated/backend-contract";

type WSQueryValue = string | number | boolean | null | undefined;

type ManagedWSOptions = {
  maxBufferedMessages?: number;
  maxBatchSize?: number;
  reconnectBaseMs?: number;
  maxReconnectAttempts?: number;
  getToken?: () => string | Promise<string>;
  onAuthFailure?: (reason: string) => void | Promise<void>;
  onEvent?: (event: WSEvent) => void;
  onStateChange?: (state: "connecting" | "open" | "closed" | "error") => void;
};

function resolveHttpApiBase(apiBaseUrl?: string): string {
  const configured = (apiBaseUrl ?? import.meta.env.VITE_API_BASE_URL ?? "").trim();
  if (configured) {
    return configured;
  }
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin;
  }
  return "http://localhost";
}

function toWsOrigin(httpBase: string): string {
  const fallbackOrigin =
    typeof window !== "undefined" ? window.location.origin : "http://localhost";
  try {
    const parsed = new URL(httpBase, fallbackOrigin);
    const wsProtocol = parsed.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//${parsed.host}`;
  } catch {
    const parsedFallback = new URL(fallbackOrigin);
    const wsProtocol = parsedFallback.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//${parsedFallback.host}`;
  }
}

export function buildBackendWsUrl(
  path: string,
  query: Record<string, WSQueryValue> = {},
  apiBaseUrl?: string,
): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const base = `${toWsOrigin(resolveHttpApiBase(apiBaseUrl))}/`;
  const target = new URL(normalizedPath, base);
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null) {
      return;
    }
    const text = String(value).trim();
    if (!text) {
      return;
    }
    target.searchParams.set(key, text);
  });
  return target.toString();
}

export class ManagedWebSocket {
  private readonly url: string;
  private readonly options: Required<ManagedWSOptions>;
  private socket: WebSocket | null = null;
  private reconnectAttempts = 0;
  private closedManually = false;
  private inboundQueue: WSEvent[] = [];
  private flushTimer: number | null = null;
  private lastEventId: string | null = null;

  constructor(url: string, options: ManagedWSOptions = {}) {
    this.url = url;
    this.options = {
      maxBufferedMessages: options.maxBufferedMessages ?? 200,
      maxBatchSize: options.maxBatchSize ?? 10,
      reconnectBaseMs: options.reconnectBaseMs ?? 800,
      maxReconnectAttempts: options.maxReconnectAttempts ?? 6,
      getToken: options.getToken ?? (() => ""),
      onAuthFailure: options.onAuthFailure ?? (() => undefined),
      onEvent: options.onEvent ?? (() => undefined),
      onStateChange: options.onStateChange ?? (() => undefined),
    };
  }

  connect() {
    if (typeof window === "undefined" || typeof WebSocket === "undefined") {
      return;
    }
    this.closedManually = false;
    this.options.onStateChange("connecting");
    const connectUrl = this.buildConnectUrl();
    if (typeof connectUrl === "string") {
      this.openSocket(connectUrl);
      return;
    }
    void connectUrl
      .then((url) => this.openSocket(url))
      .catch(() => {
        this.options.onStateChange("error");
        this.scheduleReconnect();
      });
  }

  private openSocket(connectUrl: string) {
    if (this.closedManually) {
      return;
    }
    this.socket = new WebSocket(connectUrl);

    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.options.onStateChange("open");
    };

    this.socket.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data as string) as WSEvent;
        if (!WS_EVENT_TYPES.includes(event.type)) {
          console.warn("[ws] unknown event type from backend:", event.type);
          return;
        }
        if (this.inboundQueue.length >= this.options.maxBufferedMessages) {
          this.inboundQueue.shift();
        }
        const rawEventId = event.data?.event_id;
        if (typeof rawEventId === "string" || typeof rawEventId === "number") {
          this.lastEventId = String(rawEventId);
        }
        this.inboundQueue.push(event);
        this.scheduleFlush();
      } catch {
        this.options.onStateChange("error");
      }
    };

    this.socket.onerror = () => {
      this.options.onStateChange("error");
    };

    this.socket.onclose = (event) => {
      this.options.onStateChange("closed");
      if (!this.closedManually) {
        const code = Number((event as { code?: number } | undefined)?.code ?? 0);
        const reason = String((event as { reason?: string } | undefined)?.reason ?? "");
        if (code === 4001 || reason.toLowerCase().includes("token")) {
          Promise.resolve(this.options.onAuthFailure(reason))
            .catch(() => undefined)
            .finally(() => this.scheduleReconnect());
          return;
        }
        this.scheduleReconnect();
      }
    };
  }

  close() {
    this.closedManually = true;
    if (this.flushTimer !== null) {
      window.clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    this.socket?.close();
    this.socket = null;
  }

  private scheduleFlush() {
    if (this.flushTimer !== null) {
      return;
    }
    this.flushTimer = window.setTimeout(() => {
      this.flushTimer = null;
      const events = this.inboundQueue.splice(0, this.options.maxBatchSize);
      events.forEach((event) => this.options.onEvent(event));
      if (this.inboundQueue.length > 0) {
        this.scheduleFlush();
      }
    }, 60);
  }

  private scheduleReconnect() {
    if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
      this.options.onStateChange("closed");
      return;
    }
    const timeout = Math.min(10000, this.options.reconnectBaseMs * 2 ** this.reconnectAttempts);
    this.reconnectAttempts += 1;
    window.setTimeout(() => this.connect(), timeout);
  }

  private buildConnectUrl(): string | Promise<string> {
    try {
      const maybeToken = this.options.getToken();
      if (typeof maybeToken === "string") {
        return this.formatConnectUrl(maybeToken.trim());
      }
      return Promise.resolve(maybeToken)
        .then((token) => this.formatConnectUrl(String(token).trim()))
        .catch(() => this.formatConnectUrl(""));
    } catch {
      return this.formatConnectUrl("");
    }
  }

  private formatConnectUrl(token: string): string {
    try {
      const parsed = new URL(this.url);
      if (token) {
        parsed.searchParams.set("token", token);
      }
      if (this.lastEventId) {
        parsed.searchParams.set("last_event_id", this.lastEventId);
      } else {
        parsed.searchParams.delete("last_event_id");
      }
      return parsed.toString();
    } catch {
      const separator = this.url.includes("?") ? "&" : "?";
      const params: string[] = [];
      if (token) {
        params.push(`token=${encodeURIComponent(token)}`);
      }
      if (this.lastEventId) {
        params.push(`last_event_id=${encodeURIComponent(this.lastEventId)}`);
      }
      if (params.length === 0) {
        return this.url;
      }
      return `${this.url}${separator}${params.join("&")}`;
    }
  }
}
