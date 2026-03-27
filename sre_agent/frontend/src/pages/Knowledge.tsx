import { useEffect, useState } from "react";

import { apiClient } from "../api/client";
import type { KnowledgeDocument } from "../api/types";
import { AppButton, AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { formatKnowledgeCategory } from "../utils/display";

function KnowledgePage() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [query, setQuery] = useState("RoCEv2 ECN 配置");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      setIsLoading(true);
      setError("");
      try {
        setDocuments(await apiClient.getKnowledgeSources());
      } catch (err) {
        setError(`知识文档接口暂不可用，已降级展示。${err instanceof Error ? ` (${err.message})` : ""}`);
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const runSearch = async () => {
    setIsLoading(true);
    setError("");
    try {
      setDocuments(await apiClient.searchKnowledge(query));
    } catch (err) {
      setError(`知识检索失败，已保留当前结果。${err instanceof Error ? ` (${err.message})` : ""}`);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          description="在控制台内直接检索运行手册、硬件笔记与过往运维经验，不必切换到外部知识库。"
          eyebrow="知识层"
          title="知识检索"
        />
        <SurfaceCard bodyClassName="page-stack" variant="hero">
          <div className="input-row">
            <AppInput
              onChange={setQuery}
              placeholder="搜索文档与运行手册"
              prefix={<AppIcon name="search" size={16} />}
              value={query}
            />
            <AppButton disabled={isLoading} onClick={() => void runSearch()} variant="primary">
              搜索
            </AppButton>
            <AppButton iconLeft="upload" variant="secondary">
              上传资料
            </AppButton>
          </div>
        </SurfaceCard>
      </div>

      <SurfaceCard description="当前知识库返回的相关文档与手册。" title="文档结果">
        <div className="mini-card-list">
          {error ? (
            <div className="mini-card">
              <p className="mini-card__copy">{error}</p>
            </div>
          ) : null}
          {!error && !isLoading && documents.length === 0 ? (
            <div className="mini-card">
              <p className="mini-card__copy">当前无可用知识文档，已进入降级视图。</p>
            </div>
          ) : null}
          {documents.map((item) => (
            <div key={item.id} className="mini-card">
              <div className="status-row">
                <StatusChip tone="neutral">{formatKnowledgeCategory(item.category)}</StatusChip>
                {item.score ? <StatusChip tone="success">{Math.round(item.score * 100)}%</StatusChip> : null}
              </div>
              <p className="mini-card__title">{item.title}</p>
              <p className="mini-card__copy">{item.excerpt}</p>
              <p className="data-list__copy">{item.source}</p>
            </div>
          ))}
        </div>
      </SurfaceCard>
    </div>
  );
}

export default KnowledgePage;
