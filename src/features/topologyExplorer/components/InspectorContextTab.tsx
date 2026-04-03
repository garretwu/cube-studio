import type { TopologyObject, TopologyPath } from "../../../api/types";
import { formatImpactLevel, formatTopologyStatus, formatTopologyType } from "../formatters";

type InspectorContextTabProps = {
  node: TopologyObject;
  paths: TopologyPath[];
  affectedObjects: TopologyObject[];
  neighbors: TopologyObject[];
  onSelectNode: (nodeId: string) => void;
};

function getSuggestion(node: TopologyObject, pathCount: number) {
  if (node.status === "abnormal") {
    return "优先检查上游依赖与受影响服务，确认是否需要隔离节点或切走流量。";
  }

  if (node.status === "impacted") {
    return "当前对象更像结果节点，建议沿关键路径回溯到根因对象定位问题源头。";
  }

  if (pathCount > 0) {
    return "当前对象仍处在活跃影响路径中，建议结合关系页快速检查邻居对象状态。";
  }

  return "当前对象未处于活跃异常路径，可作为稳定基线对象做横向对比。";
}

function ContextList({
  title,
  emptyText,
  items,
  onSelectNode,
}: {
  title: string;
  emptyText: string;
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
        <p className="topology-modified-empty-copy">{emptyText}</p>
      )}
    </div>
  );
}

function InspectorContextTab({ node, paths, affectedObjects, neighbors, onSelectNode }: InspectorContextTabProps) {
  return (
    <div className="topology-modified-inspector-tab">
      <div className="topology-modified-context-grid">
        <div className="topology-modified-context-card">
          <p className="topology-modified-detail-grid__label">受影响对象数</p>
          <p className="topology-modified-context-card__value">{affectedObjects.length}</p>
        </div>
        <div className="topology-modified-context-card">
          <p className="topology-modified-detail-grid__label">关联路径数</p>
          <p className="topology-modified-context-card__value">{paths.length}</p>
        </div>
      </div>

      <div className="topology-modified-inspector-note">
        <p className="topology-modified-detail-grid__label">当前定位建议</p>
        <p className="topology-modified-detail-grid__value">{getSuggestion(node, paths.length)}</p>
      </div>

      <div className="topology-modified-inspector-note">
        <p className="topology-modified-detail-grid__label">关联路径</p>
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
          <p className="topology-modified-empty-copy">当前对象没有关联路径</p>
        )}
      </div>

      <ContextList emptyText="暂无受影响对象" items={affectedObjects} onSelectNode={onSelectNode} title="受影响对象" />
      <ContextList emptyText="暂无邻居对象" items={neighbors} onSelectNode={onSelectNode} title="邻居对象" />
    </div>
  );
}

export default InspectorContextTab;
