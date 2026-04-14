import type { TopologyObject } from "../../api/types";

export type TopologyCanvasVariant = "default" | "modified";

export type TopologyCanvasMetrics = {
  nodeWidth: number;
  nodeHeight: number;
  nodeCircleSize: number;
  iconSize: number;
  layerXOffset: number;
  layerYOffset: number;
  layerXSpacing: number;
  layerYSpacing: number;
  typeXOffset: number;
  typeYOffset: number;
  typeYSpacing: number;
  typeOrder: Record<TopologyObject["type"], { x: number; y: number }>;
};

export const TOPOLOGY_CANVAS_METRICS: Record<TopologyCanvasVariant, TopologyCanvasMetrics> = {
  default: {
    nodeWidth: 148,
    nodeHeight: 112,
    nodeCircleSize: 68,
    iconSize: 22,
    layerXOffset: 124,
    layerYOffset: 138,
    layerXSpacing: 286,
    layerYSpacing: 156,
    typeXOffset: 28,
    typeYOffset: 18,
    typeYSpacing: 154,
    typeOrder: {
      cluster: { x: 112, y: 156 },
      rack: { x: 112, y: 388 },
      switch: { x: 452, y: 156 },
      port: { x: 452, y: 276 },
      bmc: { x: 636, y: 276 },
      node: { x: 452, y: 396 },
      gpu: { x: 820, y: 332 },
      service: { x: 1160, y: 218 },
    },
  },
  modified: {
    nodeWidth: 128,
    nodeHeight: 88,
    nodeCircleSize: 52,
    iconSize: 18,
    layerXOffset: 96,
    layerYOffset: 118,
    layerXSpacing: 238,
    layerYSpacing: 120,
    typeXOffset: 20,
    typeYOffset: 12,
    typeYSpacing: 118,
    typeOrder: {
      cluster: { x: 88, y: 126 },
      rack: { x: 88, y: 306 },
      switch: { x: 344, y: 126 },
      port: { x: 344, y: 216 },
      bmc: { x: 496, y: 216 },
      node: { x: 344, y: 306 },
      gpu: { x: 620, y: 238 },
      service: { x: 884, y: 170 },
    },
  },
};

export const DEFAULT_FIT_PADDING = 0.12;
export const FOCUSED_FIT_PADDING = 0.1;
