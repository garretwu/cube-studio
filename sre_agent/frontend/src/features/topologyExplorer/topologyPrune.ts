import type { TopologyExplorerResponse, TopologyObject, TopologyRelation, TopologyPath } from "../../api/types";

export const DEFAULT_TOPOLOGY_NAMESPACE_EXCLUDELIST = [
  "default",
  "kyverno",
  "karmada-system",
  "logging",
  "monitoring",
  "nvidia-device-plugin",
  "cilium-test-1",
  "kagent",
  "istio-system",
  "lws-system",
] as const;

function normalize(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

function parseCsvList(value: unknown) {
  return String(value ?? "")
    .split(",")
    .map((item) => normalize(item))
    .filter(Boolean);
}

function getNamespaceExcludeList() {
  const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
  const processEnv =
    typeof process !== "undefined"
      ? (process.env as Record<string, string | undefined>)
      : undefined;
  const configured = String(
    env?.VITE_TOPOLOGY_NAMESPACE_EXCLUDELIST ?? processEnv?.VITE_TOPOLOGY_NAMESPACE_EXCLUDELIST ?? "",
  ).trim();
  const namespaces = configured ? parseCsvList(configured) : DEFAULT_TOPOLOGY_NAMESPACE_EXCLUDELIST.map(normalize);
  return new Set(namespaces);
}

function getNodeNamespace(node: TopologyObject) {
  const attrNamespace = node.attributes.namespace;
  if (typeof attrNamespace === "string" && attrNamespace.trim()) {
    return attrNamespace.trim();
  }

  if (node.id.startsWith("ns:")) {
    return node.id.slice("ns:".length).split(":")[0];
  }

  if (node.id.startsWith("pod:") || node.id.startsWith("svc:")) {
    return node.id.split(":")[1];
  }

  if (node.name.includes("/")) {
    return node.name.split("/")[0];
  }

  return undefined;
}

function prunePaths(paths: TopologyPath[], keepNodeIds: Set<string>, keepEdgeIds: Set<string>) {
  return paths
    .map((path) => {
      const entryOk = keepNodeIds.has(path.entryNodeId);
      const rootOk = keepNodeIds.has(path.rootCauseNodeId);
      if (!entryOk || !rootOk) {
        return null;
      }

      const affectedNodeIds = path.affectedNodeIds.filter((id) => keepNodeIds.has(id));
      const edgeIds = path.edgeIds.filter((id) => keepEdgeIds.has(id));
      return { ...path, affectedNodeIds, edgeIds };
    })
    .filter((path): path is TopologyPath => Boolean(path));
}

export function pruneTopologyExplorerResponse(raw: TopologyExplorerResponse): TopologyExplorerResponse {
  const excludedNamespaces = getNamespaceExcludeList();
  if (excludedNamespaces.size === 0) {
    return raw;
  }

  const nodes = raw.nodes.filter((node) => {
    const namespace = getNodeNamespace(node);
    return !namespace || !excludedNamespaces.has(normalize(namespace));
  });
  const keepNodeIds = new Set(nodes.map((node) => node.id));

  const edges = raw.edges.filter((edge) => keepNodeIds.has(edge.source) && keepNodeIds.has(edge.target));
  const keepEdgeIds = new Set(edges.map((edge) => edge.id));
  const paths = prunePaths(raw.paths, keepNodeIds, keepEdgeIds);

  return { ...raw, nodes, edges, paths };
}
