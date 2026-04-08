import { render, screen } from "@testing-library/react";

import HistoryPage from "./History";

describe("HistoryPage", () => {
  it("renders a visible history channel placeholder", () => {
    const { container } = render(<HistoryPage />);

    expect(container.firstChild).toHaveClass("history-page");
    expect(screen.getByRole("heading", { name: "历史频道" })).toBeInTheDocument();
    expect(screen.getByText(/历史会话入口/)).toBeInTheDocument();
    expect(screen.getByText(/当前页面是历史频道的占位视图/)).toBeInTheDocument();
  });
});
