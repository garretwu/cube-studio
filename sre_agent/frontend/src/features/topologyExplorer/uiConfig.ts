import type { TopologyObject } from "../../api/types";
import type { ExplorerLayoutPreset, TopologyScopeMode } from "./types";

export type TopologyRoomOption = {
  id: string;
  name: string;
  iconName: "mapPin";
};

export type TopologyCanvasViewControl = {
  id: string;
  label: string;
  viewMode: "graph" | "tree";
  layoutPreset?: ExplorerLayoutPreset;
};

export const topologyRoomOptions = [
  {
    id: "wangjing-test",
    name: "\u671b\u4eac\u6d4b\u8bd5\u673a\u623f",
    iconName: "mapPin",
  },
] as const satisfies readonly TopologyRoomOption[];

export const defaultTopologyScopeMode: TopologyScopeMode = "room";
export const defaultTopologyRoomId = topologyRoomOptions[0].id;

export const topologyLegendTypeOrder = [
  "cluster",
  "rack",
  "node",
  "gpu",
  "service",
  "switch",
  "port",
  "bmc",
] as const satisfies readonly TopologyObject["type"][];

export const topologyCanvasViewControls = [
  {
    id: "graph-layered",
    label: "\u5173\u7cfb\u56fe\uff08\u5c42\u5e03\u5c40\uff09",
    viewMode: "graph",
    layoutPreset: "layered",
  },
  {
    id: "graph-domain",
    label: "\u5173\u7cfb\u56fe\uff08\u57df\u5e03\u5c40\uff09",
    viewMode: "graph",
    layoutPreset: "domain",
  },
  {
    id: "tree",
    label: "\u6811\u89c6\u56fe",
    viewMode: "tree",
  },
] as const satisfies readonly TopologyCanvasViewControl[];