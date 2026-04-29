import { act, render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { vi } from "vitest";

import RemediationPage from "./Remediation";
import { remediationTimeline } from "../mocks/data";
import { useRemediationStore } from "../store/remediationStore";
import { server } from "../test/server";

describe("RemediationPage", () => {
  it("falls back to the alternate history endpoint when /api/sessions fails", async () => {
    server.use(http.get("/api/sessions", async () => HttpResponse.json({ message: "boom" }, { status: 500 })));

    const { container, queryByText } = render(<RemediationPage />);

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__row")).toBeTruthy();
    });

    expect(queryByText(/修复记录加载失败/)).toBeNull();
  });

  it("opens the selected record in a right-side drawer and closes it", async () => {
    const user = userEvent.setup();
    const { container } = render(<RemediationPage />);

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__row")).toBeTruthy();
    });

    expect(container.querySelector(".remediation-record-table__expand-row")).toBeFalsy();
    expect(container.querySelector(".remediation-sidepanel")).toBeFalsy();

    const firstRow = container.querySelector<HTMLElement>(".remediation-record-table__row");
    expect(firstRow).toBeTruthy();
    await user.click(firstRow!);

    const drawer = await waitFor(() => {
      const panel = container.querySelector<HTMLElement>(".remediation-sidepanel");
      expect(panel).toBeTruthy();
      return panel;
    });

    const scoped = within(drawer!);
    expect(scoped.getByText("方案信息")).toBeInTheDocument();
    expect(scoped.getByRole("button", { name: /返回修复列表/ })).toBeTruthy();
    expect(scoped.getByText("执行过程")).toBeInTheDocument();

    const expandPlanStepsButton = scoped.getByRole("button", { name: /展开步骤详情/ });
    await user.click(expandPlanStepsButton);

    expect(scoped.getAllByText("步骤 1").length).toBeGreaterThan(0);
    expect(scoped.getAllByText(/工具：/).length).toBeGreaterThan(0);

    await user.click(scoped.getByRole("button", { name: /返回修复列表/ }));

    await waitFor(() => {
      expect(container.querySelector(".remediation-sidepanel")).toBeFalsy();
    });
  });

  it("preselects the session from the query string on first load", async () => {
    window.history.pushState({}, "", "/remediation?sessionId=sess-latency-001");
    const { container } = render(<RemediationPage />);

    const drawer = await waitFor(() => {
      const panel = container.querySelector<HTMLElement>(".remediation-sidepanel");
      expect(panel).toBeTruthy();
      return panel;
    });

    expect(within(drawer!).getByText("VLLM 延迟过高")).toBeInTheDocument();
    expect(within(drawer!).getByText("CRITICAL")).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });

  it("updates timeline in-place when realtime remediation events arrive", async () => {
    const user = userEvent.setup();
    const { container } = render(<RemediationPage />);

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__row")).toBeTruthy();
    });

    const firstRow = container.querySelector<HTMLElement>(".remediation-record-table__row");
    expect(firstRow).toBeTruthy();
    await user.click(firstRow!);

    const drawer = await waitFor(() => {
      const panel = container.querySelector<HTMLElement>(".remediation-sidepanel");
      expect(panel).toBeTruthy();
      return panel;
    });

    const activeSessionId = useRemediationStore.getState().sessionId;
    expect(activeSessionId).toBeTruthy();

    await act(async () => {
      useRemediationStore.getState().applyRealtimeEvent({
        schema_version: "1.0",
        type: "remediation_progress",
        session_id: activeSessionId,
        timestamp: "2026-04-20T10:00:00Z",
        data: {
          event_id: "realtime-1",
          stage: "observation_result",
          message: "realtime observation updated",
        },
      });
    });

    expect(within(drawer!).getByText("realtime observation updated")).toBeInTheDocument();
  });

  it("renders the same weighted overall progress in list and detail drawer", async () => {
    const user = userEvent.setup();
    const { container } = render(<RemediationPage />);

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__row")).toBeTruthy();
    });

    const firstRow = container.querySelector<HTMLElement>(".remediation-record-table__row");
    expect(firstRow).toBeTruthy();
    expect(within(firstRow!).getByText("100%")).toBeInTheDocument();

    await user.click(firstRow!);

    const drawer = await waitFor(() => {
      const panel = container.querySelector<HTMLElement>(".remediation-sidepanel");
      expect(panel).toBeTruthy();
      return panel;
    });

    expect(within(drawer!).getByText("总体 100%")).toBeInTheDocument();
  });

  it("falls back to 10s polling when realtime channel is unavailable", async () => {
    const setIntervalSpy = vi.spyOn(window, "setInterval");
    let releasePollingEvent = false;
    const activeRemediationTimeline = remediationTimeline.filter(
      (event) => event.type !== "remediation_progress" || event.data?.stage !== "execution_succeeded",
    );
    server.use(
      http.get("/api/sessions/:sessionId/events", ({ params }) => {
        const sessionId = String(params.sessionId ?? "");
        if (sessionId !== "sess-latency-001") {
          return HttpResponse.json([]);
        }
        if (releasePollingEvent) {
          return HttpResponse.json([
            ...activeRemediationTimeline,
            {
              schema_version: "1.0",
              type: "remediation_progress",
              session_id: sessionId,
              timestamp: "2026-04-20T10:05:00Z",
              data: {
                event_id: "polling-1",
                stage: "observation_result",
                message: "polling observation updated",
              },
            },
          ]);
        }
        return HttpResponse.json(activeRemediationTimeline);
      }),
    );

    try {
      const user = userEvent.setup();
      const { container } = render(<RemediationPage />);

      await waitFor(() => {
        expect(container.querySelector(".remediation-record-table__row")).toBeTruthy();
      });

      const firstRow = container.querySelector<HTMLElement>(".remediation-record-table__row");
      expect(firstRow).toBeTruthy();
      await user.click(firstRow!);

      const drawer = await waitFor(() => {
        const panel = container.querySelector<HTMLElement>(".remediation-sidepanel");
        expect(panel).toBeTruthy();
        return panel;
      });

      expect(within(drawer!).queryByText("polling observation updated")).toBeNull();
      const fallbackCall = setIntervalSpy.mock.calls.find((call) => Number(call[1]) === 10000);
      expect(fallbackCall).toBeTruthy();

      const pollTick = fallbackCall?.[0];
      expect(typeof pollTick).toBe("function");
      await act(async () => {
        releasePollingEvent = true;
        if (typeof pollTick === "function") {
          pollTick();
        }
      });

      await waitFor(() => {
        expect(within(drawer!).getByText("polling observation updated")).toBeInTheDocument();
      });
    } finally {
      setIntervalSpy.mockRestore();
    }
  });
});
