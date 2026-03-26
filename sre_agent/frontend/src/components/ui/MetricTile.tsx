import type { ReactNode } from "react";

type MetricTileProps = {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
};

function MetricTile({ label, value, hint }: MetricTileProps) {
  return (
    <div className="metric-tile">
      <p className="metric-tile__label">{label}</p>
      <p className="metric-tile__value">{value}</p>
      {hint ? <p className="metric-tile__hint">{hint}</p> : null}
    </div>
  );
}

export default MetricTile;
