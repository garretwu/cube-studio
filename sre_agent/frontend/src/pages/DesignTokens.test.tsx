import { render, screen, waitFor } from "@testing-library/react";

import { ensureThemeVariables } from "../theme/tokens";
import DesignTokensPage from "./DesignTokens";

const TOKEN_NAMES = [
  "--foundation-color-brand-600",
  "--semantic-text-primary",
  "--component-app-button-radius",
  "--icon-size-sm",
];

describe("DesignTokensPage", () => {
  beforeEach(() => {
    ensureThemeVariables();
  });

  afterEach(() => {
    for (const tokenName of TOKEN_NAMES) {
      document.documentElement.style.removeProperty(tokenName);
    }
  });

  it("renders layered token groups and icon catalog from runtime variables", async () => {
    document.documentElement.style.setProperty("--foundation-color-brand-600", "#503dff");
    document.documentElement.style.setProperty("--semantic-text-primary", "#0d0d12");
    document.documentElement.style.setProperty("--component-app-button-radius", "8px");
    document.documentElement.style.setProperty("--icon-size-sm", "16px");

    render(<DesignTokensPage />);

    expect(await screen.findByText("Global Token Explorer")).toBeInTheDocument();
    expect(screen.getAllByText("Foundation").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Semantic").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Component").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Icon").length).toBeGreaterThan(0);
    expect(screen.getByText("Icon Catalog")).toBeInTheDocument();
    expect(screen.getAllByText("--semantic-text-primary").length).toBeGreaterThan(0);
  });

  it("refreshes token values when runtime CSS variables change", async () => {
    document.documentElement.style.setProperty("--semantic-text-primary", "#0d0d12");

    render(<DesignTokensPage />);

    expect(await screen.findAllByText("#0d0d12")).not.toHaveLength(0);

    document.documentElement.style.setProperty("--semantic-text-primary", "#111827");

    await waitFor(
      () => {
        expect(screen.getAllByText("#111827").length).toBeGreaterThan(0);
      },
      { timeout: 3000 },
    );
  });
});
