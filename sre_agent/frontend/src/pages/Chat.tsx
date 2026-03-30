import { useEffect, useState } from "react";

import ChatMessage from "../components/ChatMessage";
import { AppButton, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useChatStore } from "../store/chatStore";

function ChatPage() {
  const [draft, setDraft] = useState("");
  const { messages, isSending, loadHistory, sendMessage } = useChatStore();

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          description="在一条连续线程里查询影响半径、运行手册、安全检查或修复评审，让对话始终绑定现场上下文。"
          eyebrow="协同对话"
          title="SRE 对话工作台"
        />
      </div>

      <SurfaceCard description="已绑定拓扑、诊断、记忆与知识上下文的对话记录。" title="对话记录">
        <div className="chat-thread">
          {messages.map((message) => (
            <ChatMessage key={message.id} message={message} />
          ))}
        </div>
      </SurfaceCard>

      <SurfaceCard description="输入下一条问题、排查请求或执行指令。" title="发送请求">
        <div className="composer-layout">
          <AppInput
            multiline
            onChange={setDraft}
            placeholder="描述当前信号、希望验证的假设，或需要评审的修复动作。"
            rows={4}
            value={draft}
          />
          <div className="composer-layout__actions">
            <div className="status-row">
              <StatusChip tone="neutral">已关联上下文</StatusChip>
              <StatusChip tone="accent">运维记忆</StatusChip>
            </div>
            <AppButton
              disabled={!draft.trim()}
              iconRight="send"
              loading={isSending}
              onClick={() => {
                if (!draft.trim()) {
                  return;
                }
                void sendMessage(draft.trim());
                setDraft("");
              }}
              variant="primary"
            >
              发送
            </AppButton>
          </div>
        </div>
      </SurfaceCard>
    </div>
  );
}

export default ChatPage;
