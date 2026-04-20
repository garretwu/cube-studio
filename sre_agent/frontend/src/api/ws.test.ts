import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildBackendWsUrl, ManagedWebSocket } from "./ws";

type MessageHandler = ((event: { data: string }) => void) | null;
type VoidHandler = ((event?: { code: number; reason: string }) => void) | null;

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  onopen: VoidHandler = null;
  onmessage: MessageHandler = null;
  onerror: VoidHandler = null;
  onclose: VoidHandler = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.onclose?.({ code: 1000, reason: "" });
  }

  emitOpen() {
    this.onopen?.();
  }

  emitMessage(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  emitClose(code = 1000, reason = "") {
    this.onclose?.({ code, reason });
  }
}

describe("ManagedWebSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("reconnects with last_event_id when stream reconnects", () => {
    const ws = new ManagedWebSocket("ws://localhost/ws/alerts?token=t", {
      reconnectBaseMs: 1,
    });

    ws.connect();
    expect(FakeWebSocket.instances).toHaveLength(1);
    const first = FakeWebSocket.instances[0]!;
    expect(first.url).toBe("ws://localhost/ws/alerts?token=t");

    first.emitOpen();
    first.emitMessage({
      schema_version: "1.0",
      type: "alert",
      session_id: "alerts",
      timestamp: "2026-03-27T00:00:00Z",
      data: { event_id: "7" },
    });
    vi.advanceTimersByTime(100);

    first.emitClose();
    vi.advanceTimersByTime(2);

    expect(FakeWebSocket.instances).toHaveLength(2);
    const second = FakeWebSocket.instances[1]!;
    expect(second.url).toContain("last_event_id=7");
    ws.close();
  });

  it("applies inbound backpressure by dropping oldest buffered messages", () => {
    const receivedIds: string[] = [];
    const ws = new ManagedWebSocket("ws://localhost/ws/thinking-trace/s1?token=t", {
      maxBufferedMessages: 2,
      onEvent: (event) => {
        const eventId = event.data?.event_id;
        if (typeof eventId === "string" || typeof eventId === "number") {
          receivedIds.push(String(eventId));
        }
      },
    });

    ws.connect();
    const first = FakeWebSocket.instances[0]!;
    first.emitOpen();
    first.emitMessage({
      schema_version: "1.0",
      type: "thinking_step",
      session_id: "s1",
      timestamp: "2026-03-27T00:00:00Z",
      data: { event_id: "1" },
    });
    first.emitMessage({
      schema_version: "1.0",
      type: "thinking_step",
      session_id: "s1",
      timestamp: "2026-03-27T00:00:01Z",
      data: { event_id: "2" },
    });
    first.emitMessage({
      schema_version: "1.0",
      type: "thinking_step",
      session_id: "s1",
      timestamp: "2026-03-27T00:00:02Z",
      data: { event_id: "3" },
    });

    vi.advanceTimersByTime(100);
    expect(receivedIds).toEqual(["2", "3"]);
    ws.close();
  });

  it("refreshes auth and reconnects when websocket closes with auth failure code", async () => {
    const onAuthFailure = vi.fn(async () => undefined);
    const getToken = vi.fn(() => "new-token");
    const ws = new ManagedWebSocket("ws://localhost/ws/alerts", {
      reconnectBaseMs: 1,
      getToken,
      onAuthFailure,
    });

    ws.connect();
    expect(FakeWebSocket.instances).toHaveLength(1);
    const first = FakeWebSocket.instances[0]!;
    expect(first.url).toContain("token=new-token");
    first.emitOpen();
    first.emitClose(4001, "token expired");

    await vi.runAllTimersAsync();
    expect(onAuthFailure).toHaveBeenCalledTimes(1);
    expect(FakeWebSocket.instances.length).toBeGreaterThanOrEqual(2);
    ws.close();
  });
});

describe("buildBackendWsUrl", () => {
  it("uses backend host/port from API base URL", () => {
    const url = buildBackendWsUrl(
      "/ws/alerts",
      { token: "t", last_event_id: 3 },
      "http://127.0.0.1:18090/api",
    );
    expect(url).toBe("ws://127.0.0.1:18090/ws/alerts?token=t&last_event_id=3");
  });

  it("falls back to browser origin when API base URL is empty", () => {
    const url = new URL(buildBackendWsUrl("/ws/alerts", { token: "t" }, ""));
    const expectedProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    expect(url.protocol).toBe(expectedProtocol);
    expect(url.host).toBe(window.location.host);
    expect(url.pathname).toBe("/ws/alerts");
    expect(url.searchParams.get("token")).toBe("t");
  });
});
