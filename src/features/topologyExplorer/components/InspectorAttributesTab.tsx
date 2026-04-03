import { useMemo, useState } from "react";

import type { TopologyObject } from "../../../api/types";
import { AppButton } from "../../../components/ui";
import { formatTopologyAttributeValue } from "../formatters";

type InspectorAttributesTabProps = {
  node: TopologyObject;
};

function InspectorAttributesTab({ node }: InspectorAttributesTabProps) {
  const [expanded, setExpanded] = useState(false);
  const entries = useMemo(() => Object.entries(node.attributes ?? {}), [node.attributes]);
  const visibleEntries = expanded ? entries : entries.slice(0, 6);

  return (
    <div className="topology-modified-inspector-tab">
      <div className="topology-modified-data-list">
        {visibleEntries.map(([key, value]) => (
          <div key={key} className="topology-modified-data-list__row">
            <span className="topology-modified-detail-grid__label">{key}</span>
            <span className="topology-modified-detail-grid__value">{formatTopologyAttributeValue(value)}</span>
          </div>
        ))}
      </div>
      {entries.length > 6 ? (
        <AppButton size="sm" variant="secondary" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "折叠更多字段" : "展开更多字段"}
        </AppButton>
      ) : null}
    </div>
  );
}

export default InspectorAttributesTab;
