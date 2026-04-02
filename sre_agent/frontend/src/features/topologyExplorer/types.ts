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
