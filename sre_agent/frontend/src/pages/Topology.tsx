import { useEffect, useMemo } from "react";
import { Card, Col, Row, Space, Spin, Tabs, Tag, Tree, Typography } from "antd";
import type { DataNode } from "antd/es/tree";

import EntityDetail from "../components/EntityDetail";
import TopologyGraph from "../components/TopologyGraph";
import { useTopologyStore } from "../store/topologyStore";

function TopologyPage() {
  const { nodes, edges, activeAlerts, recentEvents, selectedNodeId, isLoading, fetchTopology, selectNode } = useTopologyStore();

  useEffect(() => {
    void fetchTopology();
  }, [fetchTopology]);

  const selectedNode = nodes.find((node) => node.id === selectedNodeId);

  const treeData = useMemo<DataNode[]>(() => {
    const grouped = new Map<string, DataNode[]>();
    nodes.forEach((node) => {
      const list = grouped.get(node.entity_type) ?? [];
      list.push({ key: node.id, title: node.name ?? node.id });
      grouped.set(node.entity_type, list);
    });
    return Array.from(grouped.entries()).map(([entityType, children]) => ({
      key: entityType,
      title: `${entityType} (${children.length})`,
      children,
    }));
  }, [nodes]);

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Typography.Title level={2} style={{ margin: 0 }}>
            AIDC Topology Command Surface
          </Typography.Title>
          <Typography.Text type="secondary">
            Track physical, network, platform, and service relationships in one graph so blast radius is visible before we act.
          </Typography.Text>
          <div className="metric-strip">
            <div className="metric-card">
              <div className="metric-label">Entities</div>
              <div className="metric-value">{nodes.length}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Relationships</div>
              <div className="metric-value">{edges.length}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Active Alerts</div>
              <div className="metric-value">{activeAlerts}</div>
            </div>
          </div>
        </Space>
      </Card>

      <Row gutter={[20, 20]}>
        <Col xs={24} xl={16}>
          <Card className="panel-card" title="Topology Explorer" extra={<Tag color="cyan">D3 + Tree</Tag>}>
            {isLoading ? (
              <Spin />
            ) : (
              <Tabs
                defaultActiveKey="graph"
                items={[
                  {
                    key: "graph",
                    label: "Force Graph",
                    children: <TopologyGraph nodes={nodes} edges={edges} selectedId={selectedNodeId} onSelect={selectNode} />,
                  },
                  {
                    key: "tree",
                    label: "Grouped Tree",
                    children: <Tree treeData={treeData} selectedKeys={selectedNodeId ? [selectedNodeId] : []} onSelect={(keys) => selectNode(String(keys[0]))} />,
                  },
                ]}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <EntityDetail node={selectedNode} />
            <Card className="panel-card" title="Recent Events">
              <Space direction="vertical" style={{ width: "100%" }}>
                {recentEvents.map((event) => (
                  <Tag key={event} style={{ padding: "10px 12px" }}>
                    {event}
                  </Tag>
                ))}
              </Space>
            </Card>
          </Space>
        </Col>
      </Row>
    </div>
  );
}

export default TopologyPage;
