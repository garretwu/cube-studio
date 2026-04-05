import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { AppButton, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import {
  formatSkillLifecycleLabel,
  formatSkillScopeLabel,
  getSkillLifecycleTone,
  getSkillScopeTone,
  getSkillStatusMeta,
  normalizeSkill,
  type SkillViewModel,
} from "../features/skills/model";
import { formatDateTimeParts } from "../utils/format";

function SkillDetailPage() {
  const navigate = useNavigate();
  const { skillId } = useParams();
  const [skill, setSkill] = useState<SkillViewModel | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const resolvedSkillId = skillId ? decodeURIComponent(skillId) : "";

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
      const nextSkill = await apiClient.getSkill(resolvedSkillId);
      setSkill(normalizeSkill(nextSkill));
    } catch (error) {
      setSkill(null);
      setErrorMessage(error instanceof Error ? error.message : "技能详情加载失败。");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }

  useEffect(() => {
    void loadSkill();
  }, [resolvedSkillId]);

  const markdownLines = useMemo(() => {
    if (!skill) {
      return [] as string[];
    }
    return skill.markdown_content.split(/\r?\n/);
  }, [skill]);

  const updatedAt = formatDateTimeParts(skill?.updated_at);
  const updatedAtLabel = [updatedAt.date, updatedAt.time].filter(Boolean).join(" ") || "--";
  const statusMeta = skill ? getSkillStatusMeta(skill) : null;

  return (
    <div className="page-grid skill-file-page">
      <div className="page-intro">
        <SectionHeader
          actions={
            <AppButton iconLeft="arrowLeft" onClick={() => navigate("/skills")} variant="tertiary">
              返回技能列表
            </AppButton>
          }
          title="技能详情"
          description="查看技能定义与 SKILL.md 只读预览。"
        />
      </div>

      {errorMessage ? (
        <SurfaceCard
          actions={
            <AppButton loading={isRefreshing} onClick={() => void loadSkill(true)} variant="secondary">
              重新加载
            </AppButton>
          }
          bodyClassName="skills-empty"
          description={errorMessage}
          title="技能详情暂时不可用"
          variant="soft"
        >
          <p className="skills-empty__description">可以返回技能列表重新选择，或稍后再试。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && isLoading ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">正在加载技能详情</p>
          <p className="skills-empty__description">请稍候，正在同步当前技能的元数据与文档内容。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && skill ? (
        <SurfaceCard bodyClassName="skill-detail-canvas" className="skill-detail-canvas-card" variant="panel">
          <section className="skill-detail-overview">
            <div className="skill-detail-overview__chips-row">
              <StatusChip tone={getSkillLifecycleTone(skill.lifecycle_status)}>{formatSkillLifecycleLabel(skill.lifecycle_status)}</StatusChip>
              <StatusChip tone={getSkillScopeTone(skill.scope)}>{formatSkillScopeLabel(skill.scope)}</StatusChip>
              {statusMeta ? <StatusChip tone={statusMeta.tone}>{statusMeta.label}</StatusChip> : null}
            </div>

            <div className="skill-detail-field-grid skill-detail-field-grid--compact">
              <div className="skill-detail-field">
                <span className="skill-detail-field__label">名称</span>
                <strong className="skill-detail-field__value">{skill.name}</strong>
              </div>
              <div className="skill-detail-field">
                <span className="skill-detail-field__label">Skill ID</span>
                <strong className="skill-detail-field__value">{skill.id}</strong>
              </div>
              <div className="skill-detail-field">
                <span className="skill-detail-field__label">文件名</span>
                <strong className="skill-detail-field__value">{skill.file_name}</strong>
              </div>
              <div className="skill-detail-field">
                <span className="skill-detail-field__label">更新时间</span>
                <strong className="skill-detail-field__value">{updatedAtLabel}</strong>
              </div>
              <div className="skill-detail-field skill-detail-field--full">
                <span className="skill-detail-field__label">描述</span>
                <p className="skill-detail-field__text">{skill.summary}</p>
              </div>
              <div className="skill-detail-field skill-detail-field--full">
                <span className="skill-detail-field__label">权限</span>
                <div className="skill-detail-permissions__list">
                  {skill.permissions.length > 0 ? (
                    skill.permissions.map((permission) => (
                      <StatusChip key={permission} tone="neutral">
                        {permission}
                      </StatusChip>
                    ))
                  ) : (
                    <span className="skill-detail-permissions__empty">未声明权限</span>
                  )}
                </div>
              </div>
            </div>
          </section>

          <section className="skill-detail-document" aria-labelledby="skill-detail-preview">
            <div className="skill-detail-section__intro skill-detail-section__intro--document">
              <h4 className="skill-detail-section__title" id="skill-detail-preview">
                文档预览
              </h4>
              <span className="skill-markdown-editor__status">只读</span>
            </div>

            <div className="skill-markdown-editor" aria-label="SKILL.md 只读预览">
              <div className="skill-markdown-editor__toolbar">
                <span className="skill-markdown-editor__tab">{skill.file_name}</span>
              </div>
              <div className="skill-markdown-viewer" role="presentation">
                {markdownLines.map((line, index) => (
                  <div key={`${skill.id}-${index + 1}`} className="skill-markdown-viewer__row">
                    <span className="skill-markdown-viewer__line-number">{index + 1}</span>
                    <code className="skill-markdown-viewer__line-content">{line || " "}</code>
                  </div>
                ))}
              </div>
            </div>
          </section>
        </SurfaceCard>
      ) : null}
    </div>
  );
}

export default SkillDetailPage;