export type TopologyObjectMode = "default" | "isolate";

const ISOLATE_QUERY = "?mode=isolate";

function safeDecodeURIComponent(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function buildTopologyObjectPath(nodeId: string, mode: TopologyObjectMode) {
  const encodedNodeId = encodeURIComponent(nodeId);
  return `/topology/object/${encodedNodeId}${mode === "isolate" ? ISOLATE_QUERY : ""}`;
}

export function resolveTopologyObjectNodeId(nodeIdParam?: string, splatParam?: string) {
  if (!nodeIdParam) {
    return undefined;
  }

  const combined = splatParam ? `${nodeIdParam}/${splatParam}` : nodeIdParam;
  return safeDecodeURIComponent(combined);
}
