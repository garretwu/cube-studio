import { AppIcon } from "../../../components/ui";
import { getTopologyTypeIconName } from "../formatters";
import type { TopologyTreeNode } from "../selectors";

type TopologyTreeViewProps = {
  tree: TopologyTreeNode | null;
  selectedNodeId?: string;
  onSelectNode: (nodeId: string) => void;
};

function TreeBranch({
  node,
  selectedNodeId,
  onSelectNode,
}: {
  node: TopologyTreeNode;
  selectedNodeId?: string;
  onSelectNode: (nodeId: string) => void;
}) {
  return (
    <li className="topology-modified-tree__item">
      {node.type === "group" ? (
        <div className="topology-modified-tree__group">{node.label}</div>
      ) : (
        <button
          className={`topology-modified-tree__node ${selectedNodeId === node.objectId ? "topology-modified-tree__node--active" : ""}`}
          onClick={() => node.objectId && onSelectNode(node.objectId)}
          type="button"
        >
          {node.objectType ? (
            <span className={`topology-modified-tree__node-icon topology-modified-tree__node-icon--${node.objectType}`} aria-hidden="true">
              <AppIcon name={getTopologyTypeIconName(node.objectType)} size={14} />
            </span>
          ) : null}
          <span className="topology-modified-tree__node-label">{node.label}</span>
        </button>
      )}
      {node.children?.length ? (
        <ul className="topology-modified-tree__list">
          {node.children.map((child) => (
            <TreeBranch key={child.id} node={child} onSelectNode={onSelectNode} selectedNodeId={selectedNodeId} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function TopologyTreeView({ tree, selectedNodeId, onSelectNode }: TopologyTreeViewProps) {
  if (!tree) {
    return <div className="topology-modified-empty">暂无树视图数据</div>;
  }

  return (
    <div className="topology-modified-tree">
      <ul className="topology-modified-tree__list">
        <TreeBranch node={tree} onSelectNode={onSelectNode} selectedNodeId={selectedNodeId} />
      </ul>
    </div>
  );
}

export default TopologyTreeView;
