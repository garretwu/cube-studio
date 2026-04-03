import { render, waitFor, within } from "@testing-library/react";

import RemediationPage from "./Remediation";

describe("RemediationPage", () => {
  it("renders the expanded record as a table subform instead of summary cards", async () => {
    const { container } = render(<RemediationPage />);

    await waitFor(() => {
      expect(container.querySelector(".remediation-record-table__expand-row")).toBeTruthy();
    });

    const expandRow = container.querySelector(".remediation-record-table__expand-row");
    expect(expandRow).toBeTruthy();

    const scoped = within(expandRow as HTMLElement);
    expect(scoped.getByText("\u8be6\u7ec6\u8bb0\u5f55")).toBeInTheDocument();
    expect(scoped.getByText("\u6839\u56e0\u5b9a\u4f4d")).toBeInTheDocument();
    expect(scoped.getByText("\u4fee\u590d\u5185\u5bb9")).toBeInTheDocument();
    expect(scoped.getAllByText("\u91d1\u4e1d\u96c0\u7b56\u7565").length).toBeGreaterThan(0);
    expect(scoped.queryByText("\u5ba1\u6279\u4e0e\u6267\u884c")).not.toBeInTheDocument();
    expect(scoped.queryByText("\u7248\u672c\u4e0e\u4f18\u5148\u7ea7")).not.toBeInTheDocument();
  });
});