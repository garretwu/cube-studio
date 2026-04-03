import type { ButtonHTMLAttributes, ReactElement } from "react";

import { AppIcon, type AppIconName } from "./AppIcon";
import { cn } from "./cn";

type AppButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "tertiary" | "danger";
  size?: "sm" | "md" | "lg";
  iconLeft?: AppIconName | ReactElement;
  iconRight?: AppIconName | ReactElement;
  loading?: boolean;
  block?: boolean;
};

function renderIcon(icon: AppIconName | ReactElement | undefined) {
  if (!icon) {
    return null;
  }

  if (typeof icon === "string") {
    return <AppIcon name={icon} size={16} />;
  }

  return icon;
}

function AppButton({
  children,
  className,
  variant = "secondary",
  size = "md",
  iconLeft,
  iconRight,
  loading = false,
  block = false,
  type = "button",
  ...props
}: AppButtonProps) {
  return (
    <button
      className={cn(
        "app-button",
        `app-button--${variant}`,
        `app-button--${size}`,
        block && "app-button--block",
        className,
      )}
      type={type}
      {...props}
    >
      {loading ? <span className="app-button__spinner" /> : renderIcon(iconLeft)}
      {children ? <span>{children}</span> : null}
      {!loading ? renderIcon(iconRight) : null}
    </button>
  );
}

export default AppButton;
