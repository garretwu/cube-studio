import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { DiagnosisSession } from "../api/types";
import { server } from "../test/server";
import DiagnosisPage from "./Diagnosis";

function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location-search">{location.search}</p>;
}

function makeSession(sessionId: string, rootCause: string): DiagnosisSession {
  return {
    session_id: sessionId,
    alert: {
      alert_name: "GPUUtilizationHigh",
      severity: "warning",
      labels: {},
      annotations: {},
      starts_at: "2026-03-26T00:00:00Z",
      fingerprint: `fp-${sessionId}`,
      status: "firing",
      source: "alertmanager",
    },
    status: "diagnosed",
    diagnosis_result: {
      root_cause: rootCause,
      root_cause_layer: "service",
      root_cause_entities: [],
      confidence: 0.8,
      hypotheses: [],
      impact_summary: "impact",
      affected_services: [],
      triage_priority: "P2",
      diagnosis_certainty: "probable",
    },
    trace: { steps: [] },
    duration_seconds: 1,
    outcome: null,
  };
}

describe("Diagnosis page session picker", () => {
  it("auto-selects latest session when URL has no session_id", async () => {
    server.use(
      http.get("/api/sessions", async () =>
        HttpResponse.json({
          success: true,
          data: [
            {
              session_id: "sess-2",
              status: "diagnosed",
              alert_name: "GPUUtilizationHigh",
              severity: "warning",
              fingerprint: "fp-sess-2",
              outcome: null,
              duration_seconds: 10,
              updated_at: "2026-03-27T10:00:00Z",
            },
            {
              session_id: "sess-1",
              status: "diagnosed",
              alert_name: "GPUUtilizationHigh",
              severity: "warning",
              fingerprint: "fp-sess-1",
              outcome: null,
              duration_seconds: 8,
              updated_at: "2026-03-27T09:00:00Z",
            },
          ],
          error: null,
          trace_id: "trace-sessions",
          timestamp: "2026-03-27T10:00:01Z",
        }),
      ),
      http.get("/api/sessions/:sessionId", async ({ params }) =>
        HttpResponse.json({
          success: true,
          data: makeSession(String(params.sessionId), `rc-${params.sessionId}`),
          error: null,
          trace_id: "trace-session",
          timestamp: "2026-03-27T10:00:02Z",
        }),
      ),
    );

    render(
      <MemoryRouter initialEntries={["/diagnosis"]}>
        <Routes>
          <Route
            path="/diagnosis"
            element={
              <>
                <DiagnosisPage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("location-search").textContent).toContain("session_id=sess-2"),
    );
    await waitFor(() => expect(screen.getByText("rc-sess-2")).toBeInTheDocument());
  });

  it("switches session and syncs URL when user selects a different session", async () => {
    server.use(
      http.get("/api/sessions", async () =>
        HttpResponse.json({
          success: true,
          data: [
            {
              session_id: "sess-2",
              status: "diagnosed",
              alert_name: "GPUUtilizationHigh",
              severity: "warning",
              fingerprint: "fp-sess-2",
              outcome: null,
              duration_seconds: 10,
              updated_at: "2026-03-27T10:00:00Z",
            },
            {
              session_id: "sess-1",
              status: "diagnosed",
              alert_name: "GPUUtilizationHigh",
              severity: "warning",
              fingerprint: "fp-sess-1",
              outcome: null,
              duration_seconds: 8,
              updated_at: "2026-03-27T09:00:00Z",
            },
          ],
          error: null,
          trace_id: "trace-sessions",
          timestamp: "2026-03-27T10:00:01Z",
        }),
      ),
      http.get("/api/sessions/:sessionId", async ({ params }) =>
        HttpResponse.json({
          success: true,
          data: makeSession(String(params.sessionId), `rc-${params.sessionId}`),
          error: null,
          trace_id: "trace-session",
          timestamp: "2026-03-27T10:00:02Z",
        }),
      ),
    );

    render(
      <MemoryRouter initialEntries={["/diagnosis?session_id=sess-1"]}>
        <Routes>
          <Route
            path="/diagnosis"
            element={
              <>
                <DiagnosisPage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("rc-sess-1")).toBeInTheDocument());
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(screen.getByText("sess-2 | GPUUtilizationHigh | diagnosed"));

    await waitFor(() => expect(screen.getByText("rc-sess-2")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByTestId("location-search").textContent).toContain("session_id=sess-2"),
    );
  });
});
