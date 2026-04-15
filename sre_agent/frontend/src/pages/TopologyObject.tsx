import { Spin } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AppButton, StatusChip } from "../components/ui";
import type { TopologyCanvasHandle } from "../features/topologyExplorer/components/TopologyCanvas";
import TopologyCanvas from "../features/topologyExplorer/components/TopologyCanvas";
import ObjectInspector from "../features/topologyExplorer/components/ObjectInspector";
import {
  getNeighborDepths,
  getObjectTopologyDetail,
} from "../features/topologyExplorer/selectors";
import { useTopologyExplorerStore } from "../features/topologyExplorer/store";
import { formatTopologyStatus, getStatusTone } from "../features/topologyExplorer/formatters";
import type { InspectorTabKey } from "../features/topologyExplorer/types";
import { buildTopologyObjectPath, resolveTopologyObjectNodeId } from "../features/topologyExplorer/topologyObjectRoute";
import "../features/topologyExplorer/topologyExplorer.css";

function TopologyObjectPage() {
  const routeParams = useParams<{ nodeId?: string; "*"?: string }>();
  const nodeId = routeParams.nodeId;
  const nodeTail = routeParams["*"];
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const canvasRef = useRef<TopologyCanvasHandle | null>(null);
  const [zoomPercent, setZoomPercent] = useState(100);
  const [inspectorTab, setInspectorTab] = useState<InspectorTabKey>("overview");
  const { data, isLoading, error, fetchTopologyExplorer } = useTopologyExplorerStore();
  const mode = searchParams.get("mode") === "isolate" ? "isolate" : "default";

  useEffect(() => {
    void fetchTopologyExplorer();
  }, [fetchTopologyExplorer]);

  const resolvedNodeId = useMemo(() => resolveTopologyObjectNodeId(nodeId, nodeTail), [nodeId, nodeTail]);

  useEffect(() => {
    setInspectorTab("overview");
  }, [resolvedNodeId, mode]);

  const detail = useMemo(
    () => getObjectTopologyDetail(data, resolvedNodeId),
    [data, resolvedNodeId],
  );
  const neighborDepths = useMemo(
    () => getNeighborDepths(detail.edges, detail.focalNode?.id, 1),
    [detail.edges, detail.focalNode?.id],
  );

  useEffect(() => {
    if (!detail.focalNode) {
      return;
    }

    window.requestAnimationFrame(() => {
      canvasRef.current?.focusNode(detail.focalNode!.id);
    });
  }, [detail.focalNode?.id]);

  const navigateToObject = (nextNodeId: string) => {
    navigate(buildTopologyObjectPath(nextNodeId, mode));
  };

  return (
    <div className="page-grid topology-modified-page topology-object-page">
      <h1 className="visually-hidden">对象拓扑详情</h1>
      <section className="page-stage topology-modified-stage">
        <div className="topology-object-header">
          <div className="topology-object-header__copy">
            <div className="topology-object-header__actions">
              <AppButton size="sm" variant="secondary" onClick={() => navigate("/topology")}>
                返回全局拓扑
              </AppButton>
              {detail.focalNode ? (
                <StatusChip tone={getStatusTone(detail.focalNode.status)}>
                  {formatTopologyStatus(detail.focalNode.status)}
                </StatusChip>
              ) : null}
              {mode === "isolate" ? <StatusChip tone="accent">Isolate</StatusChip> : null}
            </div>
            <h2 className="topology-object-header__title">
              {detail.focalNode ? detail.focalNode.name : "对象拓扑详情"}
            </h2>
            <p className="topology-object-header__description">
              {mode === "isolate"
                ? "当前页面处于隔离视图，仅保留该对象与其直接关联对象。"
                : "查看单个对象的关系、状态、属性与直连拓扑。"}
            </p>
          </div>
          <div className="topology-object-header__stage-actions">
            <AppButton size="sm" variant="secondary" onClick={() => canvasRef.current?.fitView()}>
              适配
            </AppButton>
            <AppButton size="sm" variant="secondary" onClick={() => canvasRef.current?.recenter(detail.focalNode?.id)}>
              重置
            </AppButton>
          </div>
        </div>

        {isLoading ? (
          <div className="state-block">
            <Spin />
          </div>
        ) : error ? (
          <div className="topology-modified-empty">{error}</div>
        ) : detail.notFound ? (
          <div className="topology-modified-empty">未找到对应对象，请返回全局拓扑重新选择。</div>
        ) : detail.focalNode ? (
          <div className="topology-object-layout" data-testid="topology-object-layout">
            <ObjectInspector
              affectedObjects={detail.affectedObjects}
              downstream={detail.downstream}
              inspectorTab={inspectorTab}
              neighbors={detail.neighbors}
              node={detail.focalNode}
              onHighlightInGraph={() => canvasRef.current?.focusNode(detail.focalNode!.id)}
              onSelectNode={navigateToObject}
              onTabChange={setInspectorTab}
              paths={detail.paths}
              testId="topology-object-inspector"
              upstream={detail.upstream}
            />

            <section className="topology-object-stage">
              <div className="topology-object-stage__toolbar">
                <div>
                  <p className="topology-stage-filter-panel__eyebrow">关联拓扑</p>
                  <h3 className="topology-stage-filter-panel__title">直连对象关系图</h3>
                </div>
                <div className="topology-object-stage__toolbar-actions">
                  <span className="topology-stage-dock__zoom-label">{zoomPercent}%</span>
                  <AppButton size="sm" variant="secondary" onClick={() => canvasRef.current?.zoomOut()}>
                    -
                  </AppButton>
                  <AppButton size="sm" variant="secondary" onClick={() => canvasRef.current?.zoomIn()}>
                    +
                  </AppButton>
                </div>
              </div>
              <div className="topology-object-stage__canvas">
                <TopologyCanvas
                  ref={canvasRef}
                  edges={detail.edges}
                  forceEdgeLabels
                  layoutPreset="layered"
                  matchedNodeIds={[detail.focalNode.id]}
                  neighborDepths={neighborDepths}
                  nodes={detail.nodes}
                  onHoverNode={() => undefined}
                  onSelectNode={navigateToObject}
                  onZoomChange={setZoomPercent}
                  selectedNodeId={detail.focalNode.id}
                />
              </div>
            </section>
          </div>
        ) : null}
      </section>
    </div>
  );
}

export default TopologyObjectPage;



