import type { OntologyNode } from "../api/types";
import { formatEntityType, formatPropertyLabel, formatWorkflowStatus } from "../utils/display";
import { formatTimestamp } from "../utils/format";
import { StatusChip, SurfaceCard } from "./ui";

type EntityDetailProps = {
  node?: OntologyNode;
};

function formatPropertyValue(value: unknown) {
  if (typeof value === "number") {
    return String(value);
  }

  if (typeof value === "string") {
    return value;
  }

  if (Array.isArray(value)) {
    return value.join("，");
  }

  if (value && typeof value === "object") {
    return JSON.stringify(value);
  }

  return "暂无";
}

function EntityDetail({ node }: EntityDetailProps) {
  if (!node) {
    return (
      <SurfaceCard description="选中一个实体后，这里会展示它的状态、属性与更新时间。" title="实体详情">
        <p className="data-list__copy">当前尚未选中实体，请从关系图或分组树中点击一个节点查看详情。</p>
      </SurfaceCard>
    );
  }

  const propertyEntries = Object.entries(node.properties ?? {});

  return (
    <SurfaceCard description="当前聚焦实体的运行快照。" title={node.name ?? node.id}>
      <div className="entity-grid">
        <div className="entity-grid__row">
          <p className="entity-grid__label">类型</p>
          <div>
            <StatusChip tone="accent">{formatEntityType(node.entity_type)}</StatusChip>
          </div>
        </div>
        <div className="entity-grid__row">
          <p className="entity-grid__label">状态</p>
          <p className="entity-grid__value">{formatWorkflowStatus(node.status)}</p>
        </div>
        <div className="entity-grid__row">
          <p className="entity-grid__label">更新时间</p>
          <p className="entity-grid__value">{formatTimestamp(node.updated_at)}</p>
        </div>
        <div className="entity-grid__row">
          <p className="entity-grid__label">属性</p>
          <div className="page-stack" style={{ gap: "8px" }}>
            {propertyEntries.length > 0 ? (
              propertyEntries.map(([key, value]) => (
                <div key={key} className="status-row">
                  <StatusChip tone="neutral">{formatPropertyLabel(key)}</StatusChip>
                  <span className="entity-grid__value">{formatPropertyValue(value)}</span>
                </div>
              ))
            ) : (
              <p className="entity-grid__value">暂无属性</p>
            )}
          </div>
        </div>
      </div>
    </SurfaceCard>
  );
}

export default EntityDetail;
