import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import RemediationPage from "./Remediation";
import { server } from "../test/server";

describe("RemediationPage", () => {
  it("falls back to the alternate history endpoint when /api/sessions fails", async () => {
    server.use(http.get("/api/sessions", async () => HttpResponse.json({ message: "boom" }, { status: 500 })));

    const { container, queryByText } = render(<RemediationPage />);

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__row")).toBeTruthy();
    });

    expect(queryByText(/锟睫革拷锟斤拷录锟斤拷锟斤拷失锟斤拷/)).toBeNull();
  });

  it("opens the selected record in a right-side drawer and closes it", async () => {
    const user = userEvent.setup();
    const { container } = render(<RemediationPage />);

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__row")).toBeTruthy();
    });

    expect(container.querySelector(".remediation-record-table__expand-row")).toBeFalsy();
    expect(container.querySelector(".remediation-drawer__panel")).toBeFalsy();

    const firstRow = container.querySelector<HTMLElement>(".remediation-record-table__row");
    expect(firstRow).toBeTruthy();
    await user.click(firstRow!);

    const drawer = await waitFor(() => {
      const panel = container.querySelector<HTMLElement>(".remediation-drawer__panel");
      expect(panel).toBeTruthy();
      return panel;
    });

    const scoped = within(drawer!);
    expect(drawer!.querySelector(".remediation-plan-overview__facts")).toBeTruthy();
    expect(scoped.getByRole("button", { name: /\u8fd4\u56de\u4fee\u590d\u5217\u8868/ })).toBeTruthy();

    const stepButton = scoped.getByRole("button", { name: /\u67e5\u770b\u6b65\u9aa4 1 \u8be6\u60c5/ });
    await user.click(stepButton);

    expect(scoped.getByText("\u6b65\u9aa4 1 \u8be6\u60c5")).toBeTruthy();
    expect(scoped.getByText("\u6267\u884c\u53c2\u6570")).toBeTruthy();

    await user.click(scoped.getByRole("button", { name: /\u8fd4\u56de\u4fee\u590d\u5217\u8868/ }));

    await waitFor(() => {
      expect(container.querySelector(".remediation-drawer__panel")).toBeFalsy();
    });
  });

  it("preselects the session from the query string on first load", async () => {
    window.history.pushState({}, "", "/remediation?sessionId=sess-latency-001");
    const { container } = render(<RemediationPage />);

    const drawer = await waitFor(() => {
      const panel = container.querySelector<HTMLElement>(".remediation-drawer__panel");
      expect(panel).toBeTruthy();
      return panel;
    });

    expect(within(drawer!).getByText("VLLM \u5ef6\u8fdf\u8fc7\u9ad8 \u00b7 CRITICAL")).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });
});


