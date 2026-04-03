import type { SVGProps } from "react";

import { iconTokens } from "../../theme/tokens";
import { cn } from "./cn";
import {
  appIconCatalog,
  renderAppIcon,
  resolveAppIconName,
  type AppIconName,
  type AppIconVariant,
} from "./iconRegistry";

type AppIconProps = Omit<SVGProps<SVGSVGElement>, "name" | "children"> & {
  name: AppIconName;
  size?: number;
  variant?: AppIconVariant;
  decorative?: boolean;
};

function resolveStrokeWidth(strokeWidth: AppIconProps["strokeWidth"]) {
  if (typeof strokeWidth === "number") {
    return strokeWidth;
  }

  if (typeof strokeWidth === "string") {
    const parsed = Number.parseFloat(strokeWidth);
    return Number.isNaN(parsed) ? Number.parseFloat(iconTokens.strokeWidth.regular) : parsed;
  }

  return Number.parseFloat(iconTokens.strokeWidth.regular);
}

function renderMissingIcon(strokeWidth: number) {
  return (
    <g
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
    >
      <rect x="4.5" y="4.5" width="15" height="15" rx="4" />
      <path d="m9 9 6 6" />
      <path d="m15 9-6 6" />
    </g>
  );
}

export function AppIcon({
  name,
  className,
  size,
  variant = "outline",
  decorative = true,
  strokeWidth,
  ...props
}: AppIconProps) {
  const resolvedName = resolveAppIconName(name);
  const resolvedSize = size ?? iconTokens.size.md;
  const resolvedStrokeWidth = resolveStrokeWidth(strokeWidth);
  const iconNode = resolvedName ? renderAppIcon(resolvedName, variant, { strokeWidth: resolvedStrokeWidth }) : null;

  return (
    <svg
      aria-hidden={decorative}
      aria-label={!decorative ? props["aria-label"] ?? String(name) : undefined}
      className={cn("app-icon", className)}
      data-icon-name={resolvedName ?? name}
      data-icon-variant={variant}
      data-missing-icon={resolvedName ? undefined : String(name)}
      focusable="false"
      height={resolvedSize}
      role={!decorative ? "img" : undefined}
      viewBox="0 0 24 24"
      width={resolvedSize}
      {...props}
    >
      {iconNode ?? renderMissingIcon(resolvedStrokeWidth)}
    </svg>
  );
}

export { appIconCatalog, type AppIconName, type AppIconVariant };
