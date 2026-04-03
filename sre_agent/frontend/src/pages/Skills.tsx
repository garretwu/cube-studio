import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import { AppButton, AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import {
  formatSkillLifecycleLabel,
  formatSkillScopeLabel,
  getSkillLifecycleTone,
  getSkillScopeTone,
  normalizeSkills,
  type SkillViewModel,
} from "../features/skills/model";
import { formatDateTimeParts } from "../utils/format";

function SkillsPage() {
  const navigate = useNavigate();
  const [skills, setSkills] = useState<SkillViewModel[]>([]);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function loadSkills(refresh = false) {
    if (refresh) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }

    setErrorMessage(null);

    try {
      const nextSkills = await apiClient.getSkills();
      setSkills(normalizeSkills(nextSkills));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "技能列表加载失败");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }

  useEffect(() => {
    void loadSkills();
  }, []);

  const normalizedQuery = query.trim().toLowerCase();
  const filteredSkills = skills.filter((skill) => {
    const haystack = `${skill.name} ${skill.id} ${skill.summary}`.toLowerCase();
    return !normalizedQuery || haystack.includes(normalizedQuery);
  });
  const publishedCount = skills.filter((skill) => skill.lifecycle_status === "published").length;
  const customCount = skills.filter((skill) => skill.scope === "custom").length;

  return (
    <div className="page-grid skills-manage-page">
      <div className="page-intro">
        <SectionHeader
          eyebrow="Global Skills"
          title="技能管理"
          description="查看全局技能列表，进入详情页后可直接查看每个技能对应的 SKILL.md 文件内容。"
        />

        <SurfaceCard bodyClassName="skills-manage-notice" variant="soft">
          <AppIcon name="infoCircle" size={18} />
          <p>
            请直接粘贴完整的 <strong>SKILL.md</strong> 原文。页面只保留基础元数据字段，Markdown 内容会整体写入
            <strong> SKILL.md</strong>。
          </p>
        </SurfaceCard>

        <SurfaceCard bodyClassName="skills-manage-hero" className="skills-manage-hero-shell" variant="hero">
          <div className="skills-manage-hero__copy">
            <p className="skills-manage-hero__eyebrow">Skill Registry</p>
            <h3 className="skills-manage-hero__title">统一管理技能元数据与文件内容</h3>
            <p className="skills-manage-hero__description">
              列表页聚焦名称、状态、描述和更新时间，详情页展示元数据和完整 SKILL.md 原文，方便后续接入编辑与发布流程。
            </p>
          </div>

          <div className="skills-manage-hero__stats">
            <div className="skills-manage-hero__stat">
              <span className="skills-manage-hero__stat-label">技能总数</span>
              <strong className="skills-manage-hero__stat-value">{skills.length}</strong>
            </div>
            <div className="skills-manage-hero__stat">
              <span className="skills-manage-hero__stat-label">已发布</span>
              <strong className="skills-manage-hero__stat-value">{publishedCount}</strong>
            </div>
            <div className="skills-manage-hero__stat">
              <span className="skills-manage-hero__stat-label">自定义</span>
              <strong className="skills-manage-hero__stat-value">{customCount}</strong>
            </div>
          </div>
        </SurfaceCard>
      </div>

      <SurfaceCard bodyClassName="skills-manage-toolbar" variant="soft">
        <div className="skills-manage-toolbar__summary">
          <StatusChip tone="info">全局列表</StatusChip>
          <StatusChip tone="neutral">{filteredSkills.length} 个技能</StatusChip>
        </div>

        <div className="skills-manage-toolbar__actions">
          <AppInput
            className="skills-manage-toolbar__search"
            onChange={setQuery}
            placeholder="搜索技能名称、ID 或描述"
            prefix={<AppIcon name="search" size={16} />}
            value={query}
          />
          <AppButton
            aria-label="刷新技能列表"
            iconLeft="refresh"
            loading={isRefreshing}
            onClick={() => void loadSkills(true)}
            variant="secondary"
          >
            刷新
          </AppButton>
        </div>
      </SurfaceCard>

      {errorMessage ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">技能列表暂时不可用</p>
          <p className="skills-empty__description">{errorMessage}</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && isLoading ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">正在加载技能列表</p>
          <p className="skills-empty__description">请稍候，正在同步最新技能注册信息。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && filteredSkills.length === 0 ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">没有找到匹配的技能</p>
          <p className="skills-empty__description">可以尝试更换关键词，或清空搜索条件后重新查看。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && filteredSkills.length > 0 ? (
        <SurfaceCard bodyClassName="skills-manage-table-shell" variant="panel">
          <table className="skills-manage-table">
            <thead>
              <tr>
                <th>SKILL</th>
                <th>状态</th>
                <th>描述</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filteredSkills.map((skill) => {
                const updatedAt = formatDateTimeParts(skill.updated_at);

                return (
                  <tr key={skill.id} className="skills-manage-row">
                    <td>
                      <button
                        aria-label={`进入技能详情 ${skill.name}`}
                        className="skills-manage-row__name-button"
                        onClick={() => navigate(`/skills/${encodeURIComponent(skill.id)}`)}
                        type="button"
                      >
                        <span className="skills-manage-row__name">{skill.name}</span>
                        <span className="skills-manage-row__id">{skill.id}</span>
                      </button>
                    </td>
                    <td>
                      <div className="skills-manage-row__chips">
                        <StatusChip tone={getSkillLifecycleTone(skill.lifecycle_status)}>
                          {formatSkillLifecycleLabel(skill.lifecycle_status)}
                        </StatusChip>
                        <StatusChip tone={getSkillScopeTone(skill.scope)}>{formatSkillScopeLabel(skill.scope)}</StatusChip>
                      </div>
                    </td>
                    <td>
                      <div className="skills-manage-row__description-wrap">
                        <p className="skills-manage-row__description">{skill.summary}</p>
                        {skill.status === "unavailable" ? (
                          <span className="skills-manage-row__hint">当前仅展示文件信息，未接入执行链路。</span>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      <div className="skills-manage-row__updated">
                        <span>{updatedAt.date}</span>
                        <span>{updatedAt.time || "--"}</span>
                      </div>
                    </td>
                    <td>
                      <div className="skills-manage-row__actions">
                        <AppButton
                          aria-label={`查看技能详情 ${skill.name}`}
                          iconLeft="document"
                          onClick={() => navigate(`/skills/${encodeURIComponent(skill.id)}`)}
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
        </SurfaceCard>
      ) : null}
    </div>
  );
}

export default SkillsPage;
