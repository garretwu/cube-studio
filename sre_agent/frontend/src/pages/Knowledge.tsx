import { useEffect, useMemo, useState } from "react";
import { Select } from "antd";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import type { KnowledgeBaseSummary } from "../api/types";
import { AppButton, AppIcon, AppInput, MetricTile, StatusChip, SurfaceCard } from "../components/ui";
import {
  DEFAULT_KNOWLEDGE_BASE_FILTERS,
  buildKnowledgeMetrics,
  filterKnowledgeBases,
  formatKnowledgeIndexStatusLabel,
  formatKnowledgeScopeLabel,
  formatKnowledgeStatusLabel,
  formatStorageSize,
  getKnowledgeIndexStatusTone,
  getKnowledgeStatusTone,
  type KnowledgeBaseFilterState,
} from "../features/knowledge/model";
import { formatDateTimeParts } from "../utils/format";

const STATUS_OPTIONS: Array<{ value: KnowledgeBaseFilterState["status"]; label: string }> = [
  { value: "all", label: "全部" },
  { value: "enabled", label: formatKnowledgeStatusLabel("enabled") },
  { value: "disabled", label: formatKnowledgeStatusLabel("disabled") },
];

const INDEX_STATUS_OPTIONS: Array<{ value: KnowledgeBaseFilterState["indexStatus"]; label: string }> = [
  { value: "all", label: "全部" },
  { value: "ready", label: formatKnowledgeIndexStatusLabel("ready") },
  { value: "indexing", label: formatKnowledgeIndexStatusLabel("indexing") },
  { value: "failed", label: formatKnowledgeIndexStatusLabel("failed") },
  { value: "pending", label: formatKnowledgeIndexStatusLabel("pending") },
];

