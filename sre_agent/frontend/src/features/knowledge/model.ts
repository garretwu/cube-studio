import type {
  KnowledgeBaseDetail,
  KnowledgeBaseDocument,
  KnowledgeBaseScope,
  KnowledgeBaseStatus,
  KnowledgeBaseSummary,
  KnowledgeDocSourceType,
  KnowledgeIndexStatus,
} from "../../api/types";

type StatusTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

export type KnowledgeBaseFilterState = {
  status: "all" | KnowledgeBaseStatus;
  indexStatus: "all" | KnowledgeIndexStatus;
  query: string;
};

export type KnowledgeDocumentFilterState = {
  sourceType: "all" | KnowledgeDocSourceType;
  status: "all" | KnowledgeBaseStatus;
  indexStatus: "all" | KnowledgeIndexStatus;
  query: string;
};

export type KnowledgeMetrics = {
  baseCount: number;
  documentCount: number;
  storageBytes: number;
  searchableCount: number;
};

export const DEFAULT_KNOWLEDGE_BASE_FILTERS: KnowledgeBaseFilterState = {
  status: "all",
  indexStatus: "all",
  query: "",
};

export const DEFAULT_KNOWLEDGE_DOCUMENT_FILTERS: KnowledgeDocumentFilterState = {
  sourceType: "all",
  status: "all",
  indexStatus: "all",
  query: "",
};

function normalizeQuery(value: string) {
  return value.trim().toLowerCase();
}

function matchesText(haystackParts: Array<string | null | undefined>, query: string) {
  if (!query) {
    return true;
  }

  return haystackParts
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .includes(query);
}

export function formatKnowledgeScopeLabel(value: KnowledgeBaseScope) {
  return value === "shared" ? "公共知识库" : "私有知识库";
}

export function formatKnowledgeScopeShortLabel(value: KnowledgeBaseScope) {
  return value === "shared" ? "公共" : "私有";
}

export function formatKnowledgeStatusLabel(value: KnowledgeBaseStatus) {
  return value === "enabled" ? "已启用" : "已停用";
}

export function formatKnowledgeIndexStatusLabel(value: KnowledgeIndexStatus) {
  switch (value) {
    case "ready":
      return "已就绪";
    case "indexing":
      return "索引中";
    case "failed":
      return "失败";
    default:
      return "待处理";
  }
}

export function formatKnowledgeSourceTypeLabel(value: KnowledgeDocSourceType) {
  switch (value) {
    case "file":
      return "文件";
    case "manual":
      return "手录";
    default:
      return "链接";
  }
}

export function getKnowledgeStatusTone(value: KnowledgeBaseStatus): StatusTone {
  return value === "enabled" ? "success" : "danger";
}

export function getKnowledgeIndexStatusTone(value: KnowledgeIndexStatus): StatusTone {
  switch (value) {
    case "ready":
      return "success";
    case "indexing":
      return "info";
    case "failed":
      return "danger";
    default:
      return "warning";
  }
}

export function formatStorageSize(sizeBytes: number | null | undefined) {
  if (sizeBytes === null || sizeBytes === undefined || Number.isNaN(sizeBytes)) {
    return "--";
  }

  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }

  const units = ["KB", "MB", "GB", "TB"];
  let value = sizeBytes / 1024;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  const precision = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(precision).replace(/\.0+$|(?<=\.[0-9])0+$/, "")} ${units[unitIndex]}`;
}

export function filterKnowledgeBases(bases: KnowledgeBaseSummary[], filters: KnowledgeBaseFilterState) {
  const query = normalizeQuery(filters.query);

  return bases.filter((item) => {
    const matchesStatus = filters.status === "all" || item.status === filters.status;
    const matchesIndexStatus = filters.indexStatus === "all" || item.index_status === filters.indexStatus;
    const matchesQueryText = matchesText([item.name, item.code, item.description], query);
    return matchesStatus && matchesIndexStatus && matchesQueryText;
  });
}

export function filterKnowledgeDocuments(documents: KnowledgeBaseDocument[], filters: KnowledgeDocumentFilterState) {
  const query = normalizeQuery(filters.query);

  return documents.filter((item) => {
    const matchesSourceType = filters.sourceType === "all" || item.source_type === filters.sourceType;
    const matchesStatus = filters.status === "all" || item.status === filters.status;
    const matchesIndexStatus = filters.indexStatus === "all" || item.index_status === filters.indexStatus;
    const matchesQueryText = matchesText([item.title, item.file_name, item.source_label, item.preview_summary, ...item.tags], query);
    return matchesSourceType && matchesStatus && matchesIndexStatus && matchesQueryText;
  });
}

export function buildKnowledgeMetrics(bases: KnowledgeBaseSummary[]): KnowledgeMetrics {
  return bases.reduce<KnowledgeMetrics>(
    (accumulator, item) => ({
      baseCount: accumulator.baseCount + 1,
      documentCount: accumulator.documentCount + item.document_count,
      storageBytes: accumulator.storageBytes + item.storage_bytes,
      searchableCount: accumulator.searchableCount + (item.status === "enabled" && item.index_status === "ready" ? 1 : 0),
    }),
    {
      baseCount: 0,
      documentCount: 0,
      storageBytes: 0,
      searchableCount: 0,
    },
  );
}


export function buildKnowledgeDetailSummary(detail: KnowledgeBaseDetail) {
  return [
    { label: "知识库编码", value: detail.code, hint: "用于系统内唯一识别" },
    { label: "知识库范围", value: formatKnowledgeScopeShortLabel(detail.scope), hint: "按当前访问权限展示" },
    { label: "文档数量", value: String(detail.document_count), hint: "按知识库聚合统计" },
    { label: "存储占用", value: formatStorageSize(detail.storage_bytes), hint: "按当前知识库维度统计" },
    { label: "最近索引时间", value: detail.indexed_at ?? "暂无", hint: "最近一次索引完成时间" },
    { label: "创建时间", value: detail.created_at, hint: "知识库创建时间" },
    { label: "更新时间", value: detail.updated_at, hint: "最近一次资料变更时间" },
    { label: "知识库 ID", value: detail.knowledge_base_id, hint: "内部数据标识" },
  ];
}
