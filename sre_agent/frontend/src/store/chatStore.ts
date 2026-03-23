import { create } from "zustand";

import { apiClient } from "../api/client";
import type { ChatMessage } from "../api/types";

type ChatState = {
  messages: ChatMessage[];
  isSending: boolean;
  sendMessage: (content: string) => Promise<void>;
};

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isSending: false,
  sendMessage: async (content: string) => {
    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    set((state) => ({ messages: [...state.messages, userMessage], isSending: true }));
    const reply = await apiClient.postChatMessage(content);
    set((state) => ({ messages: [...state.messages, reply], isSending: false }));
  },
}));
