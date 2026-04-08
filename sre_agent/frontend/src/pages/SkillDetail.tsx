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
      setErrorMessage(error instanceof Error ? error.message : "技能详情加载失败");
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
  const statusMeta = skill ? getSkillStatusMeta(skill) : null;

  return (
    <div className="page-grid skill-file-page">
      <div className="page-intro">
        <AppButton iconLeft="arrowLeft" onClick={() => navigate("/skills")} variant="tertiary">
          返回技能列表
        </AppButton>

        <SectionHeader
          title={skill?.name ?? "技能详情"}
          description="查看技能的基础元数据和 SKILL.md 文件内容。"
        />
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
          <p className="skills-empty__description">可以返回技能列表重新选择，或稍后再试。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && isLoading ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">正在加载技能详情</p>
          <p className="skills-empty__description">请稍候，正在同步当前技能的元数据与文件内容。</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && skill ? (
        <>
          <SurfaceCard
            actions={
              <AppButton iconLeft="refresh" loading={isRefreshing} onClick={() => void loadSkill(true)} variant="secondary">
                刷新
              </AppButton>
            }
            bodyClassName="skill-file-meta"
            className="skill-file-meta-shell"
            variant="hero"
          >
            <div className="skill-file-meta__main">
              <div className="skill-file-meta__chips">
                <StatusChip tone={getSkillLifecycleTone(skill.lifecycle_status)}>
                  {formatSkillLifecycleLabel(skill.lifecycle_status)}
                </StatusChip>
                <StatusChip tone={getSkillScopeTone(skill.scope)}>{formatSkillScopeLabel(skill.scope)}</StatusChip>
                {statusMeta ? <StatusChip tone={statusMeta.tone}>{statusMeta.label}</StatusChip> : null}
              </div>

              <div className="skill-file-meta__grid">
                <div className="skill-file-meta__field">
                  <span className="skill-file-meta__label">SKILL ID</span>
                  <strong className="skill-file-meta__value">{skill.id}</strong>
                </div>
                <div className="skill-file-meta__field">
                  <span className="skill-file-meta__label">名称</span>
                  <strong className="skill-file-meta__value">{skill.name}</strong>
                </div>
                <div className="skill-file-meta__field skill-file-meta__field--full">
                  <span className="skill-file-meta__label">描述</span>
                  <p className="skill-file-meta__text">{skill.summary}</p>
                </div>
                <div className="skill-file-meta__field">
                  <span className="skill-file-meta__label">文件名</span>
                  <strong className="skill-file-meta__value">{skill.file_name}</strong>
                </div>
                <div className="skill-file-meta__field">
                  <span className="skill-file-meta__label">来源</span>
                  <strong className="skill-file-meta__value">{skill.source}</strong>
                </div>
              </div>

              <div className="skill-file-meta__permissions">
                <span className="skill-file-meta__label">权限</span>
                <div className="skill-file-meta__permission-list">
                  {skill.permissions.length > 0 ? (
                    skill.permissions.map((permission) => (
                      <StatusChip key={permission} tone="neutral">
                        {permission}
                      </StatusChip>
                    ))
                  ) : (
                    <span className="skill-file-meta__permission-empty">未声明权限</span>
                  )}
                </div>
              </div>
            </div>

            <aside className="skill-file-meta__aside">
              <div className="skill-file-meta__stat">
                <span className="skill-file-meta__stat-label">更新时间</span>
                <strong className="skill-file-meta__stat-value">{updatedAt.date}</strong>
                <span className="skill-file-meta__stat-note">{updatedAt.time || "--"}</span>
              </div>
              <div className="skill-file-meta__stat">
                <span className="skill-file-meta__stat-label">权限数量</span>
                <strong className="skill-file-meta__stat-value">{skill.permissions.length}</strong>
                <span className="skill-file-meta__stat-note">当前技能声明的访问范围</span>
              </div>
              <div className="skill-file-meta__stat">
                <span className="skill-file-meta__stat-label">文件状态</span>
                <strong className="skill-file-meta__stat-value">{formatSkillLifecycleLabel(skill.lifecycle_status)}</strong>
                <span className="skill-file-meta__stat-note">{statusMeta?.description ?? "技能文件已加载完成"}</span>
              </div>
            </aside>
          </SurfaceCard>

          <SurfaceCard bodyClassName="skills-manage-notice" variant="soft">
            <p>请将完整的 SKILL.md 内容粘贴到下方编辑器中，按需修改标题、描述和内容后，再进行保存或发布。</p>
          </SurfaceCard>

          <SurfaceCard
            actions={<StatusChip tone="info">{markdownLines.length} 行</StatusChip>}
            bodyClassName="skill-markdown-viewer-shell"
            description="页面会展示完整的 SKILL.md 原文，方便核对技能定义和运行元数据。"
            title={skill.file_name}
            variant="panel"
          >
            <div className="skill-markdown-viewer" role="presentation">
              {markdownLines.map((line, index) => (
                <div key={`${skill.id}-${index + 1}`} className="skill-markdown-viewer__row">
                  <span className="skill-markdown-viewer__line-number">{index + 1}</span>
                  <code className="skill-markdown-viewer__line-content">{line || " "}</code>
                </div>
              ))}
            </div>
          </SurfaceCard>
        </>
      ) : null}
    </div>
  );
}

export default SkillDetailPage;
