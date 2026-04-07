import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import KnowledgePage from "./Knowledge";

describe("KnowledgePage", () => {
  it("shows datasets and reloads documents when switching dataset", async () => {
    const user = userEvent.setup();
    render(<KnowledgePage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Runbook Dataset/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Network Dataset/i })).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText("knowledge/runbooks/gpu-contention.md")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /Network Dataset/i }));

    await waitFor(() => {
      expect(screen.getByText("docs/hardware/rocev2-ecn.md")).toBeInTheDocument();
      expect(screen.queryByText("knowledge/runbooks/gpu-contention.md")).not.toBeInTheDocument();
    });
  });
});
