import { create } from "zustand";

import { apiClient } from "../api/client";
import type { OntologyEdge, OntologyNode, WSEvent } from "../api/types";

type SyncState = "idle" | "syncing" | "ready" | "degraded" | "error";
type RequestState = "idle" | "request_failed";

type TopologyState = {
  nodes: OntologyNode[];
  edges: OntologyEdge[];
  activeAlerts: number;
  recentEvents: string[];
  snapshotId?: string;
  lastSyncedAt?: string;
  syncState: SyncState;
  wsState: "connecting" | "open" | "closed" | "error";
  error?: string;
  requestState: RequestState;
  selectedNodeId?: string;
  isLoading: boolean;
  isDiscovering: boolean;
  fetchTopology: () => Promise<void>;
  triggerDiscover: () => Promise<void>;
  applyTopologyEvent: (event: WSEvent) => void;
  setWsState: (state: "connecting" | "open" | "closed" | "error") => void;
  selectNode: (nodeId?: string) => void;
};

function pushRecentEvent(events: string[], message: string): string[] {
  const value = message.trim();
  if (!value) {
    return events;
  }
  const next = [...events, value];
  return next.slice(Math.max(0, next.length - 20));
}

function upsertNode(nodes: OntologyNode[], node: OntologyNode): OntologyNode[] {
  const index = nodes.findIndex((item) => item.id === node.id);
  if (index < 0) {
    return [...nodes, node];
  }
  const next = [...nodes];
  next[index] = node;
  return next;
}

function removeNode(nodes: OntologyNode[], nodeId: string): OntologyNode[] {
  return nodes.filter((item) => item.id !== nodeId);
}

function edgeKey(edge: Pick<OntologyEdge, "source_id" | "target_id" | "relation">): string {
  return `${edge.source_id}::${edge.target_id}::${edge.relation}`;
}

function upsertEdge(edges: OntologyEdge[], edge: OntologyEdge): OntologyEdge[] {
  const key = edgeKey(edge);
  const index = edges.findIndex((item) => edgeKey(item) === key);
  if (index < 0) {
    return [...edges, edge];
  }
  const next = [...edges];
  next[index] = edge;
  return next;
}

function removeEdge(
  edges: OntologyEdge[],
  payload: { source_id: string; target_id: string; relation: string },
): OntologyEdge[] {
  const target = `${payload.source_id}::${payload.target_id}::${payload.relation}`;
  return edges.filter((item) => edgeKey(item) !== target);
}

export const useTopologyStore = create<TopologyState>((set) => ({
  nodes: [],
  edges: [],
  activeAlerts: 0,
  recentEvents: [],
  snapshotId: undefined,
  lastSyncedAt: undefined,
  syncState: "idle",
  wsState: "closed",
  error: undefined,
  requestState: "idle",
  selectedNodeId: undefined,
  isLoading: false,
  isDiscovering: false,
  fetchTopology: async () => {
    set({ isLoading: true, error: undefined, requestState: "idle" });
    try {
      const response = await apiClient.getTopology();
      set((state) => ({
        nodes: response.nodes,
        edges: response.edges,
        activeAlerts: response.active_alerts,
        recentEvents: response.recent_events,
        snapshotId: response.snapshot_id ?? state.snapshotId,
        lastSyncedAt: response.last_synced_at ?? state.lastSyncedAt,
        syncState: response.sync_state ?? state.syncState,
        isLoading: false,
        error: undefined,
        requestState: "idle",
        selectedNodeId: state.selectedNodeId ?? response.nodes[0]?.id,
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load topology";
      set({
        nodes: [],
        edges: [],
        activeAlerts: 0,
        recentEvents: [],
        selectedNodeId: undefined,
        isLoading: false,
        error: message,
        requestState: "request_failed",
      });
    }
  },
  triggerDiscover: async () => {
    set({ isDiscovering: true, error: undefined });
    try {
      const status = await apiClient.triggerTopologyDiscover();
      set((state) => ({
        isDiscovering: false,
        syncState: status.sync_state,
        snapshotId: status.snapshot_id ?? state.snapshotId,
        lastSyncedAt: status.last_synced_at ?? state.lastSyncedAt,
        recentEvents: status.last_error
          ? pushRecentEvent(state.recentEvents, status.last_error)
          : state.recentEvents,
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to trigger topology discovery";
      set({ isDiscovering: false, error: message });
    }
  },
  applyTopologyEvent: (event) =>
    set((state) => {
      if (event.type !== "topology") {
        return state;
      }
      const data = event.data ?? {};
      const action = typeof data.action === "string" ? data.action : "";
      let nodes = state.nodes;
      let edges = state.edges;
      let syncState = state.syncState;
      let snapshotId = state.snapshotId;
      let lastSyncedAt = state.lastSyncedAt;
      let recentEvents = state.recentEvents;

      if (action === "node_upsert" && data.node && typeof data.node === "object") {
        nodes = upsertNode(nodes, data.node as OntologyNode);
      } else if (action === "node_remove" && typeof data.id === "string") {
        nodes = removeNode(nodes, data.id);
        edges = edges.filter((edge) => edge.source_id !== data.id && edge.target_id !== data.id);
      } else if (action === "edge_upsert" && data.edge && typeof data.edge === "object") {
        edges = upsertEdge(edges, data.edge as OntologyEdge);
      } else if (
        action === "edge_remove" &&
        typeof data.source_id === "string" &&
        typeof data.target_id === "string" &&
        typeof data.relation === "string"
      ) {
        edges = removeEdge(edges, {
          source_id: data.source_id,
          target_id: data.target_id,
          relation: data.relation,
        });
      } else if (action === "sync_started") {
        syncState = "syncing";
        recentEvents = pushRecentEvent(recentEvents, "Topology sync started");
      } else if (action === "sync_succeeded") {
        syncState = (data.sync_state as SyncState) || "ready";
        if (typeof data.snapshot_id === "string") {
          snapshotId = data.snapshot_id;
        }
        if (typeof data.last_synced_at === "string") {
          lastSyncedAt = data.last_synced_at;
        }
        recentEvents = pushRecentEvent(recentEvents, "Topology sync succeeded");
      } else if (action === "sync_failed") {
        syncState = "error";
        const message = typeof data.error === "string" ? data.error : "Topology sync failed";
        recentEvents = pushRecentEvent(recentEvents, message);
      }

      const selectedNodeId = state.selectedNodeId && nodes.some((node) => node.id === state.selectedNodeId)
        ? state.selectedNodeId
        : nodes[0]?.id;
      return {
        nodes,
        edges,
        syncState,
        snapshotId,
        lastSyncedAt,
        recentEvents,
        selectedNodeId,
      };
    }),
  setWsState: (wsState) => set({ wsState }),
  selectNode: (selectedNodeId) => set({ selectedNodeId }),
}));

