import {
  alertClusters,
  alerts,
  knowledgeBaseDetails,
  knowledgeBases,
  knowledgeDocuments,
  remediationOverview,
  skills,
  topologyEdges,
  topologyExplorerOnlineMock,
  topologyNodes,
} from "../mocks/data";
import type {
  Alert,
  AlertCluster,
  KnowledgeBaseDetail,
  KnowledgeBaseSummary,
  KnowledgeDocument,
  OntologyEdge,
  OntologyNode,
  RemediationOverview,
  SkillDescriptor,
  TopologyExplorerResponse,
} from "./types";

export type TopologyFallbackResponse = {
  nodes: OntologyNode[];
  edges: OntologyEdge[];
  active_alerts: number;
  recent_events: string[];
  snapshot_id?: string | null;
  last_synced_at?: string | null;
  sync_state?: "idle" | "syncing" | "ready" | "degraded" | "error";
};

export function getTopologyFallback(): TopologyFallbackResponse {
  return {
    nodes: topologyNodes,
    edges: topologyEdges,
    active_alerts: alerts.filter((alert) => alert.status === "firing").length,
    recent_events: ["vLLM inference canary is running.", "node-gpu-01 reported high temperature."],
  };
}

export function getTopologyExplorerFallback(): TopologyExplorerResponse {
  return topologyExplorerOnlineMock;
}

export function getAlertsFallback(): { alerts: Alert[]; clusters: AlertCluster[] } {
  return {
    alerts,
    clusters: alertClusters,
  };
}

export function getRemediationOverviewFallback(): RemediationOverview {
  return remediationOverview;
}

export function getKnowledgeBasesFallback(): KnowledgeBaseSummary[] {
  return knowledgeBases;
}

export function getKnowledgeBaseDetailFallback(knowledgeBaseId: string): KnowledgeBaseDetail {
  const matched = knowledgeBaseDetails.find((item) => item.id === knowledgeBaseId);
  if (!matched) {
    throw new Error("Knowledge base not found");
  }
  return matched;
}

export function searchKnowledgeFallback(query: string, category?: string): KnowledgeDocument[] {
  const normalized = query.trim().toLowerCase();
  return knowledgeDocuments.filter((doc) => {
    const categoryMatch = !category || doc.category === category;
    const haystack = `${doc.title} ${doc.excerpt} ${doc.tags.join(" ")}`.toLowerCase();
    return categoryMatch && (!normalized || haystack.includes(normalized) || normalized.includes("rocev2"));
  });
}

export function getKnowledgeSourcesFallback(): KnowledgeDocument[] {
  return knowledgeDocuments;
}

export function getSkillsFallback(): SkillDescriptor[] {
  return skills;
}

export function getSkillFallback(skillId: string): SkillDescriptor {
  const matched = skills.find((skill) => skill.id === skillId);
  if (!matched) {
    throw new Error("Skill not found");
  }
  return matched;
}
