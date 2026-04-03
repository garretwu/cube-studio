import type { ReactNode } from "react";

import { cn } from "./cn";

type StatusChipProps = {
  children: ReactNode;
  tone?: "neutral" | "accent" | "success" | "warning" | "danger" | "info";
  className?: string;
};

function StatusChip({ children, tone = "neutral", className }: StatusChipProps) {
  return <span className={cn("status-chip", `status-chip--${tone}`, className)}>{children}</span>;
}

export default StatusChip;
