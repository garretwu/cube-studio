import { useEffect, useState } from "react";

import { apiClient } from "../api/client";
import type { KnowledgeDocument } from "../api/types";
import { AppButton, AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { formatKnowledgeCategory } from "../utils/display";

function KnowledgePage() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [query, setQuery] = useState("RoCEv2 ECN 配置");

  useEffect(() => {
    void apiClient.getKnowledgeSources().then(setDocuments);
  }, []);

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
            <AppButton onClick={() => void apiClient.searchKnowledge(query).then(setDocuments)} variant="primary">
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
