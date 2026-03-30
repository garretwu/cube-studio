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
        const message = err instanceof Error ? err.message : "unknown request failure";
        setError(`技能接口暂不可用。${message}`);
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
        <SurfaceCard title="请求失败" description="未能从后端加载技能列表。">
          <div className="mini-card">
            <p className="mini-card__copy">{error}</p>
          </div>
        </SurfaceCard>
      ) : null}

      {!error && skills.length === 0 ? (
        <SurfaceCard title="暂无技能" description="后端返回成功，但当前技能目录为空。">
          <div className="mini-card">
            <p className="mini-card__copy">当前运行时未注册任何技能。</p>
          </div>
        </SurfaceCard>
      ) : null}

      <div className="card-grid--two">{skills.map((skill) => <SkillCard key={skill.id} skill={skill} />)}</div>
    </div>
  );
}

export default SkillsPage;
