import { create } from "zustand";

import { apiClient } from "../api/client";
import type { ChatMessage } from "../api/types";

type ChatState = {
  messages: ChatMessage[];
  isSending: boolean;
  isLoadingHistory: boolean;
  loadHistory: () => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
};

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isSending: false,
  isLoadingHistory: false,
  loadHistory: async () => {
    set({ isLoadingHistory: true });
    try {
      const history = await apiClient.getChatHistory();
      set({ messages: history, isLoadingHistory: false });
    } catch {
      set({ isLoadingHistory: false });
    }
  },
  sendMessage: async (content: string) => {
    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    set((state) => ({ messages: [...state.messages, userMessage], isSending: true }));
    try {
      const reply = await apiClient.postChatMessage(content);
      set((state) => ({ messages: [...state.messages, reply], isSending: false }));
    } catch {
      set({ isSending: false });
    }
  },
}));
