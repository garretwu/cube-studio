import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { AppButton, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import {
  formatSkillScopeLabel,
  getSkillStatusMeta,
  normalizeSkill,
  type SkillViewModel,
} from "../features/skills/model";
import { formatDateTime, formatPercent } from "../utils/format";

function SkillDetailPage() {
  const navigate = useNavigate();
  const { skillId } = useParams();
  const [skill, setSkill] = useState<SkillViewModel | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const resolvedSkillId = skillId ? decodeURIComponent(skillId) : "";
  const status = skill ? getSkillStatusMeta(skill) : null;

  async function loadSkill(refresh = false) {
    if (!resolvedSkillId) {
      setErrorMessage("技能标识缺失，暂时无法加载详情。");
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
      const allSkills = await apiClient.getSkills();
      const matched = allSkills.map(normalizeSkill).find((item) => item.id === resolvedSkillId);

      if (!matched) {
        throw new Error("未找到对应 Skill，请返回技能中心重新选择。");
      }

      setSkill(matched);
    } catch (error) {
      setSkill(null);
      setErrorMessage(error instanceof Error ? error.message : "技能详情加载失败");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }

  useEffect(() => {
    void loadSkill();
  }, [resolvedSkillId]);

  return (
    <div className="page-grid skill-detail-page">
      <div className="page-intro">
        <AppButton iconLeft="arrowLeft" onClick={() => navigate("/skills")} variant="tertiary">
          返回技能中心
        </AppButton>

        <SectionHeader
          eyebrow="Skill Detail"
          title={skill?.name ?? "技能详情"}
          description={
            skill
              ? "查看当前 Skill 的基础信息、权限范围以及当前可预览的能力。"
              : "查看技能的基础信息、权限范围以及当前可预览的能力。"
          }
        />

        {skill ? (
          <SurfaceCard bodyClassName="skill-detail-hero" className="skill-detail-hero-shell" variant="hero">
            <div className="skill-detail-hero__copy">
              <div className="skill-detail-hero__title-row">
                <div>
                  <p className="skill-detail-hero__eyebrow">Skill Snapshot</p>
                  <h3 className="skill-detail-hero__title">{skill.name}</h3>
                </div>
                {status ? <StatusChip tone={status.tone}>{status.label}</StatusChip> : null}
              </div>

              <p className="skill-detail-hero__summary">{skill.summary}</p>

              <div className="skill-detail-hero__chips">
                <StatusChip tone="accent">{formatSkillScopeLabel(skill.scope)}</StatusChip>
                <StatusChip tone="neutral">{skill.source}</StatusChip>
                <StatusChip tone="info">{`${skill.previewCapabilities.length} 项能力预览`}</StatusChip>
              </div>
            </div>

            <div className="skill-detail-hero__stats">
              <div className="skill-detail-hero__stat">
                <span className="skill-detail-hero__stat-label">命中评分</span>
                <strong className="skill-detail-hero__stat-value">{formatPercent(skill.match_score)}</strong>
              </div>
              <div className="skill-detail-hero__stat">
                <span className="skill-detail-hero__stat-label">权限数量</span>
                <strong className="skill-detail-hero__stat-value">{skill.permissions.length}</strong>
              </div>
              <div className="skill-detail-hero__stat">
                <span className="skill-detail-hero__stat-label">更新时间</span>
                <strong className="skill-detail-hero__stat-value skill-detail-hero__stat-value--time">
                  {formatDateTime(skill.updated_at)}
                </strong>
              </div>
            </div>
          </SurfaceCard>
        ) : null}
      </div>

      {errorMessage ? (
        <SurfaceCard
          actions={
            <AppButton onClick={() => void loadSkill(true)} variant="secondary">
              重新加载
            </AppButton>
          }
          bodyClassName="skills-empty"
          description={errorMessage}
          title="技能详情暂时不可用"
          variant="soft"
        >
          <p className="skills-empty__description">可以返回技能中心重新选择一个 Skill，再继续查看。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && isLoading ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">正在加载技能详情</p>
          <p className="skills-empty__description">请稍候，正在同步当前 Skill 的最新能力信息。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && skill ? (
        <>
          <div className="skill-detail-layout">
            <SurfaceCard bodyClassName="skill-detail-overview" title="基本信息" variant="panel">
              <div className="skill-detail-facts">
                <div className="skill-detail-fact">
                  <span className="skill-detail-fact__label">Skill ID</span>
                  <strong className="skill-detail-fact__value">{skill.id}</strong>
                </div>
                <div className="skill-detail-fact">
                  <span className="skill-detail-fact__label">技能类型</span>
                  <strong className="skill-detail-fact__value">{formatSkillScopeLabel(skill.scope)}</strong>
                </div>
                <div className="skill-detail-fact">
                  <span className="skill-detail-fact__label">当前状态</span>
                  <strong className="skill-detail-fact__value">{status?.label}</strong>
                </div>
                <div className="skill-detail-fact">
                  <span className="skill-detail-fact__label">更新时间</span>
                  <strong className="skill-detail-fact__value">{formatDateTime(skill.updated_at)}</strong>
                </div>
              </div>

              <div className="skill-detail-overview__stack">
                <div className="skill-detail-overview__section">
                  <span className="skill-detail-overview__label">适用场景</span>
                  <p className="skill-detail-overview__text">{skill.scenario}</p>
                </div>
                <div className="skill-detail-overview__section">
                  <span className="skill-detail-overview__label">适用对象</span>
                  <p className="skill-detail-overview__text">{skill.audience}</p>
                </div>
                <div className="skill-detail-overview__section">
                  <span className="skill-detail-overview__label">预览边界</span>
                  <p className="skill-detail-overview__text">{skill.guardrail}</p>
                </div>
              </div>
            </SurfaceCard>

            <SurfaceCard
              actions={
                <AppButton
                  iconLeft="refresh"
                  loading={isRefreshing}
                  onClick={() => void loadSkill(true)}
                  variant="secondary"
                >
                  刷新
                </AppButton>
              }
              bodyClassName="skill-detail-permissions"
              description={status?.description}
              title="权限与来源"
              variant="panel"
            >
              <div className="skill-detail-permissions__source">{skill.source}</div>
              <div className="skill-detail-permissions__chips">
                {skill.permissions.map((permission) => (
                  <StatusChip key={permission} tone="neutral">
                    {permission}
                  </StatusChip>
                ))}
              </div>
            </SurfaceCard>
          </div>

          <SurfaceCard
            bodyClassName="skill-preview-grid"
            description="以下内容用于预览当前 Skill 在工作台中能提供的核心能力。"
            title="能力预览"
            variant="panel"
          >
            {skill.previewCapabilities.map((capability) => (
              <article key={capability.title} className="skill-preview-card">
                <div className="skill-preview-card__header">
                  <p className="skill-preview-card__eyebrow">Capability</p>
                  <h4 className="skill-preview-card__title">{capability.title}</h4>
                </div>
                <p className="skill-preview-card__description">{capability.description}</p>
                <div className="skill-preview-card__meta">
                  <div className="skill-preview-card__meta-item">
                    <span className="skill-preview-card__meta-label">触发时机</span>
                    <p className="skill-preview-card__meta-value">{capability.trigger}</p>
                  </div>
                  <div className="skill-preview-card__meta-item">
                    <span className="skill-preview-card__meta-label">预览产出</span>
                    <p className="skill-preview-card__meta-value">{capability.output}</p>
                  </div>
                </div>
              </article>
            ))}
          </SurfaceCard>
        </>
      ) : null}
    </div>
  );
}

export default SkillDetailPage;