function KnowledgePage() {
  const navigate = useNavigate();
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseSummary[]>([]);
  const [draftFilters, setDraftFilters] = useState<KnowledgeBaseFilterState>(DEFAULT_KNOWLEDGE_BASE_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<KnowledgeBaseFilterState>(DEFAULT_KNOWLEDGE_BASE_FILTERS);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function loadKnowledgeBases(refresh = false) {
    if (refresh) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }

    setErrorMessage(null);

    try {
      const nextKnowledgeBases = await apiClient.getKnowledgeBases();
      setKnowledgeBases(nextKnowledgeBases);
    } catch (error) {
      setKnowledgeBases([]);
      setErrorMessage(error instanceof Error ? error.message : "知识库列表加载失败");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }

  useEffect(() => {
    void loadKnowledgeBases();
  }, []);

  const filteredKnowledgeBases = useMemo(() => filterKnowledgeBases(knowledgeBases, appliedFilters), [appliedFilters, knowledgeBases]);
  const metrics = useMemo(() => buildKnowledgeMetrics(filteredKnowledgeBases), [filteredKnowledgeBases]);

  function updateDraftFilter<K extends keyof KnowledgeBaseFilterState>(key: K, value: KnowledgeBaseFilterState[K]) {
    setDraftFilters((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function handleReset() {
    setDraftFilters(DEFAULT_KNOWLEDGE_BASE_FILTERS);
    setAppliedFilters(DEFAULT_KNOWLEDGE_BASE_FILTERS);
  }

  return (
    <div className="page-grid knowledge-page">
      <h1 className="visually-hidden">知识库</h1>
      <section className="page-stage knowledge-page__stage">
        <div className="page-stage__summary knowledge-page__summary">
          <div className="card-grid--metrics knowledge-page__metrics">
            <MetricTile hint="当前筛选结果中的知识库数量" label="知识库总数" value={metrics.baseCount} />
            <MetricTile hint="按当前筛选结果聚合统计" label="文档总数" value={metrics.documentCount} />
            <MetricTile hint="仅统计当前结果中的文档占用" label="已占用存储" value={formatStorageSize(metrics.storageBytes)} />
            <MetricTile hint="已启用且可直接参与检索的知识库数量" label="索引就绪" value={metrics.searchableCount} />
          </div>
        </div>

        <SurfaceCard
          actions={
            <AppButton iconLeft="refresh" loading={isRefreshing} onClick={() => void loadKnowledgeBases(true)} variant="secondary">
              刷新数据
            </AppButton>
          }
          className="page-stage__panel knowledge-manage-workbench"
          description="按启用状态、索引状态和关键词过滤当前列表中的知识库。"
          title="筛选条件"
          variant="panel"
        >
          <div className="page-stage__toolbar knowledge-toolbar">
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
                placeholder="搜索知识库名称、编码或描述"
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

          {errorMessage ? (
            <div className="knowledge-page__state">
              <p className="skills-empty__title">知识库列表暂时不可用</p>
              <p className="skills-empty__description">{errorMessage}</p>
            </div>
          ) : null}

          {!errorMessage && isLoading ? (
            <div className="knowledge-page__state">
              <p className="skills-empty__title">知识库列表加载中</p>
              <p className="skills-empty__description">正在同步当前知识库资产和索引状态。</p>
            </div>
          ) : null}

          {!errorMessage && !isLoading && filteredKnowledgeBases.length === 0 ? (
            <div className="knowledge-page__state">
              <p className="skills-empty__title">当前没有匹配的知识库</p>
              <p className="skills-empty__description">可以调整筛选条件后重新查询。</p>
            </div>
          ) : null}

          {!errorMessage && !isLoading && filteredKnowledgeBases.length > 0 ? (
            <div className="page-stage__table-shell knowledge-table-shell">
              <table className="knowledge-table">
                <colgroup>
                  <col className="knowledge-table__column knowledge-table__column--name" />
                  <col className="knowledge-table__column knowledge-table__column--scope" />
                  <col className="knowledge-table__column knowledge-table__column--count" />
                  <col className="knowledge-table__column knowledge-table__column--storage" />
                  <col className="knowledge-table__column knowledge-table__column--index" />
                  <col className="knowledge-table__column knowledge-table__column--status" />
                  <col className="knowledge-table__column knowledge-table__column--updated" />
                  <col className="knowledge-table__column knowledge-table__column--actions" />
                </colgroup>
                <thead>
                  <tr>
                    <th>知识库</th>
                    <th>类型</th>
                    <th>文档数</th>
                    <th>存储占用</th>
                    <th>索引状态</th>
                    <th>启用状态</th>
                    <th>更新时间</th>
                    <th className="knowledge-table__head--actions">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredKnowledgeBases.map((item) => {
                    const updatedAt = formatDateTimeParts(item.updated_at);

                    return (
                      <tr key={item.id} className="knowledge-table__row">
                        <td>
                          <div className="knowledge-table__name-block">
                            <button
                              type="button"
                              className="knowledge-table__name-button"
                              onClick={() => navigate(`/knowledge/${encodeURIComponent(item.id)}`)}
                            >
                              <span className="knowledge-table__name">{item.name}</span>
                              <span className="knowledge-table__code">{item.code}</span>
                            </button>
                            <p className="knowledge-table__description">{item.description}</p>
                          </div>
                        </td>
                        <td>
                          <StatusChip tone={item.scope === "shared" ? "accent" : "info"}>{formatKnowledgeScopeLabel(item.scope)}</StatusChip>
                        </td>
                        <td>{item.document_count}</td>
                        <td>{formatStorageSize(item.storage_bytes)}</td>
                        <td>
                          <StatusChip tone={getKnowledgeIndexStatusTone(item.index_status)}>{formatKnowledgeIndexStatusLabel(item.index_status)}</StatusChip>
                        </td>
                        <td>
                          <StatusChip tone={getKnowledgeStatusTone(item.status)}>{formatKnowledgeStatusLabel(item.status)}</StatusChip>
                        </td>
                        <td>
                          <div className="knowledge-table__updated">
                            <span>{updatedAt.date}</span>
                            <span>{updatedAt.time || "--"}</span>
                          </div>
                        </td>
                        <td>
                          <div className="knowledge-table__actions">
                            <AppButton
                              aria-label={`查看详情 ${item.name}`}
                              iconLeft="document"
                              onClick={() => navigate(`/knowledge/${encodeURIComponent(item.id)}`)}
                              variant="tertiary"
                            >
                              查看详情
                            </AppButton>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </SurfaceCard>
      </section>
    </div>
  );
}

export default KnowledgePage;