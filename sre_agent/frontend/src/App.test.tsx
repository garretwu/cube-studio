import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import App from "./App";

describe("App shell", () => {
  it("renders the command center heading", () => {
    render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByText(/AIDC Auto-SRE 指挥台/i)).toBeInTheDocument();
    expect(screen.getByText(/AIDC Topology Command Surface/i)).toBeInTheDocument();
  });
});
