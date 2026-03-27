import { useEffect, useState } from "react";

import { apiClient } from "../api/client";
import type { SkillDescriptor } from "../api/types";
import SkillCard from "../components/SkillCard";
import { SectionHeader, SurfaceCard } from "../components/ui";

function SkillsPage() {
  const [skills, setSkills] = useState<SkillDescriptor[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      setError("");
      try {
        setSkills(await apiClient.getSkills());
      } catch (err) {
        setError(`技能接口暂不可用，已降级展示。${err instanceof Error ? ` (${err.message})` : ""}`);
        setSkills([]);
      }
    })();
  }, []);

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          description="查看当前运行时可调用的内置与自定义能力模块。"
          eyebrow="能力注册表"
          title="技能注册表"
        />
      </div>

      {error ? (
        <SurfaceCard title="降级提示" description="当前未读取到技能列表。">
          <div className="mini-card">
            <p className="mini-card__copy">{error}</p>
          </div>
        </SurfaceCard>
      ) : null}

      <div className="card-grid--two">{skills.map((skill) => <SkillCard key={skill.id} skill={skill} />)}</div>
    </div>
  );
}

export default SkillsPage;
