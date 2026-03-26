import { appTheme } from "./antdTheme";
import { createThemeVariables, semanticTokens, serializeThemeVars } from "./tokens";

describe("theme tokens", () => {
  it("serializes layered theme variables and keeps legacy aliases", () => {
    const variables = createThemeVariables();
    const cssText = serializeThemeVars(variables);

    expect(variables["--foundation-color-brand-600"]).toBe("#503dff");
    expect(variables["--semantic-text-primary"]).toBe("#0d0d12");
    expect(variables["--component-app-button-radius"]).toBe("8px");
    expect(variables["--icon-color-active"]).toBe("#3f30d0");
    expect(variables["--color-brand-600"]).toBe("#503dff");
    expect(cssText).toContain(":root");
    expect(cssText).toContain("--semantic-text-primary: #0d0d12;");
    expect(cssText).toContain("--component-app-button-radius: 8px;");
  });

  it("keeps the antd bridge sourced from shared theme tokens", () => {
    expect(appTheme.token?.colorPrimary).toBe(semanticTokens.action.primary);
    expect(appTheme.token?.colorBorder).toBe(semanticTokens.border.default);
    expect(appTheme.components?.Modal?.contentBg).toBe(semanticTokens.surface.panel);
    expect(appTheme.components?.Tabs?.colorText).toBe(semanticTokens.text.secondary);
    expect(appTheme.components?.Select?.optionSelectedBg).toBe(semanticTokens.surface.panelBrand);
  });
});
