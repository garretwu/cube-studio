import { useEffect, useState } from "react";

import { apiClient } from "../api/client";
import type { SkillDescriptor } from "../api/types";
import SkillCard from "../components/SkillCard";
import { SectionHeader } from "../components/ui";

function SkillsPage() {
  const [skills, setSkills] = useState<SkillDescriptor[]>([]);

  useEffect(() => {
    void apiClient.getSkills().then(setSkills);
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

      <div className="card-grid--two">
        {skills.map((skill) => (
          <SkillCard key={skill.id} skill={skill} />
        ))}
      </div>
    </div>
  );
}

export default SkillsPage;
