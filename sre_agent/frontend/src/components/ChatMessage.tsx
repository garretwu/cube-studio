import type { ChatMessage as ChatMessageType } from "../api/types";
import { formatTimestamp } from "../utils/format";
import { StatusChip } from "./ui";

type ChatMessageProps = {
  message: ChatMessageType;
};

function toneByRole(role: ChatMessageType["role"]) {
  switch (role) {
    case "user":
      return "neutral";
    case "tool":
      return "warning";
    default:
      return "accent";
  }
}

function labelByRole(role: ChatMessageType["role"]) {
  switch (role) {
    case "user":
      return "我";
    case "tool":
      return "工具";
    default:
      return "助手";
  }
}

function ChatMessage({ message }: ChatMessageProps) {
  return (
    <article className={`chat-message chat-message--${message.role}`}>
      <div className="chat-message__meta">
        <StatusChip tone={toneByRole(message.role)}>{labelByRole(message.role)}</StatusChip>
        <p className="chat-message__time">{formatTimestamp(message.created_at)}</p>
      </div>
      <p className="chat-message__content">{message.content}</p>
      {message.tool_name ? <div className="chat-message__tool">{message.tool_name}</div> : null}
    </article>
  );
}

export default ChatMessage;
