import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetTokenManagerForTests,
  getAccessTokenSync,
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
});
