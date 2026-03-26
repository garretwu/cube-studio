import { StatusChip } from "./ui";
import type { ChatMessage as ChatMessageType } from "../api/types";
import { formatRole } from "../utils/display";
import { formatTimestamp } from "../utils/format";

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

function ChatMessage({ message }: ChatMessageProps) {
  return (
    <article className={`chat-message ${message.role === "user" ? "chat-message--user" : ""}`}>
      <div className="chat-message__meta">
        <StatusChip tone={toneByRole(message.role)}>{formatRole(message.role)}</StatusChip>
        <p className="chat-message__time">{formatTimestamp(message.created_at)}</p>
      </div>
      <p className="data-list__copy" style={{ color: "inherit", margin: 0 }}>
        {message.content}
      </p>
      {message.tool_name ? <div className="chat-message__tool">{message.tool_name}</div> : null}
    </article>
  );
}

export default ChatMessage;
