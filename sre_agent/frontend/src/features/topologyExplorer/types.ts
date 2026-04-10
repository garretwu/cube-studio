import type { TopologyObject } from "../../api/types";

export type ExplorerViewMode = "graph" | "tree" | "impact";
export type ExplorerStatusFilter = "all" | "abnormal";
export type ExplorerLayerFilter = "all" | TopologyObject["layer"];
export type ExplorerSummaryFilter = "all" | "abnormal" | "impacted" | "paths";
export type ExplorerLayoutPreset = "layered" | "domain";
export type InspectorTabKey = "overview" | "relations" | "status" | "attributes" | "context";
export type DrawerTabKey = "paths" | "affected" | "neighbors";
export type SearchFeedback = "idle" | "ready" | "not_found";

export type TopologyExplorerFilters = {
  statusFilter: ExplorerStatusFilter;
  layerFilter: ExplorerLayerFilter;
  summaryFilter: ExplorerSummaryFilter;
};

export type TopologyCanvasExportMeta = {
  exportedAt: string;
  source: "topology-modified-canvas";
  lastUpdated?: string;
  viewMode: "graph";
  layoutPreset: ExplorerLayoutPreset;
  zoomPercent: number;
};

export type TopologyCanvasExportNode = Pick<
  TopologyObject,
  | "id"
  | "name"
  | "type"
  | "status"
  | "layer"
  | "domain"
  | "region"
  | "zone"
  | "cluster"
  | "rack"
  | "slot"
  | "summary"
  | "tags"
  | "metrics"
  | "attributes"
> & {
  position: {
    x: number;
    y: number;
  };
  size: {
    width: number;
    height: number;
  };
};

export type TopologyCanvasExportEdge = {
  id: string;
  source: string;
  target: string;
  relationType: string;
  status: string;
  impactLevel: string;
  label?: string;
  isCritical: boolean;
  isAggregated?: boolean;
};

export type TopologyCanvasExport = {
  meta: TopologyCanvasExportMeta;
  filters: TopologyExplorerFilters & {
    searchQuery: string;
  };
  focus: {
    selectedNodeId?: string;
    hoveredNodeId?: string;
    matchedNodeIds: string[];
  };
  nodes: TopologyCanvasExportNode[];
  edges: TopologyCanvasExportEdge[];
};
