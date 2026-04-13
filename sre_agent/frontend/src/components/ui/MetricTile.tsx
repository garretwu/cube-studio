import type { ReactNode } from "react";

import { AppIcon, type AppIconName } from "./AppIcon";

export type MetricTileTone = "neutral" | "accent" | "info" | "warning" | "success" | "danger";

type MetricTileProps = {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  icon?: AppIconName;
  tone?: MetricTileTone;
};

function MetricTile({ label, value, hint, icon, tone = "neutral" }: MetricTileProps) {
  return (
    <div className={`metric-tile metric-tile--${tone}${icon ? " metric-tile--with-icon" : ""}`}>
      <div className="metric-tile__header">
        <p className="metric-tile__label">{label}</p>
        {icon ? (
          <span aria-hidden="true" className="metric-tile__icon">
            <AppIcon name={icon} size={16} />
          </span>
        ) : null}
      </div>
      <p className="metric-tile__value">{value}</p>
      {hint ? <p className="metric-tile__hint">{hint}</p> : null}
    </div>
  );
}

export default MetricTile;
