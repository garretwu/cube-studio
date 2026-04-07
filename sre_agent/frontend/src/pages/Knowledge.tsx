import { useEffect, useMemo, useState } from "react";

import { apiClient } from "../api/client";
import type { KnowledgeDataset, KnowledgeDocument, KnowledgeSearchHit, KnowledgeSegment } from "../api/types";
import { AppButton, AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { formatKnowledgeCategory } from "../utils/display";

const PAGE_LIMIT = 50;

function KnowledgePage() {
  const [datasets, setDatasets] = useState<KnowledgeDataset[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string>("");
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string>("");
  const [selectedDetail, setSelectedDetail] = useState<KnowledgeDocument | null>(null);
  const [segments, setSegments] = useState<KnowledgeSegment[]>([]);
  const [globalHits, setGlobalHits] = useState<KnowledgeSearchHit[]>([]);

  const [docKeyword, setDocKeyword] = useState("");
  const [segmentKeyword, setSegmentKeyword] = useState("");
  const [globalQuery, setGlobalQuery] = useState("");

  const [isLoadingDocs, setIsLoadingDocs] = useState(false);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [isSearchingGlobal, setIsSearchingGlobal] = useState(false);
  const [error, setError] = useState("");

  const selectedDataset = useMemo(
    () => datasets.find((item) => item.id === selectedDatasetId) ?? datasets[0] ?? null,
    [datasets, selectedDatasetId],
  );

  const selectedDocument = useMemo(
    () => documents.find((item) => item.id === selectedDocumentId) ?? selectedDetail,
    [documents, selectedDetail, selectedDocumentId],
  );

  const loadDatasets = async () => {
    setError("");
    try {
      const list = await apiClient.getKnowledgeDatasets(undefined, 1, PAGE_LIMIT).catch(() => []);
      const normalized = list.length > 0
        ? list
        : [await apiClient.getKnowledgeDataset().catch(() => null)].filter(
            (item): item is KnowledgeDataset => Boolean(item),
          );
      setDatasets(normalized);
      setSelectedDatasetId((prev) => {
        if (prev && normalized.some((item) => item.id === prev)) {
          return prev;
        }
        return normalized[0]?.id ?? "";
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "知识库列表加载失败");
      setDatasets([]);
      setSelectedDatasetId("");
    }
  };

  const loadDocuments = async (keyword: string, datasetId: string) => {
    if (!datasetId) {
      setDocuments([]);
      setSelectedDocumentId("");
      setSelectedDetail(null);
      setSegments([]);
      return;
    }
    setIsLoadingDocs(true);
    setError("");
    try {
      const [datasetPayload, docs] = await Promise.all([
        apiClient.getKnowledgeDataset(datasetId).catch(() => null),
        apiClient.getKnowledgeSources(keyword || undefined, 1, PAGE_LIMIT, datasetId),
      ]);
      const normalizedDocs = Array.isArray(docs) ? docs : [];
      if (datasetPayload) {
        setDatasets((prev) => {
          const current = [...prev];
          const index = current.findIndex((item) => item.id === datasetPayload.id);
          if (index >= 0) {
            current[index] = datasetPayload;
          } else {
            current.unshift(datasetPayload);
          }
          return current;
        });
      }
      setDocuments(normalizedDocs);
      setSelectedDocumentId((prev) => {
        if (prev && normalizedDocs.some((item) => item.id === prev)) {
          return prev;
        }
        return normalizedDocs[0]?.id ?? "";
      });
      if (normalizedDocs.length === 0) {
        setSelectedDetail(null);
        setSegments([]);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "知识文档加载失败");
      setDocuments([]);
      setSelectedDocumentId("");
      setSelectedDetail(null);
      setSegments([]);
    } finally {
      setIsLoadingDocs(false);
    }
  };

  const loadDocumentDetailAndSegments = async (documentId: string, datasetId: string, keyword = "") => {
    if (!documentId || !datasetId) {
      setSelectedDetail(null);
      setSegments([]);
      return;
    }
    setIsLoadingDetail(true);
    setError("");
    try {
      const [detail, segmentRows] = await Promise.all([
        apiClient.getKnowledgeDocumentDetail(documentId, datasetId),
        apiClient.getKnowledgeDocumentSegments(documentId, {
          datasetId,
          keyword: keyword || undefined,
          page: 1,
          limit: PAGE_LIMIT,
        }),
      ]);
      setSelectedDetail(detail);
      setSegments(segmentRows);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "文档详情加载失败");
      setSelectedDetail(null);
      setSegments([]);
    } finally {
      setIsLoadingDetail(false);
    }
  };

  useEffect(() => {
    void loadDatasets();
  }, []);

  useEffect(() => {
    if (!selectedDatasetId) {
      return;
    }
    setGlobalHits([]);
    void loadDocuments(docKeyword.trim(), selectedDatasetId);
  }, [selectedDatasetId]);

  useEffect(() => {
    if (!selectedDocumentId || !selectedDatasetId) {
      return;
    }
    void loadDocumentDetailAndSegments(selectedDocumentId, selectedDatasetId, segmentKeyword);
  }, [selectedDocumentId, selectedDatasetId]);

  const handleSearchDocuments = () => {
    if (!selectedDatasetId) {
      return;
    }
    void loadDocuments(docKeyword.trim(), selectedDatasetId);
  };

  const handleSearchSegmentsInDocument = () => {
    if (!selectedDocumentId || !selectedDatasetId) {
      return;
    }
    void loadDocumentDetailAndSegments(selectedDocumentId, selectedDatasetId, segmentKeyword.trim());
  };

  const handleSearchGlobalSegments = async () => {
    const query = globalQuery.trim();
    if (!query) {
      setGlobalHits([]);
      return;
    }
    if (!selectedDatasetId) {
      setError("请先选择知识库");
      return;
    }
    setIsSearchingGlobal(true);
    setError("");
    try {
      const rows = await apiClient.searchKnowledgeSegments(query, 10, selectedDatasetId);
      setGlobalHits(rows);
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "全库片段检索失败");
      setGlobalHits([]);
    } finally {
      setIsSearchingGlobal(false);
    }
  };

  return (
    <div className="page-grid">
      <SectionHeader
        eyebrow="知识"
        title="知识库双面板"
        description="左侧选择知识库并检索文档，右侧查看文档详情、片段与全库检索结果。"
      />

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "minmax(320px, 1fr) minmax(420px, 1.4fr)" }}>
        <SurfaceCard
          title="知识库与文档"
          description="先选择 dataset，再按关键词检索文档。"
          actions={<StatusChip tone={isLoadingDocs ? "warning" : "success"}>{isLoadingDocs ? "加载中" : `${documents.length} 篇`}</StatusChip>}
        >
          <div className="page-stack">
            <div className="status-row">
              <StatusChip tone="accent">{selectedDataset?.name ?? "未命名知识库"}</StatusChip>
              <StatusChip tone="neutral">{selectedDataset?.id ?? "dataset-local"}</StatusChip>
              <StatusChip tone="neutral">{selectedDataset?.document_count ?? documents.length} 文档</StatusChip>
            </div>

            <div className="mini-card-list">
              {datasets.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className="mini-card"
                  style={{ textAlign: "left", border: selectedDatasetId === item.id ? "1px solid var(--accent-strong)" : undefined }}
                  onClick={() => setSelectedDatasetId(item.id)}
                >
                  <div className="status-row">
                    <StatusChip tone="accent">{item.name || item.id}</StatusChip>
                    <StatusChip tone="neutral">{item.document_count ?? 0} 文档</StatusChip>
                  </div>
                  <p className="mini-card__copy">{item.description || "暂无描述"}</p>
                </button>
              ))}
              {datasets.length === 0 ? <p className="data-list__copy">暂无可用知识库。</p> : null}
            </div>

            <div className="input-row">
              <AppInput
                value={docKeyword}
                onChange={setDocKeyword}
                placeholder="按文档名、摘要或标签检索"
                prefix={<AppIcon name="search" size={16} />}
              />
              <AppButton variant="primary" onClick={handleSearchDocuments}>
                检索文档
              </AppButton>
            </div>

            {documents.length === 0 ? <p className="data-list__copy">暂无文档。</p> : null}
            <div className="mini-card-list">
              {documents.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className="mini-card"
                  style={{ textAlign: "left", border: selectedDocumentId === item.id ? "1px solid var(--accent-strong)" : undefined }}
                  onClick={() => setSelectedDocumentId(item.id)}
                >
                  <div className="status-row">
                    <StatusChip tone="neutral">{formatKnowledgeCategory(item.category)}</StatusChip>
                    {item.score ? <StatusChip tone="success">{Math.round(item.score * 100)}%</StatusChip> : null}
                  </div>
                  <p className="mini-card__title">{item.title}</p>
                  <p className="mini-card__copy">{item.excerpt || "暂无摘要"}</p>
                  <p className="data-list__copy">{item.source || item.id}</p>
                </button>
              ))}
            </div>
          </div>
        </SurfaceCard>

        <SurfaceCard
          title="文档详情与片段"
          description="支持文档内片段过滤与当前知识库全库片段检索。"
          actions={<StatusChip tone={isLoadingDetail ? "warning" : "neutral"}>{isLoadingDetail ? "同步中" : "已就绪"}</StatusChip>}
        >
          <div className="page-stack">
            {selectedDocument ? (
              <div className="state-block">
                <p className="mini-card__title">{selectedDocument.title}</p>
                <p className="mini-card__copy">{selectedDocument.excerpt || "暂无文档摘要"}</p>
                <div className="status-row">
                  <StatusChip tone="neutral">{selectedDocument.id}</StatusChip>
                  <StatusChip tone="neutral">{formatKnowledgeCategory(selectedDocument.category)}</StatusChip>
                </div>
              </div>
            ) : (
              <p className="data-list__copy">请先在左侧选择文档。</p>
            )}

            <div className="input-row">
              <AppInput
                value={segmentKeyword}
                onChange={setSegmentKeyword}
                placeholder="文档内关键词过滤片段"
                prefix={<AppIcon name="search" size={16} />}
              />
              <AppButton variant="secondary" onClick={handleSearchSegmentsInDocument}>
                检索片段
              </AppButton>
            </div>

            <div className="mini-card-list">
              {segments.map((item) => (
                <div key={item.id} className="mini-card">
                  <div className="status-row">
                    <StatusChip tone="neutral">{item.status ?? "enabled"}</StatusChip>
                    {item.score != null ? <StatusChip tone="success">{Math.round(item.score * 100)}%</StatusChip> : null}
                  </div>
                  <p className="mini-card__copy">{item.content || "(空片段)"}</p>
                  <p className="data-list__copy">segment: {item.id}</p>
                </div>
              ))}
              {selectedDocument && segments.length === 0 ? <p className="data-list__copy">当前文档暂无匹配片段。</p> : null}
            </div>

            <div className="input-row">
              <AppInput
                value={globalQuery}
                onChange={setGlobalQuery}
                placeholder="当前知识库全库片段检索"
                prefix={<AppIcon name="search" size={16} />}
              />
              <AppButton variant="primary" onClick={handleSearchGlobalSegments}>
                {isSearchingGlobal ? "检索中" : "全库检索"}
              </AppButton>
            </div>

            <div className="mini-card-list">
              {globalHits.map((item) => (
                <div key={`${item.document_id}-${item.id}`} className="mini-card">
                  <div className="status-row">
                    <StatusChip tone="neutral">doc {item.document_id || "unknown"}</StatusChip>
                    {item.score != null ? <StatusChip tone="success">{Math.round(item.score * 100)}%</StatusChip> : null}
                  </div>
                  <p className="mini-card__copy">{item.content || "(空片段)"}</p>
                </div>
              ))}
              {globalQuery.trim() && globalHits.length === 0 && !isSearchingGlobal ? (
                <p className="data-list__copy">全库检索暂无结果。</p>
              ) : null}
            </div>

            {error ? <p className="data-list__copy">{error}</p> : null}
          </div>
        </SurfaceCard>
      </div>
    </div>
  );
}

export default KnowledgePage;
