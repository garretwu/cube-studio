import type { WSEvent } from "./types";
import { WS_EVENT_TYPES } from "./generated/backend-contract";

type ManagedWSOptions = {
  maxBufferedMessages?: number;
  maxBatchSize?: number;
  reconnectBaseMs?: number;
  onEvent?: (event: WSEvent) => void;
  onStateChange?: (state: "connecting" | "open" | "closed" | "error") => void;
};

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
    this.socket = new WebSocket(this.buildConnectUrl());

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

    this.socket.onclose = () => {
      this.options.onStateChange("closed");
      if (!this.closedManually) {
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
    const timeout = Math.min(10000, this.options.reconnectBaseMs * 2 ** this.reconnectAttempts);
    this.reconnectAttempts += 1;
    window.setTimeout(() => this.connect(), timeout);
  }

  private buildConnectUrl(): string {
    if (!this.lastEventId) {
      return this.url;
    }
    try {
      const parsed = new URL(this.url);
      parsed.searchParams.set("last_event_id", this.lastEventId);
      return parsed.toString();
    } catch {
      const separator = this.url.includes("?") ? "&" : "?";
      return `${this.url}${separator}last_event_id=${encodeURIComponent(this.lastEventId)}`;
    }
  }
}
