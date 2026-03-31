import type { TopologyObject } from "../../../api/types";
import { formatTopologyLayer, formatTopologyStatus, formatTopologyType, getLocationLabel, getStatusTone } from "../formatters";
import { StatusChip } from "../../../components/ui";

type InspectorOverviewTabProps = {
  node: TopologyObject;
};

function InspectorOverviewTab({ node }: InspectorOverviewTabProps) {
  return (
    <div className="topology-modified-inspector-tab">
      <div className="topology-modified-detail-grid">
        <div>
          <p className="topology-modified-detail-grid__label">对象名称</p>
          <p className="topology-modified-detail-grid__value">{node.name}</p>
        </div>
        <div>
          <p className="topology-modified-detail-grid__label">对象类型</p>
          <p className="topology-modified-detail-grid__value">{formatTopologyType(node.type)}</p>
        </div>
        <div>
          <p className="topology-modified-detail-grid__label">状态</p>
          <StatusChip tone={getStatusTone(node.status)}>{formatTopologyStatus(node.status)}</StatusChip>
        </div>
        <div>
          <p className="topology-modified-detail-grid__label">所属层级</p>
          <p className="topology-modified-detail-grid__value">{formatTopologyLayer(node.layer)}</p>
        </div>
      </div>
      <div className="topology-modified-inspector-note">
        <p className="topology-modified-detail-grid__label">摘要</p>
        <p className="topology-modified-detail-grid__value">{node.summary}</p>
      </div>
      <div className="topology-modified-inspector-note">
        <p className="topology-modified-detail-grid__label">所属位置</p>
        <p className="topology-modified-detail-grid__value">{getLocationLabel(node)}</p>
      </div>
    </div>
  );
}

export default InspectorOverviewTab;
