import { StatusChip, SurfaceCard } from "./ui";
import type { SkillDescriptor } from "../api/types";
import { formatSkillScope } from "../utils/display";
import { formatPercent } from "../utils/format";

type SkillCardProps = {
  skill: SkillDescriptor;
};

function SkillCard({ skill }: SkillCardProps) {
  return (
    <SurfaceCard
      description={skill.summary}
      title={skill.name}
      variant={skill.scope === "builtin" ? "panel" : "soft"}
    >
      <div className="page-stack">
        <div className="status-row">
          <StatusChip tone={skill.scope === "builtin" ? "accent" : "info"}>{formatSkillScope(skill.scope)}</StatusChip>
          <StatusChip tone="neutral">{formatPercent(skill.match_score)}</StatusChip>
        </div>
        <p className="data-list__copy">{skill.source}</p>
        <div className="skill-card__footer">
          {skill.permissions.map((permission) => (
            <StatusChip key={permission} tone="neutral">
              {permission}
            </StatusChip>
          ))}
        </div>
      </div>
    </SurfaceCard>
  );
}

export default SkillCard;
