import { create } from 'zustand';

export type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
  timestamp: number;
};

type ChatState = {
  messages: Message[];
  sessionId: string | null;
  isLoggedIn: boolean;
  isLoading: boolean;

  addMessage: (msg: Omit<Message, 'id' | 'timestamp'>) => string;
  appendToMessage: (id: string, content: string) => void;
  finalizeMessage: (id: string) => void;
  setSession: (sessionId: string | null) => void;
  setLoggedIn: (v: boolean) => void;
  setLoading: (v: boolean) => void;
  clearMessages: () => void;
};

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  sessionId: null,
  isLoggedIn: false,
  isLoading: false,

  addMessage: (msg) => {
    const id = crypto.randomUUID();
    set((s) => ({
      messages: [...s.messages, { ...msg, id, timestamp: Date.now() }],
    }));
    return id;
  },

  appendToMessage: (id, content) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, content: m.content + content } : m,
      ),
    })),

  finalizeMessage: (id) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, isStreaming: false } : m,
      ),
    })),

  setSession: (sessionId) => set({ sessionId }),
  setLoggedIn: (isLoggedIn) => set({ isLoggedIn }),
  setLoading: (isLoading) => set({ isLoading }),
  clearMessages: () => set({ messages: [] }),
}));
