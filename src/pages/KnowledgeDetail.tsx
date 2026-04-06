import { useEffect, useMemo, useState } from "react";
import { Select } from "antd";
import { useNavigate, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { KnowledgeBaseDetail, KnowledgeBaseDocument } from "../api/types";
import { AppButton, AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import {
  DEFAULT_KNOWLEDGE_DOCUMENT_FILTERS,
  buildKnowledgeDetailSummary,
  filterKnowledgeDocuments,
  formatKnowledgeIndexStatusLabel,
  formatKnowledgeScopeLabel,
  formatKnowledgeSourceTypeLabel,
  formatKnowledgeStatusLabel,
  formatStorageSize,
  getKnowledgeIndexStatusTone,
  getKnowledgeStatusTone,
  type KnowledgeDocumentFilterState,
} from "../features/knowledge/model";
import { formatDateTime, formatDateTimeParts } from "../utils/format";

const SOURCE_TYPE_OPTIONS: Array<{ value: KnowledgeDocumentFilterState["sourceType"]; label: string }> = [
  { value: "all", label: "全部" },
  { value: "file", label: formatKnowledgeSourceTypeLabel("file") },
  { value: "manual", label: formatKnowledgeSourceTypeLabel("manual") },
  { value: "link", label: formatKnowledgeSourceTypeLabel("link") },
];

const STATUS_OPTIONS: Array<{ value: KnowledgeDocumentFilterState["status"]; label: string }> = [
  { value: "all", label: "全部" },
  { value: "enabled", label: formatKnowledgeStatusLabel("enabled") },
  { value: "disabled", label: formatKnowledgeStatusLabel("disabled") },
];

const INDEX_STATUS_OPTIONS: Array<{ value: KnowledgeDocumentFilterState["indexStatus"]; label: string }> = [
  { value: "all", label: "全部" },
  { value: "ready", label: formatKnowledgeIndexStatusLabel("ready") },
  { value: "indexing", label: formatKnowledgeIndexStatusLabel("indexing") },
  { value: "failed", label: formatKnowledgeIndexStatusLabel("failed") },
  { value: "pending", label: formatKnowledgeIndexStatusLabel("pending") },
];

function KnowledgeDetailPage() {
  const navigate = useNavigate();
  const { knowledgeBaseId } = useParams();
  const [detail, setDetail] = useState<KnowledgeBaseDetail | null>(null);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string>("");
  const [draftFilters, setDraftFilters] = useState<KnowledgeDocumentFilterState>(DEFAULT_KNOWLEDGE_DOCUMENT_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<KnowledgeDocumentFilterState>(DEFAULT_KNOWLEDGE_DOCUMENT_FILTERS);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const resolvedKnowledgeBaseId = knowledgeBaseId ? decodeURIComponent(knowledgeBaseId) : "";

  async function loadKnowledgeBaseDetail(refresh = false) {
    if (!resolvedKnowledgeBaseId) {
      setDetail(null);
      setErrorMessage("知识库标识缺失，暂时无法加载详情。");
      setIsLoading(false);
      setIsRefreshing(false);
      return;
    }

    if (refresh) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }

    setErrorMessage(null);

    try {
      const nextDetail = await apiClient.getKnowledgeBaseDetail(resolvedKnowledgeBaseId);
      setDetail(nextDetail);
      setSelectedDocumentId("");
    } catch (error) {
      setDetail(null);
      setSelectedDocumentId("");
      setErrorMessage(error instanceof Error ? error.message : "知识库详情加载失败");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }

  useEffect(() => {
    void loadKnowledgeBaseDetail();
  }, [resolvedKnowledgeBaseId]);

  const filteredDocuments = useMemo(
    () => filterKnowledgeDocuments(detail?.documents ?? [], appliedFilters),
    [appliedFilters, detail?.documents],
  );

  useEffect(() => {
    if (!selectedDocumentId) {
      return;
    }

    if (!filteredDocuments.some((item) => item.id === selectedDocumentId)) {
      setSelectedDocumentId("");
    }
  }, [filteredDocuments, selectedDocumentId]);

  const selectedDocument = useMemo(
    () => filteredDocuments.find((item) => item.id === selectedDocumentId) ?? null,
    [filteredDocuments, selectedDocumentId],
  );

  const detailSummary = useMemo(() => (detail ? buildKnowledgeDetailSummary(detail) : []), [detail]);

  function updateDraftFilter<K extends keyof KnowledgeDocumentFilterState>(key: K, value: KnowledgeDocumentFilterState[K]) {
    setDraftFilters((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function handleReset() {
    setDraftFilters(DEFAULT_KNOWLEDGE_DOCUMENT_FILTERS);
    setAppliedFilters(DEFAULT_KNOWLEDGE_DOCUMENT_FILTERS);
  }

  function renderPreviewPanel(document: KnowledgeBaseDocument | null) {
    if (!document) {
      return (
        <div className="knowledge-preview knowledge-preview--empty">
          <p className="knowledge-preview__empty-title">选择一条文档记录查看详情</p>
          <p className="knowledge-preview__empty-copy">这里会展示文档来源、索引状态、标签、摘要和结构化正文片段，帮助快速判断是否适合检索引用。</p>
        </div>
      );
    }

    const updatedAt = formatDateTime(document.preview.updated_at);

    return (
      <div className="knowledge-preview">
        <div className="knowledge-preview__header">
          <div>
            <p className="knowledge-preview__eyebrow">文档预览</p>
            <h4 className="knowledge-preview__title">{document.preview.title}</h4>
            <p className="knowledge-preview__description">{document.preview.description}</p>
          </div>
          <div className="knowledge-preview__chips">
            <StatusChip tone={getKnowledgeIndexStatusTone(document.index_status)}>{formatKnowledgeIndexStatusLabel(document.index_status)}</StatusChip>
            <StatusChip tone={getKnowledgeStatusTone(document.status)}>{formatKnowledgeStatusLabel(document.status)}</StatusChip>
          </div>
        </div>

        <div className="knowledge-preview__meta-grid">
          <div className="knowledge-preview__meta-item">
            <span className="knowledge-preview__meta-label">来源</span>
            <strong className="knowledge-preview__meta-value">{document.preview.source_label}</strong>
          </div>
          <div className="knowledge-preview__meta-item">
            <span className="knowledge-preview__meta-label">文件名</span>
            <strong className="knowledge-preview__meta-value">{document.file_name}</strong>
          </div>
          <div className="knowledge-preview__meta-item">
            <span className="knowledge-preview__meta-label">更新时间</span>
            <strong className="knowledge-preview__meta-value">{updatedAt}</strong>
          </div>
          <div className="knowledge-preview__meta-item">
            <span className="knowledge-preview__meta-label">文件大小</span>
            <strong className="knowledge-preview__meta-value">{formatStorageSize(document.size_bytes)}</strong>
          </div>
        </div>

        <div className="knowledge-preview__tag-list">
          {document.preview.tags.map((tag) => (
            <StatusChip key={tag} tone="neutral">
              {tag}
            </StatusChip>
          ))}
        </div>

        {document.preview.warning ? <div className="knowledge-preview__warning">{document.preview.warning}</div> : null}

        <div className="knowledge-preview__sections">
          {document.preview.sections.map((section) => (
            <section key={section.id} className="knowledge-preview__section">
              <h5 className="knowledge-preview__section-title">{section.heading}</h5>
              <p className="knowledge-preview__section-copy">{section.body}</p>
            </section>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="page-grid knowledge-detail-page">
      <div className="page-intro">
        <SectionHeader
          title="知识库详情"
          description="查看知识库元信息、文档清单和统一只读预览，帮助快速判断当前文档是否适合被检索与引用。"
          actions={
            <div className="knowledge-detail-page__header-actions">
              <AppButton iconLeft="arrowLeft" onClick={() => navigate("/knowledge")} variant="tertiary">
                返回知识库
              </AppButton>
              <AppButton iconLeft="refresh" loading={isRefreshing} onClick={() => void loadKnowledgeBaseDetail(true)} variant="secondary">
                刷新详情
              </AppButton>
            </div>
          }
        />
      </div>

      {errorMessage ? (
        <SurfaceCard
          bodyClassName="skills-empty"
          actions={
            <AppButton iconLeft="arrowLeft" onClick={() => navigate("/knowledge")} variant="secondary">
              返回列表
            </AppButton>
          }
          description={errorMessage}
          title="知识库详情暂时不可用"
          variant="soft"
        >
          <p className="skills-empty__description">可以返回知识库列表重新选择，或稍后刷新后再试。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && isLoading ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">正在加载知识库详情</p>
          <p className="skills-empty__description">正在同步知识库元信息和文档清单，请稍候。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && detail ? (
        <>
          <SurfaceCard bodyClassName="knowledge-detail-hero" variant="hero">
            <div className="knowledge-detail-hero__back-row">
              <button type="button" className="knowledge-detail-hero__back-link" onClick={() => navigate("/knowledge")}>
                <AppIcon name="arrowLeft" size={16} />
                <span>知识库详情</span>
              </button>
            </div>
            <div className="knowledge-detail-hero__headline">
              <div>
                <h2 className="knowledge-detail-hero__title">{detail.name}</h2>
                <p className="knowledge-detail-hero__description">{detail.description}</p>
              </div>
              <div className="knowledge-detail-hero__chips">
                <StatusChip tone={getKnowledgeStatusTone(detail.status)}>{formatKnowledgeStatusLabel(detail.status)}</StatusChip>
                <StatusChip tone={getKnowledgeIndexStatusTone(detail.index_status)}>{formatKnowledgeIndexStatusLabel(detail.index_status)}</StatusChip>
                <StatusChip tone="neutral">{formatKnowledgeScopeLabel(detail.scope)}</StatusChip>
              </div>
            </div>
          </SurfaceCard>

          <div className="knowledge-detail-summary-grid">
            {detailSummary.map((item) => {
              const value = item.label.includes("时间") ? formatDateTime(item.value) : item.value;
              return (
                <article key={item.label} className="knowledge-detail-summary-card">
                  <p className="knowledge-detail-summary-card__label">{item.label}</p>
                  <p className="knowledge-detail-summary-card__value">{value}</p>
                  <p className="knowledge-detail-summary-card__hint">{item.hint}</p>
                </article>
              );
            })}
          </div>

          <div className="knowledge-detail-workspace">
            <SurfaceCard className="knowledge-detail-workspace__table-card" description="按来源类型、启用状态和索引状态过滤当前知识库中的文档。" title={`当前知识库下共 ${detail.document_count} 条文档`}>
              <div className="knowledge-toolbar knowledge-toolbar--detail">
                <label className="knowledge-toolbar__field">
                  <span className="knowledge-toolbar__label">来源类型</span>
                  <Select
                    className="app-select knowledge-toolbar__select"
                    onChange={(value) => updateDraftFilter("sourceType", value)}
                    options={SOURCE_TYPE_OPTIONS}
                    value={draftFilters.sourceType}
                  />
                </label>
                <label className="knowledge-toolbar__field">
                  <span className="knowledge-toolbar__label">启用状态</span>
                  <Select
                    className="app-select knowledge-toolbar__select"
                    onChange={(value) => updateDraftFilter("status", value)}
                    options={STATUS_OPTIONS}
                    value={draftFilters.status}
                  />
                </label>
                <label className="knowledge-toolbar__field">
                  <span className="knowledge-toolbar__label">索引状态</span>
                  <Select
                    className="app-select knowledge-toolbar__select"
                    onChange={(value) => updateDraftFilter("indexStatus", value)}
                    options={INDEX_STATUS_OPTIONS}
                    value={draftFilters.indexStatus}
                  />
                </label>
                <label className="knowledge-toolbar__search-field">
                  <span className="knowledge-toolbar__label">关键词</span>
                  <AppInput
                    onChange={(value) => updateDraftFilter("query", value)}
                    placeholder="搜索文档标题、文件名或来源地址"
                    prefix={<AppIcon name="search" size={16} />}
                    value={draftFilters.query}
                  />
                </label>
                <div className="knowledge-toolbar__actions">
                  <AppButton onClick={handleReset} variant="secondary">
                    重置
                  </AppButton>
                  <AppButton onClick={() => setAppliedFilters(draftFilters)} variant="primary">
                    查询
                  </AppButton>
                </div>
              </div>

              {filteredDocuments.length === 0 ? (
                <div className="knowledge-page__state">
                  <p className="skills-empty__title">当前没有匹配的文档</p>
                  <p className="skills-empty__description">可以调整筛选条件，或切换回知识库列表查看其他知识库。</p>
                </div>
              ) : (
                <div className="knowledge-document-table-shell">
                  <table className="knowledge-document-table">
                    <colgroup>
                      <col className="knowledge-document-table__column knowledge-document-table__column--title" />
                      <col className="knowledge-document-table__column knowledge-document-table__column--source" />
                      <col className="knowledge-document-table__column knowledge-document-table__column--file" />
                      <col className="knowledge-document-table__column knowledge-document-table__column--size" />
                      <col className="knowledge-document-table__column knowledge-document-table__column--index" />
                      <col className="knowledge-document-table__column knowledge-document-table__column--status" />
                    </colgroup>
                    <thead>
                      <tr>
                        <th>文档标题</th>
                        <th>来源类型</th>
                        <th>文件名</th>
                        <th>文件大小</th>
                        <th>索引状态</th>
                        <th>启用状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredDocuments.map((item) => {
                        const updatedAt = formatDateTimeParts(item.updated_at);
                        const isSelected = item.id === selectedDocumentId;

                        return (
                          <tr
                            key={item.id}
                            className={`knowledge-document-table__row${isSelected ? " knowledge-document-table__row--active" : ""}`}
                            onClick={() => setSelectedDocumentId(item.id)}
                          >
                            <td>
                              <div className="knowledge-document-table__title-block">
                                <strong className="knowledge-document-table__title">{item.title}</strong>
                                <span className="knowledge-document-table__summary">{item.preview_summary}</span>
                                <span className="knowledge-document-table__updated">最近更新：{updatedAt.date} {updatedAt.time || ""}</span>
                              </div>
                            </td>
                            <td>{formatKnowledgeSourceTypeLabel(item.source_type)}</td>
                            <td>{item.file_name}</td>
                            <td>{formatStorageSize(item.size_bytes)}</td>
                            <td>
                              <StatusChip tone={getKnowledgeIndexStatusTone(item.index_status)}>{formatKnowledgeIndexStatusLabel(item.index_status)}</StatusChip>
                            </td>
                            <td>
                              <StatusChip tone={getKnowledgeStatusTone(item.status)}>{formatKnowledgeStatusLabel(item.status)}</StatusChip>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </SurfaceCard>

            <SurfaceCard className="knowledge-detail-workspace__preview-card" description="统一只读预览当前选中文档的来源、标签、说明和正文片段。" title="文档预览">
              {renderPreviewPanel(selectedDocument)}
            </SurfaceCard>
          </div>
        </>
      ) : null}
    </div>
  );
}

export default KnowledgeDetailPage;
