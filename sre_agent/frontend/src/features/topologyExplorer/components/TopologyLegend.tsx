import { AppIcon, StatusChip } from "../../../components/ui";
import {
  formatRelationType,
  formatTopologyStatus,
  formatTopologyType,
  getStatusTone,
  getTopologyTypeIconName,
} from "../formatters";

type TopologyLegendProps = {
  open: boolean;
};

const typeItems = ["rack", "node", "gpu", "switch", "port", "bmc", "service", "pod", "cluster"] as const;
const statusItems = ["healthy", "abnormal", "impacted", "maintenance"] as const;
const relationItems = ["contains", "runs_on", "connects_to", "depends_on", "uplink_to", "aggregated"] as const;

function TopologyLegend({ open }: TopologyLegendProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="topology-modified-legend topology-modified-legend--floating" data-testid="topology-legend">
      <div className="topology-modified-legend__section">
        <p className="topology-modified-legend__title">{"\u8282\u70b9\u7c7b\u578b"}</p>
        <div className="topology-modified-legend__items">
          {typeItems.map((item) => (
            <span key={item} className={`topology-modified-legend__pill topology-modified-legend__pill--${item}`}>
              <span className={`topology-modified-legend__icon topology-modified-legend__icon--${item}`} aria-hidden="true">
                <AppIcon name={getTopologyTypeIconName(item)} size={12} />
              </span>
              {formatTopologyType(item)}
            </span>
          ))}
        </div>
      </div>

      <div className="topology-modified-legend__section">
        <p className="topology-modified-legend__title">{"\u8282\u70b9\u72b6\u6001"}</p>
        <div className="topology-modified-legend__items">
          {statusItems.map((item) => (
            <StatusChip key={item} tone={getStatusTone(item)}>
              {formatTopologyStatus(item)}
            </StatusChip>
          ))}
        </div>
      </div>

      <div className="topology-modified-legend__section">
        <p className="topology-modified-legend__title">{"\u8fb9\u5173\u7cfb\u7c7b\u578b"}</p>
        <div className="topology-modified-legend__items">
          {relationItems.map((item) => (
            <span key={item} className="topology-modified-legend__relation">
              <span className="topology-modified-legend__line" />
              {formatRelationType(item)}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default TopologyLegend;