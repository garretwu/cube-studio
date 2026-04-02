import type { TopologyObject, TopologyPath } from "../../../api/types";
import { AppButton, StatusChip } from "../../../components/ui";
import { formatImpactLevel, formatTopologyStatus, formatTopologyType, getStatusTone } from "../formatters";

type InspectorRelationsTabProps = {
  upstream: TopologyObject[];
  downstream: TopologyObject[];
  neighbors: TopologyObject[];
  paths: TopologyPath[];
  onHighlightInGraph: () => void;
  onSelectNode: (nodeId: string) => void;
};

function RelationList({
  title,
  items,
  onSelectNode,
}: {
  title: string;
  items: TopologyObject[];
  onSelectNode: (nodeId: string) => void;
}) {
  return (
    <div className="topology-modified-inspector-note">
      <div className="topology-modified-inspector-row">
        <p className="topology-modified-detail-grid__label">{title}</p>
        <span className="topology-modified-detail-grid__meta">{items.length}</span>
      </div>
      {items.length > 0 ? (
        <div className="topology-modified-link-list">
          {items.map((item) => (
            <button key={item.id} className="topology-modified-link-list__item" onClick={() => onSelectNode(item.id)} type="button">
              <span>{item.name}</span>
              <span className="topology-modified-link-list__meta">
                {formatTopologyType(item.type)} · {formatTopologyStatus(item.status)}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <p className="topology-modified-empty-copy">暂无对象</p>
      )}
    </div>
  );
}

function InspectorRelationsTab({
  upstream,
  downstream,
  neighbors,
  paths,
  onHighlightInGraph,
  onSelectNode,
}: InspectorRelationsTabProps) {
  return (
    <div className="topology-modified-inspector-tab">
      <div className="topology-modified-inspector-row">
        <StatusChip tone="accent">依赖关系 {upstream.length + downstream.length}</StatusChip>
        <AppButton size="sm" variant="secondary" onClick={onHighlightInGraph}>
          在图中高亮
        </AppButton>
      </div>
      <RelationList items={upstream} onSelectNode={onSelectNode} title="上游对象" />
      <RelationList items={downstream} onSelectNode={onSelectNode} title="下游对象" />
      <RelationList items={neighbors} onSelectNode={onSelectNode} title="邻居对象" />
      <div className="topology-modified-inspector-note">
        <p className="topology-modified-detail-grid__label">当前对象所在关键路径</p>
        {paths.length > 0 ? (
          <div className="topology-modified-link-list">
            {paths.map((path) => (
              <div key={path.id} className="topology-modified-link-list__item topology-modified-link-list__item--static">
                <span>{path.summary}</span>
                <span className="topology-modified-link-list__meta">影响等级 {formatImpactLevel(path.impactLevel)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="topology-modified-empty-copy">当前对象不在关键影响路径中</p>
        )}
      </div>
    </div>
  );
}

export default InspectorRelationsTab;
