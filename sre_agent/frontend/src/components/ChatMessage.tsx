import { Card, Space, Tag, Typography } from "antd";

import type { ChatMessage as ChatMessageType } from "../api/types";

type ChatMessageProps = {
  message: ChatMessageType;
};

function ChatMessage({ message }: ChatMessageProps) {
  return (
    <div className={`chat-bubble ${message.role === "user" ? "user" : "agent"}`}>
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <Space>
          <Tag color={message.role === "user" ? "geekblue" : message.role === "tool" ? "gold" : "cyan"}>{message.role}</Tag>
          <Typography.Text type={message.role === "user" ? undefined : "secondary"}>{message.created_at}</Typography.Text>
        </Space>
        <Typography.Paragraph style={{ marginBottom: 0, color: "inherit" }}>{message.content}</Typography.Paragraph>
        {message.tool_name ? (
          <Card size="small">
            <Typography.Text type="secondary">{message.tool_name}</Typography.Text>
          </Card>
        ) : null}
      </Space>
    </div>
  );
}

export default ChatMessage;
