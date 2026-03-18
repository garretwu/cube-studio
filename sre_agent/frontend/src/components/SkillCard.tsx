import { Card, Space, Tag, Typography } from "antd";

import type { SkillDescriptor } from "../api/types";

type SkillCardProps = {
  skill: SkillDescriptor;
};

function SkillCard({ skill }: SkillCardProps) {
  return (
    <Card className="panel-card">
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Space wrap>
          <Tag color={skill.scope === "builtin" ? "cyan" : "purple"}>{skill.scope}</Tag>
          <Tag>{Math.round(skill.match_score * 100)}%</Tag>
        </Space>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {skill.name}
        </Typography.Title>
        <Typography.Text>{skill.summary}</Typography.Text>
        <Typography.Text type="secondary">{skill.source}</Typography.Text>
        <Space wrap>
          {skill.permissions.map((permission) => (
            <Tag key={permission}>{permission}</Tag>
          ))}
        </Space>
      </Space>
    </Card>
  );
}

export default SkillCard;
