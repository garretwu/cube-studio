import { Tabs } from "antd";

import type { TopologyObject, TopologyPath } from "../../../api/types";
import { AppButton, AppIcon, StatusChip } from "../../../components/ui";
import { formatTimestamp } from "../../../utils/format";
import {
  formatTopologyStatus,
  formatTopologyType,
  getLocationLabel,
  getStatusTone,
  getTopologyTypeIconName,
} from "../formatters";
import type { InspectorTabKey } from "../types";
import InspectorAttributesTab from "./InspectorAttributesTab";
import InspectorContextTab from "./InspectorContextTab";
import InspectorOverviewTab from "./InspectorOverviewTab";
import InspectorRelationsTab from "./InspectorRelationsTab";
import InspectorStatusTab from "./InspectorStatusTab";

type ObjectInspectorProps = {
  node?: TopologyObject;
  open: boolean;
  inspectorTab: InspectorTabKey;
  onOpenChange: (open: boolean) => void;
  onTabChange: (key: InspectorTabKey) => void;
  upstream: TopologyObject[];
  downstream: TopologyObject[];
  neighbors: TopologyObject[];
  paths: TopologyPath[];
  affectedObjects: TopologyObject[];
  onHighlightInGraph: () => void;
  onSelectNode: (nodeId: string) => void;
};

function ObjectInspector({
  node,
  open,
  inspectorTab,
  onOpenChange,
  onTabChange,
  upstream,
  downstream,
  neighbors,
  paths,
  affectedObjects,
  onHighlightInGraph,
  onSelectNode,
}: ObjectInspectorProps) {
  if (!open) {
    return null;
  }

  return (
    <aside className="topology-modified-stage-panel" data-testid="topology-side-inspector">
      <div className="topology-modified-stage-panel__header">
        <div className="topology-modified-stage-panel__header-main">
          {node ? (
            <span className={`topology-modified-stage-panel__icon topology-modified-stage-panel__icon--${node.type}`} aria-hidden="true">
              <AppIcon name={getTopologyTypeIconName(node.type)} size={18} />
            </span>
          ) : null}
          <div>
            <p className="topology-modified-stage-panel__eyebrow">对象检查器</p>
            <h3 className="topology-modified-stage-panel__title">{node ? node.name : "等待选择对象"}</h3>
            <p className="topology-modified-stage-panel__meta">
              {node ? `${formatTopologyType(node.type)} · 最近更新 ${formatTimestamp(node.updatedAt)}` : "在右侧面板中查看对象的关系、状态与属性。"}
            </p>
          </div>
        </div>
        <div className="topology-modified-stage-panel__header-actions">
          {node ? <StatusChip tone={getStatusTone(node.status)}>{formatTopologyStatus(node.status)}</StatusChip> : null}
          <AppButton size="sm" variant="secondary" onClick={() => onOpenChange(false)}>
            隐藏
          </AppButton>
        </div>
      </div>

      {!node ? (
        <div className="topology-modified-stage-panel__empty">
          <p className="topology-modified-empty-copy">请选择图中的对象，查看其关系、状态与属性。</p>
        </div>
      ) : (
        <>
          <div className="topology-modified-stage-panel__meta-grid">
            <span>{node.domain}</span>
            <span>{node.region}</span>
            <span>{node.cluster ?? "未归属集群"}</span>
            <span>{getLocationLabel(node)}</span>
          </div>

          <Tabs
            activeKey={inspectorTab}
            className="app-tabs topology-modified-stage-panel__tabs"
            onChange={(key) => onTabChange(key as InspectorTabKey)}
            items={[
              {
                key: "overview",
                label: "概览",
                children: <InspectorOverviewTab node={node} />,
              },
              {
                key: "relations",
                label: "关系",
                children: (
                  <InspectorRelationsTab
                    downstream={downstream}
                    neighbors={neighbors}
                    onHighlightInGraph={onHighlightInGraph}
                    onSelectNode={onSelectNode}
                    paths={paths}
                    upstream={upstream}
                  />
                ),
              },
              {
                key: "status",
                label: "状态",
                children: <InspectorStatusTab node={node} />,
              },
              {
                key: "attributes",
                label: "属性",
                children: <InspectorAttributesTab node={node} />,
              },
              {
                key: "context",
                label: "关联上下文",
                children: (
                  <InspectorContextTab
                    affectedObjects={affectedObjects}
                    neighbors={neighbors}
                    node={node}
                    onSelectNode={onSelectNode}
                    paths={paths}
                  />
                ),
              },
            ]}
          />
        </>
      )}
    </aside>
  );
}

export default ObjectInspector;
