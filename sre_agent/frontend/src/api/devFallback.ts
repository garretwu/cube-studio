import {
  alertClusters,
  alerts,
  knowledgeBaseDetails,
  knowledgeBases,
  knowledgeDocuments,
  skills,
  topologyEdges,
  topologyExplorerMock,
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
    recent_events: [
      "vLLM 推理服务正在进行金丝雀验证",
      "node-gpu-01 检测到 GPU 热压升高",
    ],
  };
}

export function getTopologyExplorerFallback(): TopologyExplorerResponse {
  return topologyExplorerMock;
}

export function getKnowledgeBasesFallback(): KnowledgeBaseSummary[] {
  return knowledgeBases;
}

export function getKnowledgeBaseDetailFallback(knowledgeBaseId: string): KnowledgeBaseDetail {
  const matched = knowledgeBaseDetails.find((item) => item.id === knowledgeBaseId);
  if (!matched) {
    throw new Error("未找到对应知识库");
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
    throw new Error("未找到对应技能");
  }
  return matched;
}
