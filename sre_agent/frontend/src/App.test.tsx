import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import App from "./App";

describe("App shell", () => {
  it("renders the redesigned shell and topology landing page", () => {
    render(
      <MemoryRouter initialEntries={["/topology"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByText(/AIDC 拓扑总览/i)).toBeInTheDocument();
    expect(screen.getByText(/拓扑浏览器/i)).toBeInTheDocument();
    expect(screen.getByText(/帮助中心/i)).toBeInTheDocument();
  });
});
