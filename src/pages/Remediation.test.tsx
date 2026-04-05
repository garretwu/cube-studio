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

    expect(queryByText(/淇璁板綍鍔犺浇澶辫触/)).toBeNull();
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
    expect(scoped.getByRole("button", { name: /返回修复列表/ })).toBeTruthy();

    const stepButton = scoped.getByRole("button", { name: /查看步骤 1 详情/ });
    await user.click(stepButton);

    expect(scoped.getByText("步骤 1 详情")).toBeTruthy();
    expect(scoped.getByText("执行参数")).toBeTruthy();

    await user.click(scoped.getByRole("button", { name: /返回修复列表/ }));

    await waitFor(() => {
      expect(container.querySelector(".remediation-drawer__panel")).toBeFalsy();
    });
  });
});


