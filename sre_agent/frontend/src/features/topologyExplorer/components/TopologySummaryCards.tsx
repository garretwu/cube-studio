import type { TopologySummaryMetrics } from "../selectors";
import type { ExplorerSummaryFilter } from "../types";

type TopologySummaryCardsProps = {
  metrics: TopologySummaryMetrics;
  activeFilter: ExplorerSummaryFilter;
  onSelect: (filter: ExplorerSummaryFilter) => void;
};

function TopologySummaryCards({ metrics, activeFilter, onSelect }: TopologySummaryCardsProps) {
  const cards = [
    {
      key: "all" as const,
      label: "总实体数",
      value: metrics.totalEntities,
      hint: "全部对象与关系",
    },
    {
      key: "abnormal" as const,
      label: "异常实体数",
      value: metrics.abnormalEntities,
      hint: "聚焦异常对象",
    },
    {
      key: "impacted" as const,
      label: "受影响实体数",
      value: metrics.impactedEntities,
      hint: "查看受影响对象",
    },
    {
      key: "paths" as const,
      label: "活跃影响路径数",
      value: metrics.activePaths,
      hint: "聚焦 blast radius",
    },
  ];

  return (
    <div className="topology-modified-summary-grid">
      {cards.map((card) => (
        <button
          key={card.key}
          className={`topology-modified-summary-card ${activeFilter === card.key ? "topology-modified-summary-card--active" : ""}`}
          onClick={() => onSelect(card.key)}
          type="button"
        >
          <p className="topology-modified-summary-card__label">{card.label}</p>
          <p className="topology-modified-summary-card__value">{card.value}</p>
          <p className="topology-modified-summary-card__hint">{card.hint}</p>
        </button>
      ))}
    </div>
  );
}

export default TopologySummaryCards;
