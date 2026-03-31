import { useCallback, useEffect, useMemo } from "react";
import { Button, Spin, Tabs, Tree } from "antd";
import type { DataNode } from "antd/es/tree";

import type { WSEvent } from "../api/types";
import { buildBackendWsUrl } from "../api/ws";
import EntityDetail from "../components/EntityDetail";
import TopologyGraph from "../components/TopologyGraph";
import { AppIcon, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useWebSocket } from "../hooks/useWebSocket";
import { useTopologyStore } from "../store/topologyStore";
import { formatEntityType } from "../utils/display";
import { formatTimestamp } from "../utils/format";

function TopologyPage() {
  const {
    nodes,
    edges,
    activeAlerts,
    recentEvents,
    selectedNodeId,
    isLoading,
    isDiscovering,
    error,
    requestState,
    syncState,
    wsState,
    lastSyncedAt,
    fetchTopology,
    triggerDiscover,
    applyTopologyEvent,
    setWsState,
    selectNode,
  } = useTopologyStore();

  useEffect(() => {
    void fetchTopology();
  }, [fetchTopology]);

  const wsUrl = useMemo(() => buildBackendWsUrl("/ws/topology"), []);
  const enableTopologyWs = import.meta.env.MODE !== "test";
  const onTopologyEvent = useCallback(
    (event: WSEvent) => {
      applyTopologyEvent(event);
    },
    [applyTopologyEvent],
  );
  const { state: currentWsState } = useWebSocket(wsUrl, onTopologyEvent, {
    enabled: enableTopologyWs,
    maxBufferedMessages: 300,
  });
  useEffect(() => {
    setWsState(currentWsState);
  }, [currentWsState, setWsState]);

  const selectedNode = nodes.find((node) => node.id === selectedNodeId);
  const metricCards = [
    {
      key: "entities",
      label: "实体数",
      value: nodes.length,
      hint: "当前已纳入拓扑图谱的实体数量",
      icon: "layers" as const,
    },
    {
      key: "relations",
      label: "关系数",
      value: edges.length,
      hint: "实体之间的依赖与连接关系",
      icon: "algorithm" as const,
    },
    {
      key: "alerts",
      label: "活动告警",
      value: activeAlerts,
      hint: "与当前拓扑关联的 firing 告警",
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

  const syncTone = syncState === "error" ? "danger" : syncState === "degraded" ? "warning" : "accent";
  const wsTone = wsState === "open" ? "success" : wsState === "connecting" ? "warning" : wsState === "error" ? "danger" : "neutral";

  return (
    <div className="page-grid topology-page">
      <div className="page-intro">
        <SectionHeader
          eyebrow="运行拓扑"
          title="AIDC 拓扑总览"
          description="以快照 + 实时事件流展示基础设施拓扑，用于后续故障半径评估与诊断关联。"
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
          actions={
            <div style={{ display: "flex", gap: 8 }}>
              <StatusChip tone={syncTone}>Sync: {syncState}</StatusChip>
              <StatusChip tone={wsTone}>WS: {wsState}</StatusChip>
              <Button size="small" loading={isDiscovering} onClick={() => void triggerDiscover()}>
                手工刷新
              </Button>
            </div>
          }
          title="拓扑浏览器"
          description={lastSyncedAt ? `最近同步：${formatTimestamp(lastSyncedAt)}` : "尚未完成同步"}
        >
          {isLoading ? (
            <div className="state-block">
              <Spin />
            </div>
          ) : error && requestState === "request_failed" ? (
            <div className="state-block">
              <div>请求失败（网络/跨域/后端不可达）</div>
              <div style={{ marginTop: 8 }}>{error}</div>
            </div>
          ) : nodes.length === 0 ? (
            <div className="state-block">请求成功，但当前暂无拓扑数据。</div>
          ) : (
            <Tabs
              className="app-tabs"
              defaultActiveKey="graph"
              items={[
                {
                  key: "graph",
                  label: "关系图",
                  children: <TopologyGraph nodes={nodes} edges={edges} selectedId={selectedNodeId} onSelect={selectNode} />,
                },
                {
                  key: "tree",
                  label: "分组树",
                  children: (
                    <Tree
                      className="app-tree"
                      treeData={treeData}
                      selectedKeys={selectedNodeId ? [selectedNodeId] : []}
                      onSelect={(keys) => selectNode(String(keys[0] ?? ""))}
                    />
                  ),
                },
              ]}
            />
          )}
        </SurfaceCard>

        <div className="page-stack">
          <EntityDetail node={selectedNode} />
          <SurfaceCard title="最近事件" description="拓扑同步和增量变更事件">
            <div className="mini-card-list">
              {recentEvents.length === 0 ? (
                <div className="mini-card">
                  <p className="mini-card__copy">暂无最近事件。</p>
                </div>
              ) : (
                recentEvents.map((event) => (
                  <div key={event} className="mini-card">
                    <p className="mini-card__copy">{event}</p>
                  </div>
                ))
              )}
            </div>
          </SurfaceCard>
        </div>
      </div>
    </div>
  );
}

export default TopologyPage;
