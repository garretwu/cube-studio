import { render, screen } from "@testing-library/react";

import { AppIcon, type AppIconName } from "./AppIcon";

describe("AppIcon", () => {
  it("keeps legacy icon names working through aliases", () => {
    render(<AppIcon data-testid="icon" name="topology" />);

    expect(screen.getByTestId("icon")).toHaveAttribute("data-icon-name", "layers");
  });

  it("switches between outline and fill variants", () => {
    const { rerender } = render(<AppIcon data-testid="icon" name="search" variant="outline" />);
    const outlineMarkup = screen.getByTestId("icon").innerHTML;

    rerender(<AppIcon data-testid="icon" name="search" variant="fill" />);

    expect(screen.getByTestId("icon")).toHaveAttribute("data-icon-variant", "fill");
    expect(screen.getByTestId("icon").innerHTML).not.toEqual(outlineMarkup);
  });

  it("renders an explicit fallback for missing icon names", () => {
    render(<AppIcon data-testid="icon" name={"doesNotExist" as AppIconName} />);

    expect(screen.getByTestId("icon")).toHaveAttribute("data-missing-icon", "doesNotExist");
  });
});
