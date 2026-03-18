import { useEffect, useState } from "react";
import { Card, Col, Row, Typography } from "antd";

import { apiClient } from "../api/client";
import type { SkillDescriptor } from "../api/types";
import SkillCard from "../components/SkillCard";

function SkillsPage() {
  const [skills, setSkills] = useState<SkillDescriptor[]>([]);

  useEffect(() => {
    void apiClient.getSkills().then(setSkills);
  }, []);

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Typography.Title level={2} style={{ margin: 0 }}>
          Skill Registry
        </Typography.Title>
      </Card>
      <Row gutter={[20, 20]}>
        {skills.map((skill) => (
          <Col key={skill.id} xs={24} lg={12}>
            <SkillCard skill={skill} />
          </Col>
        ))}
      </Row>
    </div>
  );
}

export default SkillsPage;
