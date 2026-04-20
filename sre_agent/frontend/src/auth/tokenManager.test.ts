import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetTokenManagerForTests,
  getAuthRecoveryState,
  getAccessTokenSync,
  recoverAuthSession,
  refreshAccessToken,
  startAuthSessionMonitor,
} from "./tokenManager";

describe("tokenManager", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    __resetTokenManagerForTests();
  });

  afterEach(() => {
    __resetTokenManagerForTests();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("bootstraps access token from VITE_API_TOKEN", () => {
    vi.stubEnv("VITE_API_TOKEN", "bootstrap-access");
    expect(getAccessTokenSync()).toBe("bootstrap-access");
  });

  it("deduplicates concurrent refresh requests (single flight)", async () => {
    vi.stubEnv("VITE_API_TOKEN", "bootstrap-access");
    vi.stubEnv("VITE_API_REFRESH_TOKEN", "bootstrap-refresh");

    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          success: true,
          data: {
            access_token: "new-access",
            refresh_token: "new-refresh",
            token_type: "Bearer",
            expires_at: "2026-04-20T18:00:00Z",
            refresh_expires_at: "2026-04-21T18:00:00Z",
            server_boot_id: "boot-a",
            auth_error_kind: "unknown",
          },
          trace_id: "trace-auth-refresh",
          timestamp: "2026-04-20T10:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const [left, right] = await Promise.all([refreshAccessToken(), refreshAccessToken()]);
    expect(left).toBe("new-access");
    expect(right).toBe("new-access");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getAccessTokenSync()).toBe("new-access");
  });

  it("starts and stops auth session monitor safely", () => {
    const stop = startAuthSessionMonitor(20_000);
    vi.advanceTimersByTime(20_000);
    stop();
    expect(true).toBe(true);
  });

  it("recovers from invalid signature by clearing stale tokens and using bootstrap endpoint", async () => {
    window.localStorage.setItem("sre_access_token", "stale-access");
    window.localStorage.setItem("sre_refresh_token", "stale-refresh");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "token signature verification failed" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            success: true,
            data: {
              access_token: "bootstrap-access",
              refresh_token: "bootstrap-refresh",
              token_type: "Bearer",
              expires_at: "2026-04-20T18:00:00Z",
              refresh_expires_at: "2026-04-21T18:00:00Z",
              server_boot_id: "boot-b",
              auth_error_kind: "unknown",
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const refreshed = await refreshAccessToken();

    expect(refreshed).toBe("bootstrap-access");
    expect(getAccessTokenSync()).toBe("bootstrap-access");
    expect(window.localStorage.getItem("sre_access_token")).toBe("bootstrap-access");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/api/auth/refresh");
    expect(fetchMock.mock.calls[1]?.[0]).toContain("/api/auth/bootstrap");
  });

  it("enters terminal state when recovery and bootstrap both fail", async () => {
    window.localStorage.setItem("sre_access_token", "stale-access");
    window.localStorage.setItem("sre_refresh_token", "stale-refresh");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "token signature verification failed" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "demo auth bootstrap is disabled" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const recovered = await recoverAuthSession();

    expect(recovered).toBe(false);
    expect(getAuthRecoveryState()).toBe("terminal");
  });
});
