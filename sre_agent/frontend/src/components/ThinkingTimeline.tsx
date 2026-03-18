import { Card, Space, Tag, Typography } from "antd";

import type { Observation, ThinkingStep } from "../api/types";

type TimelineItem = ThinkingStep | Observation;

type ThinkingTimelineProps = {
  steps: TimelineItem[];
};

function isThought(item: TimelineItem): item is ThinkingStep {
  return "step" in item;
}

function ThinkingTimeline({ steps }: ThinkingTimelineProps) {
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {steps.map((item, index) =>
        isThought(item) ? (
          <Card key={`${item.timestamp}-${index}`} className="timeline-item">
            <Space direction="vertical" size={6}>
              <Space>
                <Tag color="cyan">Step {item.step}</Tag>
                <Tag color="blue">{item.action_type}</Tag>
                {item.confidence ? <Tag>{Math.round(item.confidence * 100)}%</Tag> : null}
              </Space>
              <Typography.Text strong>{item.thought}</Typography.Text>
              {item.tool_name ? (
                <Typography.Text type="secondary">
                  {item.tool_name} {item.tool_params ? JSON.stringify(item.tool_params) : ""}
                </Typography.Text>
              ) : null}
            </Space>
          </Card>
        ) : (
          <Card key={`${item.timestamp}-${index}`} className="timeline-item">
            <Space direction="vertical" size={6}>
              <Space>
                <Tag color="green">Observation</Tag>
                <Tag>{item.tool}</Tag>
              </Space>
              <Typography.Text type="secondary">{JSON.stringify(item.result, null, 2)}</Typography.Text>
            </Space>
          </Card>
        ),
      )}
    </Space>
  );
}

export default ThinkingTimeline;
