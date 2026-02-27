import { create } from 'zustand';

export type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
  timestamp: number;
};

export type ToolStep = {
  name: string;
  status: 'running' | 'done' | 'error';
  startedAt: number;
};

type ChatState = {
  messages: Message[];
  sessionId: string | null;
  isLoggedIn: boolean;
  isLoading: boolean;
  // Browser control state
  isBrowserControlling: boolean;
  currentAction: string | null;
  agentTabId: number | null;
  agentTabGroupId: number | null;
  // Tool execution steps (shown during browser control)
  toolSteps: ToolStep[];

  addMessage: (msg: Omit<Message, 'id' | 'timestamp'>) => string;
  appendToMessage: (id: string, content: string) => void;
  finalizeMessage: (id: string) => void;
  setSession: (sessionId: string | null) => void;
  setLoggedIn: (v: boolean) => void;
  setLoading: (v: boolean) => void;
  clearMessages: () => void;
  // Browser control actions
  setBrowserControlling: (v: boolean, action?: string | null) => void;
  setAgentTab: (tabId: number | null, groupId: number | null) => void;
  addToolStep: (name: string) => void;
  completeToolStep: (name: string) => void;
  clearToolSteps: () => void;
};

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  sessionId: null,
  isLoggedIn: false,
  isLoading: false,
  isBrowserControlling: false,
  currentAction: null,
  agentTabId: null,
  agentTabGroupId: null,
  toolSteps: [],

  addMessage: (msg) => {
    const id = crypto.randomUUID();
    set((s) => {
      const messages = [...s.messages, { ...msg, id, timestamp: Date.now() }];
      return { messages: messages.length > 200 ? messages.slice(-200) : messages };
    });
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

  setBrowserControlling: (isBrowserControlling, currentAction = null) =>
    set({ isBrowserControlling, currentAction }),

  setAgentTab: (agentTabId, agentTabGroupId) =>
    set({ agentTabId, agentTabGroupId }),

  addToolStep: (name) =>
    set((s) => ({
      toolSteps: [
        ...s.toolSteps,
        { name, status: 'running', startedAt: Date.now() },
      ],
    })),

  completeToolStep: (name) =>
    set((s) => ({
      toolSteps: s.toolSteps.map((step) =>
        step.name === name && step.status === 'running'
          ? { ...step, status: 'done' }
          : step,
      ),
    })),

  clearToolSteps: () => set({ toolSteps: [] }),
}));
