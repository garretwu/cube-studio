import { render } from "@testing-library/react";

import HistoryPage from "./History";

describe("HistoryPage", () => {
  it("renders an intentionally blank history channel body", () => {
    const { container } = render(<HistoryPage />);

    expect(container.firstChild).toHaveClass("history-page", "history-page--blank");
    expect(container.firstChild).toBeEmptyDOMElement();
  });
});
