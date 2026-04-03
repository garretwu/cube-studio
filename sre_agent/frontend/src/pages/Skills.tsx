import { useEffect, useMemo, useState } from "react";
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

type SkillFilter = "all" | "published" | "draft" | "builtin" | "custom";

const FILTER_LABELS: Record<SkillFilter, string> = {
  all: "全部",
  builtin: "内置",
  custom: "自定义",
  draft: "草稿",
  published: "已发布",
};

function matchesSkillFilter(skill: SkillViewModel, filter: SkillFilter): boolean {
  if (filter === "all") return true;
  if (filter === "published") return skill.lifecycle_status === "published";
  if (filter === "draft") return skill.lifecycle_status === "draft";
  if (filter === "builtin") return skill.scope === "builtin";
  return skill.scope === "custom";
}

function SkillsPage() {
  const navigate = useNavigate();
  const [skills, setSkills] = useState<SkillViewModel[]>([]);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<SkillFilter>("all");
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
  const filteredSkills = useMemo(
    () =>
      skills.filter((skill) => {
        const haystack = `${skill.name} ${skill.id} ${skill.summary}`.toLowerCase();
        return (!normalizedQuery || haystack.includes(normalizedQuery)) && matchesSkillFilter(skill, statusFilter);
      }),
    [normalizedQuery, skills, statusFilter],
  );

  const publishedCount = skills.filter((skill) => skill.lifecycle_status === "published").length;
  const customCount = skills.filter((skill) => skill.scope === "custom").length;

  return (
    <div className="page-grid skills-manage-page">
      <div className="page-intro">
        <SectionHeader
          title="技能管理"
          description="按统一视图查看技能元数据、生命周期和可用状态，快速筛选并进入详情。"
          actions={
            <AppButton iconLeft="refresh" loading={isRefreshing} onClick={() => void loadSkills(true)} variant="secondary">
              刷新数据
            </AppButton>
          }
        />
      </div>

      <div className="card-grid--metrics skills-manage-metrics">
        <MetricTile hint="已同步到平台的技能总量" label="技能总数" value={skills.length} />
        <MetricTile hint="当前可直接使用的技能" label="已发布" value={publishedCount} />
        <MetricTile hint="团队维护的自定义技能" label="自定义" value={customCount} />
      </div>

      <SurfaceCard className="skills-manage-workbench" description="按生命周期、范围和关键词快速定位技能。" title="筛选条件" variant="panel">
        <div className="skills-manage-workbench__filters">
          <AppInput
            className="skills-manage-toolbar__search"
            onChange={setQuery}
            placeholder="搜索技能名称、ID 或描述"
            prefix={<AppIcon name="search" size={16} />}
            value={query}
          />
          <div className="remediation-filter-row">
            {(Object.entries(FILTER_LABELS) as Array<[SkillFilter, string]>).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`remediation-filter-chip${statusFilter === value ? " remediation-filter-chip--active" : ""}`}
                onClick={() => setStatusFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {errorMessage ? (
          <div className="skills-manage-workbench__state">
            <p className="skills-empty__title">技能列表暂时不可用</p>
            <p className="skills-empty__description">{errorMessage}</p>
          </div>
        ) : null}

        {!errorMessage && isLoading ? (
          <div className="skills-manage-workbench__state">
            <p className="skills-empty__title">技能列表加载中</p>
            <p className="skills-empty__description">正在同步最新技能元数据，请稍候。</p>
          </div>
        ) : null}

        {!errorMessage && !isLoading && filteredSkills.length === 0 ? (
          <div className="skills-manage-workbench__state">
            <p className="skills-empty__title">没有匹配的技能</p>
            <p className="skills-empty__description">可以尝试更换关键词，或清空筛选条件后重新查看。</p>
          </div>
        ) : null}

        {!errorMessage && !isLoading && filteredSkills.length > 0 ? (
          <div className="skills-manage-table-shell">
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
                          <StatusChip tone={getSkillLifecycleTone(skill.lifecycle_status)}>{formatSkillLifecycleLabel(skill.lifecycle_status)}</StatusChip>
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
          </div>
        ) : null}
      </SurfaceCard>
    </div>
  );
}

export default SkillsPage;
