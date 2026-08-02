/**
 * UI state, fed by the engine's event stream.
 *
 * One rule: the engine is the source of truth. The store caches what it was told
 * and never invents a state — a task is "running" because an event said so, not
 * because we just submitted it. That is what keeps the window honest after it has
 * been closed, reopened, or opened on a second machine.
 */

import { create } from "zustand";
import type {
  AgentState,
  Approval,
  Device,
  EngineState,
  Envelope,
  Memory,
  Secret,
  Task,
  Tool,
} from "./api";
import { GarisApi } from "./api";

export type View =
  | "home"
  | "conversation"
  | "tasks"
  | "memory"
  | "devices"
  | "subscriptions"
  | "settings"
  | "diagnostics";

export interface ChatEntry {
  id: string;
  role: "user" | "garis";
  text: string;
  at: number;
  taskId?: string;
  /** "answer" is conversation — a reply with no task behind it. */
  kind?: "report" | "question" | "notice" | "answer";
  pending?: boolean;
}

export interface ProgressLine {
  taskId: string;
  text: string;
  at: number;
}

interface Store {
  api: GarisApi | null;
  connection: "connecting" | "open" | "closed";
  engine: EngineState | null;

  view: View;
  agentState: AgentState;
  /** Microphone amplitude 0..1 — drives the orb's listening rings. */
  level: number;

  tasks: Task[];
  approvals: Approval[];
  chat: ChatEntry[];
  progress: ProgressLine[];

  memories: Memory[];
  secrets: Secret[];
  devices: Device[];
  tools: Tool[];

  setApi: (api: GarisApi) => void;
  setView: (view: View) => void;
  setLevel: (level: number) => void;
  setAgentState: (state: AgentState) => void;

  handleEvent: (event: Envelope) => void;
  setConnection: (status: "connecting" | "open" | "closed") => void;

  refresh: () => Promise<void>;
  refreshMemories: (query?: string) => Promise<void>;
  refreshSecrets: () => Promise<void>;
  refreshDevices: () => Promise<void>;
  refreshTools: () => Promise<void>;

  send: (goal: string) => Promise<void>;
  stopTask: (id: string) => Promise<void>;
  resolveApproval: (id: string, approved: boolean) => Promise<void>;
  say: (text: string, kind?: ChatEntry["kind"], taskId?: string) => void;
}

const MAX_CHAT = 300;
const MAX_PROGRESS = 40;

/** After a terminal state the orb should settle, not stay lit. */
const SETTLE_AFTER_MS = 2600;
let settleTimer: ReturnType<typeof setTimeout> | null = null;

