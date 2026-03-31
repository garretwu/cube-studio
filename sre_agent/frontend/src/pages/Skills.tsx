import { useEffect, useState } from "react";

import { apiClient } from "../api/client";
import SkillCard from "../components/SkillCard";
import { AppButton, AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { normalizeSkills, type SkillViewModel } from "../features/skills/model";

function SkillsPage() {
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
      setErrorMessage(error instanceof Error ? error.message : "\u6280\u80fd\u5217\u8868\u52a0\u8f7d\u5931\u8d25");
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
  const availableCount = skills.filter((skill) => skill.status !== "unavailable").length;

  return (
    <div className="page-grid skills-page">
      <div className="page-intro">
        <SectionHeader
          eyebrow="Skill Directory"
          title={"\u6280\u80fd\u4e2d\u5fc3"}
          description={
            "\u4f7f\u7528\u5361\u7247\u96c6\u4e2d\u67e5\u770b Skill \u7684\u540d\u79f0\u3001\u53ef\u7528\u72b6\u6001\u3001Skill ID\u3001\u7b80\u4ecb\u4e0e\u66f4\u65b0\u65f6\u95f4\uff0c\u65b9\u4fbf\u5728\u8bca\u65ad\u4e0e\u81ea\u52a8\u5316\u6d41\u7a0b\u4e2d\u5feb\u901f\u7b5b\u9009\u3002"
          }
        />

        <SurfaceCard bodyClassName="skills-hero" className="skills-hero-shell" variant="hero">
          <div className="skills-hero__copy">
            <p className="skills-hero__eyebrow">Skill Overview</p>
            <h3 className="skills-hero__title">
              {"\u5f53\u524d\u53ef\u63a5\u5165\u7684\u80fd\u529b\u76ee\u5f55"}
            </h3>
            <p className="skills-hero__description">
              {
                "\u9875\u9762\u4ec5\u4fdd\u7559\u6280\u80fd\u68c0\u7d22\u548c\u6838\u5fc3\u4fe1\u606f\u5c55\u793a\uff0c\u53bb\u6389\u6765\u6e90\u3001\u6743\u9650\u5206\u503c\u7b49\u6b21\u8981\u5185\u5bb9\uff0c\u8ba9\u5217\u8868\u66f4\u6e05\u6670\u3002"
              }
            </p>
          </div>

          <div className="skills-hero__stats">
            <div className="skills-hero__stat">
              <span className="skills-hero__stat-label">{"\u6280\u80fd\u603b\u6570"}</span>
              <strong className="skills-hero__stat-value">{skills.length}</strong>
            </div>
            <div className="skills-hero__stat">
              <span className="skills-hero__stat-label">{"\u53ef\u7528\u6280\u80fd"}</span>
              <strong className="skills-hero__stat-value">{availableCount}</strong>
            </div>
          </div>
        </SurfaceCard>
      </div>

      <SurfaceCard bodyClassName="skills-toolbar" variant="soft">
        <div className="skills-toolbar__main">
          <StatusChip tone="accent">{`${filteredSkills.length} \u4e2a\u6280\u80fd`}</StatusChip>
          <StatusChip tone="neutral">{`${availableCount} \u4e2a\u53ef\u7528`}</StatusChip>
        </div>

        <div className="skills-toolbar__actions">
          <AppInput
            className="skills-toolbar__search"
            onChange={setQuery}
            placeholder={"\u641c\u7d22 Skill \u540d\u79f0\u6216 ID"}
            prefix={<AppIcon name="search" size={16} />}
            value={query}
          />
          <AppButton
            aria-label={"\u5237\u65b0\u6280\u80fd\u5217\u8868"}
            iconLeft="refresh"
            loading={isRefreshing}
            onClick={() => void loadSkills(true)}
            variant="secondary"
          >
            {"\u5237\u65b0"}
          </AppButton>
        </div>
      </SurfaceCard>

      {errorMessage ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">{"\u6280\u80fd\u5217\u8868\u6682\u65f6\u4e0d\u53ef\u7528"}</p>
          <p className="skills-empty__description">{errorMessage}</p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && isLoading ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">{"\u6b63\u5728\u52a0\u8f7d\u6280\u80fd\u5217\u8868"}</p>
          <p className="skills-empty__description">
            {"\u8bf7\u7a0d\u5019\uff0c\u6b63\u5728\u540c\u6b65\u6700\u65b0 Skill \u4fe1\u606f\u3002"}
          </p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && filteredSkills.length === 0 ? (
        <SurfaceCard bodyClassName="skills-empty" variant="soft">
          <p className="skills-empty__title">{"\u6ca1\u6709\u627e\u5230\u5339\u914d\u7684 Skill"}</p>
          <p className="skills-empty__description">
            {"\u53ef\u4ee5\u5c1d\u8bd5\u66f4\u6362\u5173\u952e\u5b57\uff0c\u6216\u6e05\u7a7a\u641c\u7d22\u540e\u91cd\u65b0\u67e5\u770b\u5168\u90e8\u6280\u80fd\u3002"}
          </p>
        </SurfaceCard>
      ) : null}

      {!errorMessage && !isLoading && filteredSkills.length > 0 ? (
        <div className="skills-grid">
          {filteredSkills.map((skill) => (
            <SkillCard key={skill.id} skill={skill} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default SkillsPage;
