import { afterEach, describe, expect, it, vi } from "vitest";

import { streamDiagnosis } from "./client";

const streamAlert = {
  alert_name: "LatencyHigh",
  severity: "warning" as const,
  labels: { service: "auth-svc" },
  annotations: {},
  starts_at: "2026-03-26T00:00:00Z",
  fingerprint: "fp-stream-sse",
  status: "firing" as const,
};

function makeSseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamDiagnosis SSE framing", () => {
  it("parses frames split by LF separator", async () => {
    const fetchMock = vi.fn(async () => makeSseResponse([
      [
        "event: token_delta",
        'data: {"type":"token_delta","session_id":"sess-lf","data":{"content":"LF"}}',
        "",
        "event: done",
        'data: {"type":"done","session_id":"sess-lf","data":{}}',
        "",
      ].join("\n"),
    ]));
    vi.stubGlobal("fetch", fetchMock);

    const events: Array<{ type: string; session_id?: string; data?: Record<string, unknown> }> = [];
    await streamDiagnosis(streamAlert, [], (event) => events.push(event));

    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({
      type: "token_delta",
      session_id: "sess-lf",
      data: { content: "LF" },
    });
    expect(events[1]).toMatchObject({
      type: "done",
      session_id: "sess-lf",
    });
  });

  it("parses frames split by CRLF separator", async () => {
    const fetchMock = vi.fn(async () => makeSseResponse([
      [
        "event: token_delta",
        'data: {"type":"token_delta","session_id":"sess-crlf","data":{"content":"CRLF"}}',
        "",
        "event: done",
        'data: {"type":"done","session_id":"sess-crlf","data":{}}',
        "",
      ].join("\r\n"),
    ]));
    vi.stubGlobal("fetch", fetchMock);

    const events: Array<{ type: string; session_id?: string; data?: Record<string, unknown> }> = [];
    await streamDiagnosis(streamAlert, [], (event) => events.push(event));

    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({
      type: "token_delta",
      session_id: "sess-crlf",
      data: { content: "CRLF" },
    });
    expect(events[1]).toMatchObject({
      type: "done",
      session_id: "sess-crlf",
    });
  });

  it("parses mixed chunks with LF and CRLF separators", async () => {
    const fetchMock = vi.fn(async () => makeSseResponse([
      "event: token_delta\r\n",
      'data: {"type":"token_delta","session_id":"sess-mixed","data":{"content":"A"}}',
      "\r\n\r\nevent: token_delta\n",
      'data: {"type":"token_delta","session_id":"sess-mixed","data":{"content":"B"}}\n',
      "\n",
      "event: done\r\n",
      'data: {"type":"done","session_id":"sess-mixed","data":{}}\r\n\r\n',
    ]));
    vi.stubGlobal("fetch", fetchMock);

    const events: Array<{ type: string; session_id?: string; data?: Record<string, unknown> }> = [];
    await streamDiagnosis(streamAlert, [], (event) => events.push(event));

    expect(events).toHaveLength(3);
    expect(events[0]).toMatchObject({
      type: "token_delta",
      session_id: "sess-mixed",
      data: { content: "A" },
    });
    expect(events[1]).toMatchObject({
      type: "token_delta",
      session_id: "sess-mixed",
      data: { content: "B" },
    });
    expect(events[2]).toMatchObject({
      type: "done",
      session_id: "sess-mixed",
    });
  });
});

