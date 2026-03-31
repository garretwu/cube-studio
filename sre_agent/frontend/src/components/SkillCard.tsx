import { useNavigate } from "react-router-dom";

import { getSkillStatusMeta, type SkillViewModel } from "../features/skills/model";
import { formatDateTime } from "../utils/format";
import { AppIcon, StatusChip, SurfaceCard } from "./ui";

type SkillCardProps = {
  skill: SkillViewModel;
};

function SkillCard({ skill }: SkillCardProps) {
  const navigate = useNavigate();
  const status = getSkillStatusMeta(skill);

  return (
    <SurfaceCard bodyClassName="skill-card__body" className="skill-card-shell" variant="panel">
      <button
        aria-label={`查看技能详情 ${skill.name}`}
        className="skill-card"
        onClick={() => navigate(`/skills/${encodeURIComponent(skill.id)}`)}
        type="button"
      >
        <div className="skill-card__header">
          <div className="skill-card__icon">
            <AppIcon name="skills" size={22} variant="fill" />
          </div>
          <div className="skill-card__headline">
            <div className="skill-card__title-row">
              <h3 className="skill-card__title">{skill.name}</h3>
              <StatusChip tone={status.tone}>{status.label}</StatusChip>
            </div>
            <p className="skill-card__id">Skill ID: {skill.id}</p>
          </div>
        </div>

        <p className="skill-card__summary">{skill.summary}</p>

        <div className="skill-card__meta">
          <span className="skill-card__meta-label">更新时间</span>
          <span className="skill-card__meta-value">{formatDateTime(skill.updated_at)}</span>
        </div>

        <div className="skill-card__cta">
          <span className="skill-card__cta-label">查看详情</span>
          <AppIcon name="arrowRight" size={16} />
        </div>
      </button>
    </SurfaceCard>
  );
}

export default SkillCard;
