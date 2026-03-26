import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import App from "./App";

describe("App shell", () => {
  it("renders the command center heading", () => {
    render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getAllByText(/AIDC Auto-SRE 指挥台/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/AIDC Topology Command Surface/i)).toBeInTheDocument();
  });
});
