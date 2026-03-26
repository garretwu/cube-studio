import { useEffect, useMemo } from "react";
<<<<<<< HEAD
import { Alert, Card, Col, Empty, Row, Space, Spin, Tabs, Tag, Tree, Typography } from "antd";
=======
import { Spin, Tabs, Tree } from "antd";
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)
import type { DataNode } from "antd/es/tree";

import EntityDetail from "../components/EntityDetail";
import TopologyGraph from "../components/TopologyGraph";
import { AppIcon, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useTopologyStore } from "../store/topologyStore";
import { formatEntityType } from "../utils/display";

function TopologyPage() {
<<<<<<< HEAD
  const { nodes, edges, activeAlerts, recentEvents, selectedNodeId, isLoading, error, fetchTopology, selectNode } = useTopologyStore();
=======
  const { nodes, edges, activeAlerts, recentEvents, selectedNodeId, isLoading, fetchTopology, selectNode } =
    useTopologyStore();
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)

  useEffect(() => {
    void fetchTopology();
  }, [fetchTopology]);

  const selectedNode = nodes.find((node) => node.id === selectedNodeId);
  const metricCards = [
    {
      key: "entities",
      label: "实体数",
      value: nodes.length,
      hint: "当前已纳入拓扑的物理与逻辑实体数量。",
      icon: "layers" as const,
    },
    {
      key: "relations",
      label: "关联数",
      value: edges.length,
      hint: "覆盖机柜、节点、交换机与服务之间的依赖关系。",
      icon: "algorithm" as const,
    },
    {
      key: "alerts",
      label: "活动告警",
      value: activeAlerts,
      hint: "已挂接到当前拓扑视图的活动告警数量。",
      icon: "notification" as const,
    },
  ];

  const treeData = useMemo<DataNode[]>(() => {
    const grouped = new Map<string, DataNode[]>();
    nodes.forEach((node) => {
      const list = grouped.get(node.entity_type) ?? [];
      list.push({ key: node.id, title: node.name ?? node.id });
      grouped.set(node.entity_type, list);
    });

    return Array.from(grouped.entries()).map(([entityType, children]) => ({
      key: entityType,
      title: `${formatEntityType(entityType)}（${children.length}）`,
      children,
    }));
  }, [nodes]);

  return (
    <div className="page-grid topology-page">
      <div className="page-intro">
        <SectionHeader
          description="在一张关系图里串联物理资源、网络链路、平台节点与服务依赖，让影响评估和路径定位在此处更直接。"
          eyebrow="运行拓扑"
          title="AIDC 拓扑总览"
        />
      </div>

      <div className="topology-metric-grid">
        {metricCards.map((card) => (
          <article key={card.key} className={`topology-metric-card topology-metric-card--${card.key}`}>
            <div className="topology-metric-card__icon" aria-hidden="true">
              <AppIcon name={card.icon} size={22} />
            </div>
            <div className="topology-metric-card__body">
              <p className="topology-metric-card__label">{card.label}</p>
              <p className="topology-metric-card__value">{card.value}</p>
              <p className="topology-metric-card__hint">{card.hint}</p>
            </div>
          </article>
        ))}
      </div>

      <div className="page-two-col topology-page__content">
        <SurfaceCard
          actions={<StatusChip tone="accent">图谱 + 树视图</StatusChip>}
          description="在力导向图和分组树之间切换，快速查看依赖路径与影响范围。"
          title="拓扑浏览器"
        >
          {isLoading ? (
            <div className="state-block">
              <Spin />
<<<<<<< HEAD
            ) : error ? (
              <Alert
                type="error"
                showIcon
                message="Unable to load topology"
                description={error}
              />
            ) : nodes.length === 0 ? (
              <Empty description="No topology data is available in the current ontology graph." />
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
              {recentEvents.length === 0 ? (
                <Typography.Text type="secondary">No recent topology events are available.</Typography.Text>
              ) : (
                <Space direction="vertical" style={{ width: "100%" }}>
                  {recentEvents.map((event) => (
                    <Tag key={event} style={{ padding: "10px 12px" }}>
                      {event}
                    </Tag>
                  ))}
                </Space>
              )}
            </Card>
          </Space>
        </Col>
      </Row>
=======
            </div>
          ) : (
            <Tabs
              className="app-tabs"
              defaultActiveKey="graph"
              items={[
                {
                  key: "graph",
                  label: "关系图",
                  children: <TopologyGraph nodes={nodes} edges={edges} onSelect={selectNode} selectedId={selectedNodeId} />,
                },
                {
                  key: "tree",
                  label: "分组树",
                  children: (
                    <Tree
                      className="app-tree"
                      onSelect={(keys) => selectNode(String(keys[0] ?? ""))}
                      selectedKeys={selectedNodeId ? [selectedNodeId] : []}
                      treeData={treeData}
                    />
                  ),
                },
              ]}
            />
          )}
        </SurfaceCard>

        <div className="page-stack">
          <EntityDetail node={selectedNode} />
          <SurfaceCard description="影响当前拓扑态势的重要变化与状态流转。" title="最近事件">
            <div className="mini-card-list">
              {recentEvents.map((event) => (
                <div key={event} className="mini-card">
                  <p className="mini-card__copy">{event}</p>
                </div>
              ))}
            </div>
          </SurfaceCard>
        </div>
      </div>
>>>>>>> dd3aadbc (feat(frontend): redesign auto-sre console ui)
    </div>
  );
}

export default TopologyPage;
