import { useMemo, useState } from "react";
import { Button, Card, Input, Space, Typography } from "antd";

import ChatMessage from "../components/ChatMessage";
import { initialChatMessages } from "../mocks/data";
import { useChatStore } from "../store/chatStore";

function ChatPage() {
  const [draft, setDraft] = useState("");
  const { messages, isSending, sendMessage } = useChatStore();

  const merged = useMemo(() => [...initialChatMessages, ...messages], [messages]);

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Typography.Title level={2} style={{ margin: 0 }}>
          Conversational SRE Copilot
        </Typography.Title>
      </Card>
      <Card className="panel-card">
        <div className="chat-stack">
          {merged.map((message) => (
            <ChatMessage key={message.id} message={message} />
          ))}
        </div>
      </Card>
      <Card className="panel-card">
        <Space.Compact style={{ width: "100%" }}>
          <Input.TextArea value={draft} autoSize={{ minRows: 2, maxRows: 4 }} onChange={(event) => setDraft(event.target.value)} />
          <Button
            type="primary"
            loading={isSending}
            onClick={() => {
              if (!draft.trim()) {
                return;
              }
              void sendMessage(draft.trim());
              setDraft("");
            }}
          >
            Send
          </Button>
        </Space.Compact>
      </Card>
    </div>
  );
}

export default ChatPage;
