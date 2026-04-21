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

  it("opens the selected record in a masked overlay panel and supports closing selection", async () => {
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

    const panel = await waitFor(() => {
      const node = container.querySelector<HTMLElement>(".remediation-sidepanel");
      expect(node).toBeTruthy();
      return node;
    });

    const scoped = within(panel!);
    expect(scoped.getByText("方案信息")).toBeInTheDocument();
    expect(scoped.getByRole("button", { name: /\u8fd4\u56de\u4fee\u590d\u5217\u8868/ })).toBeTruthy();
    expect(scoped.getByText("执行过程")).toBeInTheDocument();
    expect(scoped.queryByText("执行信息")).toBeNull();
    expect(scoped.queryByText("修复时间线")).toBeNull();
    expect(scoped.queryByText("最新状态")).toBeNull();
    expect(scoped.queryByText("当前步骤")).toBeNull();
    expect(scoped.queryByText("灰度进度")).toBeNull();

    const expandPlanStepsButton = scoped.getByRole("button", { name: /\u5c55\u5f00\u6b65\u9aa4\u8be6\u60c5/ });
    await user.click(expandPlanStepsButton);
    expect(scoped.getAllByText("\u6b65\u9aa4 1").length).toBeGreaterThan(0);
    expect(scoped.getAllByText(/工具：/).length).toBeGreaterThan(0);
    expect(scoped.getByText("修复事件")).toBeInTheDocument();

    await user.click(scoped.getByRole("button", { name: /\u8fd4\u56de\u4fee\u590d\u5217\u8868/ }));

    await waitFor(() => {
      expect(container.querySelector(".remediation-sidepanel")).toBeFalsy();
    });
  });

  it("shows a single progress column in the record list", async () => {
    const { container } = render(<RemediationPage />);

    const row = await waitFor(() => {
      const node = container.querySelector<HTMLElement>(".remediation-record-table__row");
      expect(node).toBeTruthy();
      return node;
    });

    const headers = Array.from(container.querySelectorAll("thead th")).map((node) => node.textContent?.trim());
    const progressLabels = Array.from(row!.querySelectorAll<HTMLElement>(".remediation-record-table__cell--progress span"))
      .map((node) => node.textContent?.trim())
      .filter(Boolean);

    expect(headers).toContain("进度");
    expect(headers).not.toContain("金丝雀");
    expect(headers).not.toContain("全量");
    expect(progressLabels).toEqual(["67%"]);
  });

  it("preselects the session from the query string on first load", async () => {
    window.history.pushState({}, "", "/remediation?sessionId=sess-latency-001");
    const { container } = render(<RemediationPage />);

    const panel = await waitFor(() => {
      const node = container.querySelector<HTMLElement>(".remediation-sidepanel");
      expect(node).toBeTruthy();
      return node;
    });

    const scoped = within(panel!);
    await waitFor(() => {
      expect(scoped.getByText("VLLM \u5ef6\u8fdf\u8fc7\u9ad8")).toBeInTheDocument();
    });
    expect(scoped.getByText("CRITICAL")).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });
});


