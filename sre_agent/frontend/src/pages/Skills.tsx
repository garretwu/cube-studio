import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import { AppButton, AppIcon, AppInput, MetricTile, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
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
          description="按统一视图查看技能元数据、生命周期和可用状态，便于快速筛选与进入详情。"
        />
      </div>

      <div className="card-grid--metrics skills-manage-metrics">
        <MetricTile label="技能总数" value={skills.length} />
        <MetricTile label="已发布" value={publishedCount} />
        <MetricTile label="自定义" value={customCount} />
      </div>

      <SurfaceCard bodyClassName="skills-manage-toolbar" variant="soft">
        <div className="skills-manage-toolbar__summary">
          <StatusChip tone="info">全局列表</StatusChip>
          <StatusChip tone="neutral">{filteredSkills.length} 项结果</StatusChip>
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
          <p className="skills-empty__title">技能列表加载中</p>
          <p className="skills-empty__description">正在同步最新技能元数据，请稍候。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && filteredSkills.length === 0 ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">没有匹配的技能</p>
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
                <th>说明</th>
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
                        aria-label={`打开技能 ${skill.name}`}
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
                          <span className="skills-manage-row__hint">当前仅展示技能说明，暂未接入执行链路。</span>
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
