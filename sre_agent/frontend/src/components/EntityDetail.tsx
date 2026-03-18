import { Card, Descriptions, Tag } from "antd";

import type { OntologyNode } from "../api/types";

type EntityDetailProps = {
  node?: OntologyNode;
};

function EntityDetail({ node }: EntityDetailProps) {
  if (!node) {
    return (
      <Card className="panel-card" title="Entity Detail">
        Select an entity to inspect relationships, properties, and recent changes.
      </Card>
    );
  }

  return (
    <Card className="panel-card" title={node.name ?? node.id}>
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Type">
          <Tag color="cyan">{node.entity_type}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Status">{node.status ?? "unknown"}</Descriptions.Item>
        <Descriptions.Item label="Updated">{node.updated_at}</Descriptions.Item>
        <Descriptions.Item label="Properties">
          <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(node.properties, null, 2)}</pre>
        </Descriptions.Item>
      </Descriptions>
    </Card>
  );
}

export default EntityDetail;
