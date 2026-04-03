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

    expect(queryByText(/修复记录加载失败/)).toBeNull();
  });
  it("keeps detailed records collapsed by default and toggles them on click", async () => {
    const user = userEvent.setup();
    const { container } = render(<RemediationPage />);

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__row")).toBeTruthy();
    });

    expect(container.querySelector(".remediation-record-table__expand-row")).toBeFalsy();

    const firstRow = container.querySelector<HTMLElement>(".remediation-record-table__row");
    expect(firstRow).toBeTruthy();
    await user.click(firstRow!);

    const expandRow = await waitFor(() => {
      const row = container.querySelector<HTMLElement>(".remediation-record-table__expand-row");
      expect(row).toBeTruthy();
      return row;
    });

    const scoped = within(expandRow as HTMLElement);
    expect(scoped.queryByRole("heading", { name: /详细记录/ })).toBeNull();
    expect(scoped.getByText("基础信息")).toBeInTheDocument();
    expect(scoped.getByText("方案信息")).toBeInTheDocument();
    expect(scoped.getByText("执行信息")).toBeInTheDocument();
    expect(scoped.getByRole("button", { name: "收起详细记录" })).toBeInTheDocument();
    expect(scoped.getByText("修复时间线")).toBeInTheDocument();

    await user.click(scoped.getByRole("button", { name: "收起详细记录" }));

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__expand-row")).toBeFalsy();
    });
  });
});