export const useStore = create<Store>((set, get) => ({
  api: null,
  connection: "connecting",
  engine: null,

  view: "home",
  agentState: "idle",
  level: 0,

  tasks: [],
  approvals: [],
  chat: [],
  progress: [],

  memories: [],
  secrets: [],
  devices: [],
  tools: [],

  setApi: (api) => set({ api }),
  setView: (view) => set({ view }),
  setLevel: (level) => set({ level }),

  setAgentState: (agentState) => {
    if (settleTimer) clearTimeout(settleTimer);
    set({ agentState });
    if (agentState === "done" || agentState === "failed") {
      settleTimer = setTimeout(() => set({ agentState: "idle" }), SETTLE_AFTER_MS);
    }
  },

  setConnection: (connection) => set({ connection }),

  say: (text, kind = "notice", taskId) =>
    set((current) => ({
      chat: [
        ...current.chat.slice(-MAX_CHAT),
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          role: "garis",
          text,
          at: Date.now() / 1000,
          kind,
          taskId,
        },
      ],
    })),

  handleEvent: (event) => {
    const { topic, data, at } = event;
    const store = get();

    switch (topic) {
      case "state":
        set({ engine: data as unknown as EngineState,
              tasks: (data as unknown as EngineState).active_tasks ?? [] });
        void store.refresh();
        return;

      case "agent.state":
        store.setAgentState((data.state as AgentState) ?? "idle");
        return;

      case "voice.state":
        if (data.state === "listening" || data.state === "speaking") {
          store.setAgentState(data.state as AgentState);
        }
        return;

      case "task.created":
      case "task.started":
      case "task.finished":
      case "task.failed":
      case "task.blocked":
      case "task.stopped":
        void store.refresh();
        break;

      case "task.step":
      case "task.progress": {
        const text =
          (data.message as string) ??
          [data.tool, data.purpose].filter(Boolean).join(" — ");
        if (!text) return;
        set((current) => ({
          progress: [
            ...current.progress.slice(-MAX_PROGRESS),
            { taskId: (data.task_id as string) ?? "", text, at },
          ],
        }));
        return;
      }

      case "approval.requested":
      case "approval.resolved":
        void store.refresh();
        break;

      case "memory.changed":
        void store.refreshMemories();
        return;

      default:
        break;
    }

    // What the user actually hears about. Everything else stays in the panels.
    if (topic === "task.finished" || topic === "task.failed") {
      const report = data.report as string;
      if (report) store.say(report, "report", data.task_id as string);
    }
    if (topic === "task.blocked") {
      const question = (data.question as string) ?? "Potrzebuję Twojej zgody.";
      store.say(question, "question", data.task_id as string);
    }
    if (topic === "notice" && !data.silent) {
      const message = data.message as string;
      if (message) store.say(message, "notice", data.task_id as string);
    }
  },

  refresh: async () => {
    const { api } = get();
    if (!api) return;
    try {
      const [engine, tasks, approvals] = await Promise.all([
        api.state(),
        api.tasks("?limit=60"),
        api.approvals(),
      ]);
      set({ engine, tasks: tasks.tasks, approvals: approvals.approvals });
    } catch {
      /* the stream will trigger another refresh; a failed poll is not news */
    }
  },

  refreshMemories: async (query = "") => {
    const { api } = get();
    if (!api) return;
    try {
      set({ memories: (await api.memories(query)).memories });
    } catch {
      /* ignore */
    }
  },

  refreshSecrets: async () => {
    const { api } = get();
    if (!api) return;
    try {
      set({ secrets: (await api.secrets()).secrets });
    } catch {
      /* ignore */
    }
  },

  refreshDevices: async () => {
    const { api } = get();
    if (!api) return;
    try {
      set({ devices: (await api.devices()).devices });
    } catch {
      /* ignore */
    }
  },

  refreshTools: async () => {
    const { api } = get();
    if (!api) return;
    try {
      set({ tools: (await api.tools()).tools });
    } catch {
      /* ignore */
    }
  },

  send: async (goal) => {
    const { api } = get();
    const text = goal.trim();
    if (!api || !text) return;

    const id = `${Date.now()}-user`;
    set((current) => ({
      chat: [
        ...current.chat.slice(-MAX_CHAT),
        { id, role: "user", text, at: Date.now() / 1000 },
      ],
      // Optimistic only about *appearing to think* — never about the outcome.
      agentState: "thinking",
    }));

    try {
      const said = await api.say(text);
      if (said.kind === "task") return; // the task's own events take over
      // A greeting has no task to watch, so nothing else will ever settle the
      // orb — say the answer and put it back to rest here.
      get().say(said.text, said.kind === "pointer" ? "notice" : "answer");
      get().setAgentState("done");
    } catch (error) {
      get().say(
        error instanceof Error ? error.message : "Nie udało się zlecić zadania.",
        "notice",
      );
      get().setAgentState("failed");
    }
  },

  stopTask: async (id) => {
    const { api } = get();
    if (!api) return;
    await api.stopTask(id);
    await get().refresh();
  },

  resolveApproval: async (id, approved) => {
    const { api } = get();
    if (!api) return;
    await api.resolveApproval(id, approved);
    await get().refresh();
  },
}));

export const activeTasks = (tasks: Task[]) =>
  tasks.filter((task) => ["pending", "running", "blocked"].includes(task.state));

export const finishedTasks = (tasks: Task[]) =>
  tasks.filter((task) => ["finished", "failed", "stopped"].includes(task.state));
