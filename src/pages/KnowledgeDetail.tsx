import { useEffect, useMemo, useState } from "react";
import { Select } from "antd";
import { useNavigate, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { KnowledgeBaseDetail, KnowledgeBaseDocument } from "../api/types";
import { AppButton, AppIcon, AppInput, StatusChip, SurfaceCard } from "../components/ui";
import {
  DEFAULT_KNOWLEDGE_DOCUMENT_FILTERS,
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
  { value: "all", label: "\u5168\u90e8" },
  { value: "file", label: formatKnowledgeSourceTypeLabel("file") },
  { value: "manual", label: formatKnowledgeSourceTypeLabel("manual") },
  { value: "link", label: formatKnowledgeSourceTypeLabel("link") },
];

const STATUS_OPTIONS: Array<{ value: KnowledgeDocumentFilterState["status"]; label: string }> = [
  { value: "all", label: "\u5168\u90e8" },
  { value: "enabled", label: formatKnowledgeStatusLabel("enabled") },
  { value: "disabled", label: formatKnowledgeStatusLabel("disabled") },
];

const INDEX_STATUS_OPTIONS: Array<{ value: KnowledgeDocumentFilterState["indexStatus"]; label: string }> = [
  { value: "all", label: "\u5168\u90e8" },
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
      setErrorMessage("\u77e5\u8bc6\u5e93\u6807\u8bc6\u7f3a\u5931\uff0c\u6682\u65f6\u65e0\u6cd5\u52a0\u8f7d\u8be6\u60c5\u3002");
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
      setErrorMessage(error instanceof Error ? error.message : "\u77e5\u8bc6\u5e93\u8be6\u60c5\u52a0\u8f7d\u5931\u8d25");
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

  const detailMetrics = useMemo(
    () =>
      detail
        ? [
            { label: "\u6587\u6863\u6570\u91cf", value: String(detail.document_count), hint: "\u5f53\u524d\u77e5\u8bc6\u5e93\u6536\u5f55\u6587\u6863" },
            { label: "\u5b58\u50a8\u5360\u7528", value: formatStorageSize(detail.storage_bytes), hint: "\u6309\u5f53\u524d\u77e5\u8bc6\u5e93\u7ef4\u5ea6\u7edf\u8ba1" },
            {
              label: "\u6700\u8fd1\u7d22\u5f15\u65f6\u95f4",
              value: formatDateTime(detail.indexed_at),
              hint: detail.indexed_at ? "\u6700\u8fd1\u4e00\u6b21\u7d22\u5f15\u5b8c\u6210\u65f6\u95f4" : "\u5f53\u524d\u6682\u65e0\u7d22\u5f15\u8bb0\u5f55",
            },
            { label: "\u66f4\u65b0\u65f6\u95f4", value: formatDateTime(detail.updated_at), hint: "\u6700\u8fd1\u4e00\u6b21\u8d44\u6599\u53d8\u66f4\u65f6\u95f4" },
          ]
        : [],
    [detail],
  );

  const detailMeta = useMemo(
    () =>
      detail
        ? [
            { label: "\u77e5\u8bc6\u5e93\u7f16\u7801", value: detail.code },
            { label: "\u8303\u56f4", value: formatKnowledgeScopeLabel(detail.scope) },
            { label: "\u521b\u5efa\u65f6\u95f4", value: formatDateTime(detail.created_at) },
            { label: "\u77e5\u8bc6\u5e93 ID", value: detail.knowledge_base_id },
          ]
        : [],
    [detail],
  );

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
          <p className="knowledge-preview__empty-title">{"\u9009\u62e9\u4e00\u6761\u6587\u6863\u8bb0\u5f55\u67e5\u770b\u8be6\u60c5"}</p>
          <p className="knowledge-preview__empty-copy">{"\u8fd9\u91cc\u4f1a\u5c55\u793a\u6587\u6863\u6765\u6e90\u3001\u7d22\u5f15\u72b6\u6001\u3001\u6807\u7b7e\u3001\u6458\u8981\u548c\u7ed3\u6784\u5316\u6b63\u6587\u7247\u6bb5\uff0c\u5e2e\u52a9\u5feb\u901f\u5224\u65ad\u662f\u5426\u9002\u5408\u68c0\u7d22\u5f15\u7528\u3002"}</p>
        </div>
      );
    }

    const updatedAt = formatDateTime(document.preview.updated_at);

    return (
      <div className="knowledge-preview">
        <div className="knowledge-preview__header">
          <div>
            <p className="knowledge-preview__eyebrow">{"\u6587\u6863\u9884\u89c8"}</p>
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
            <span className="knowledge-preview__meta-label">{"\u6765\u6e90"}</span>
            <strong className="knowledge-preview__meta-value">{document.preview.source_label}</strong>
          </div>
          <div className="knowledge-preview__meta-item">
            <span className="knowledge-preview__meta-label">{"\u6587\u4ef6\u540d"}</span>
            <strong className="knowledge-preview__meta-value">{document.file_name}</strong>
          </div>
          <div className="knowledge-preview__meta-item">
            <span className="knowledge-preview__meta-label">{"\u66f4\u65b0\u65f6\u95f4"}</span>
            <strong className="knowledge-preview__meta-value">{updatedAt}</strong>
          </div>
          <div className="knowledge-preview__meta-item">
            <span className="knowledge-preview__meta-label">{"\u6587\u4ef6\u5927\u5c0f"}</span>
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
      {errorMessage ? (
        <SurfaceCard
          bodyClassName="skills-empty"
          actions={
            <AppButton iconLeft="arrowLeft" onClick={() => navigate("/knowledge")} variant="secondary">
              {"\u8fd4\u56de\u5217\u8868"}
            </AppButton>
          }
          description={errorMessage}
          title={"\u77e5\u8bc6\u5e93\u8be6\u60c5\u6682\u65f6\u4e0d\u53ef\u7528"}
          variant="soft"
        >
          <p className="skills-empty__description">{"\u53ef\u4ee5\u8fd4\u56de\u77e5\u8bc6\u5e93\u5217\u8868\u91cd\u65b0\u9009\u62e9\uff0c\u6216\u7a0d\u540e\u5237\u65b0\u540e\u518d\u8bd5\u3002"}</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && isLoading ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">{"\u6b63\u5728\u52a0\u8f7d\u77e5\u8bc6\u5e93\u8be6\u60c5"}</p>
          <p className="skills-empty__description">{"\u6b63\u5728\u540c\u6b65\u77e5\u8bc6\u5e93\u5143\u4fe1\u606f\u548c\u6587\u6863\u6e05\u5355\uff0c\u8bf7\u7a0d\u5019\u3002"}</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && detail ? (
        <>
          <div className="knowledge-detail-page__intro">
            <div className="knowledge-detail-page__intro-head">
              <div className="knowledge-detail-page__intro-copy">
                <button type="button" className="knowledge-detail-page__back-link" onClick={() => navigate("/knowledge")}>
                  <AppIcon name="arrowLeft" size={16} />
                  <span>{"\u8fd4\u56de\u77e5\u8bc6\u5e93"}</span>
                </button>
                <p className="knowledge-detail-page__eyebrow">{"\u77e5\u8bc6\u5e93\u8be6\u60c5"}</p>
                <div className="knowledge-detail-page__title-row">
                  <h1 className="knowledge-detail-page__title">{detail.name}</h1>
                  <div className="knowledge-detail-page__chips">
                    <StatusChip tone={getKnowledgeStatusTone(detail.status)}>{formatKnowledgeStatusLabel(detail.status)}</StatusChip>
                    <StatusChip tone={getKnowledgeIndexStatusTone(detail.index_status)}>{formatKnowledgeIndexStatusLabel(detail.index_status)}</StatusChip>
                    <StatusChip tone="neutral">{formatKnowledgeScopeLabel(detail.scope)}</StatusChip>
                  </div>
                </div>
                <p className="knowledge-detail-page__description">{detail.description}</p>
              </div>
              <div className="knowledge-detail-page__header-actions">
                <AppButton iconLeft="refresh" loading={isRefreshing} onClick={() => void loadKnowledgeBaseDetail(true)} variant="secondary">
                  {"\u5237\u65b0\u8be6\u60c5"}
                </AppButton>
              </div>
            </div>

            <div className="knowledge-detail-page__meta-bar">
              {detailMeta.map((item) => (
                <div key={item.label} className="knowledge-detail-page__meta-item">
                  <p className="knowledge-detail-summary-card__label">{item.label}</p>
                  <p className="knowledge-detail-page__meta-value">{item.value}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="knowledge-detail-metric-grid">
            {detailMetrics.map((item) => (
              <article key={item.label} className="knowledge-detail-metric-card">
                <p className="knowledge-detail-summary-card__label">{item.label}</p>
                <p className="knowledge-detail-metric-card__value">{item.value}</p>
                <p className="knowledge-detail-summary-card__hint">{item.hint}</p>
              </article>
            ))}
          </div>

          <div className="knowledge-detail-workspace">
            <SurfaceCard className="knowledge-detail-workspace__table-card" description={"\u6309\u6765\u6e90\u7c7b\u578b\u3001\u542f\u7528\u72b6\u6001\u548c\u7d22\u5f15\u72b6\u6001\u8fc7\u6ee4\u5f53\u524d\u77e5\u8bc6\u5e93\u4e2d\u7684\u6587\u6863\u3002"} title={`\u5f53\u524d\u77e5\u8bc6\u5e93\u4e0b\u5171 ${detail.document_count} \u6761\u6587\u6863`}>
              <div className="knowledge-toolbar knowledge-toolbar--detail">
                <label className="knowledge-toolbar__field">
                  <span className="knowledge-toolbar__label">{"\u6765\u6e90\u7c7b\u578b"}</span>
                  <Select
                    className="app-select knowledge-toolbar__select"
                    onChange={(value) => updateDraftFilter("sourceType", value)}
                    options={SOURCE_TYPE_OPTIONS}
                    value={draftFilters.sourceType}
                  />
                </label>
                <label className="knowledge-toolbar__field">
                  <span className="knowledge-toolbar__label">{"\u542f\u7528\u72b6\u6001"}</span>
                  <Select
                    className="app-select knowledge-toolbar__select"
                    onChange={(value) => updateDraftFilter("status", value)}
                    options={STATUS_OPTIONS}
                    value={draftFilters.status}
                  />
                </label>
                <label className="knowledge-toolbar__field">
                  <span className="knowledge-toolbar__label">{"\u7d22\u5f15\u72b6\u6001"}</span>
                  <Select
                    className="app-select knowledge-toolbar__select"
                    onChange={(value) => updateDraftFilter("indexStatus", value)}
                    options={INDEX_STATUS_OPTIONS}
                    value={draftFilters.indexStatus}
                  />
                </label>
                <label className="knowledge-toolbar__search-field">
                  <span className="knowledge-toolbar__label">{"\u5173\u952e\u8bcd"}</span>
                  <AppInput
                    onChange={(value) => updateDraftFilter("query", value)}
                    placeholder={"\u641c\u7d22\u6587\u6863\u6807\u9898\u3001\u6587\u4ef6\u540d\u6216\u6765\u6e90\u5730\u5740"}
                    prefix={<AppIcon name="search" size={16} />}
                    value={draftFilters.query}
                  />
                </label>
                <div className="knowledge-toolbar__actions">
                  <AppButton onClick={handleReset} variant="secondary">
                    {"\u91cd\u7f6e"}
                  </AppButton>
                  <AppButton onClick={() => setAppliedFilters(draftFilters)} variant="primary">
                    {"\u67e5\u8be2"}
                  </AppButton>
                </div>
              </div>

              {filteredDocuments.length === 0 ? (
                <div className="knowledge-page__state">
                  <p className="skills-empty__title">{"\u5f53\u524d\u6ca1\u6709\u5339\u914d\u7684\u6587\u6863"}</p>
                  <p className="skills-empty__description">{"\u53ef\u4ee5\u8c03\u6574\u7b5b\u9009\u6761\u4ef6\uff0c\u6216\u5207\u6362\u56de\u77e5\u8bc6\u5e93\u5217\u8868\u67e5\u770b\u5176\u4ed6\u77e5\u8bc6\u5e93\u3002"}</p>
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
                        <th>{"\u6587\u6863\u6807\u9898"}</th>
                        <th>{"\u6765\u6e90\u7c7b\u578b"}</th>
                        <th>{"\u6587\u4ef6\u540d"}</th>
                        <th>{"\u6587\u4ef6\u5927\u5c0f"}</th>
                        <th>{"\u7d22\u5f15\u72b6\u6001"}</th>
                        <th>{"\u542f\u7528\u72b6\u6001"}</th>
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
                                <span className="knowledge-document-table__updated">{"\u6700\u8fd1\u66f4\u65b0\uff1a"}{updatedAt.date} {updatedAt.time || ""}</span>
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

            <SurfaceCard className="knowledge-detail-workspace__preview-card" description={"\u7edf\u4e00\u53ea\u8bfb\u9884\u89c8\u5f53\u524d\u9009\u4e2d\u6587\u6863\u7684\u6765\u6e90\u3001\u6807\u7b7e\u3001\u8bf4\u660e\u548c\u6b63\u6587\u7247\u6bb5\u3002"} title={"\u6587\u6863\u9884\u89c8"}>
              {renderPreviewPanel(selectedDocument)}
            </SurfaceCard>
          </div>
        </>
      ) : null}
    </div>
  );
}

export default KnowledgeDetailPage;
