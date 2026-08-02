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
import { applyAppearance } from "./appearance";
import type { Entry } from "./chat";
import type { Route } from "./nav";
import { outcomeOf } from "./chat";

/** Routes live in lib/nav.ts, which is also what draws them. */
export type View = Route;

export interface ChatEntry {
  id: string;
  text: string;
  at: number;
  taskId?: string;
  /** What this is. See lib/chat.ts — every value maps to something the engine
   *  produced, never to a category the window invented. */
  kind: Entry;
  /** The engine's own classification, for developer mode. */
  intent?: string;
  /** An approval this entry is about, so the card can resolve it in place. */
  approvalId?: string;
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
  runAsTask: (goal: string) => Promise<void>;
  /** Text put into the composer from elsewhere — "Popraw" on a sent message. */
  composerDraft: string;
  setComposerDraft: (text: string) => void;
  stopTask: (id: string) => Promise<void>;
  resolveApproval: (id: string, approved: boolean) => Promise<void>;
  say: (text: string, kind?: Entry, extra?: Partial<ChatEntry>) => void;
  /** True while a request the user is waiting on is genuinely open. */
  sending: boolean;
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
  sending: false,
  composerDraft: "",

  tasks: [],
  approvals: [],
  chat: [],
  progress: [],

  memories: [],
  secrets: [],
  devices: [],
  tools: [],

  setApi: (api) => set({ api }),
  setComposerDraft: (composerDraft) => set({ composerDraft }),
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

  say: (text, kind = "notice", extra = {}) =>
    set((current) => ({
      chat: [
        ...current.chat.slice(-MAX_CHAT),
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          text,
          at: Date.now() / 1000,
          kind,
          ...extra,
        },
      ],
    })),

  handleEvent: (event) => {
    const { topic, data, at } = event;
    const store = get();

    switch (topic) {
      case "state": {
        const engine = data as unknown as EngineState;
        set({ engine, tasks: engine.active_tasks ?? [] });
        // The look travels in the same frame as everything else, so the window
        // never paints one theme and then corrects itself.
        applyAppearance(engine.appearance ?? {});
        void store.refresh();
        return;
      }

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

      case "config.changed":
        void store.refresh();
        return;

      default:
        break;
    }

    // What the user actually hears about. Everything else stays in the panels.
    const taskId = data.task_id as string | undefined;

    if (topic === "task.finished") {
      const text = data.report as string;
      // "done" and "done and checked" are different claims and the engine
      // distinguishes them, so the window must not flatten them into one.
      const task = get().tasks.find((candidate) => candidate.id === taskId);
      if (text) store.say(text, outcomeOf(task?.report), { taskId });
    }
    if (topic === "task.failed") {
      const text = (data.report as string) || "Nie udało się.";
      store.say(text, "failure", { taskId });
    }
    if (topic === "task.blocked") {
      // An approval and a question are not the same interruption: one wants a
      // decision, the other wants information. The engine says which.
      const approvalId = data.approval_id as string | undefined;
      const question =
        (data.question as string) ||
        (approvalId ? "Potrzebuję Twojej zgody." : "Potrzebuję odpowiedzi.");
      store.say(question, approvalId ? "approval" : "clarification", {
        taskId,
        approvalId,
      });
    }
    if (topic === "notice" && !data.silent) {
      const message = data.message as string;
      if (message) store.say(message, "notice", { taskId });
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
      // Settings can change from another window, from the file on disk, or from
      // the settings screen. Every path ends here, so this is where the look is
      // re-applied rather than in each of them.
      applyAppearance(engine.appearance ?? {});
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

    set((current) => ({
      chat: [
        ...current.chat.slice(-MAX_CHAT),
        {
          id: `${Date.now()}-user`,
          text,
          at: Date.now() / 1000,
          kind: "user" as Entry,
        },
      ],
      // Optimistic about *a request being open*, which is a fact, never about
      // what the engine will decide it is.
      sending: true,
    }));

    try {
      // Everything typed goes through /api/say. The engine decides whether it
      // is work; the window does not get to guess, because guessing is what
      // turned "cześć" into a task in the first place.
      const said = await api.say(text);
      if (said.kind === "task") {
        // The card renders from the task itself, so all the entry needs is the
        // id — the report, steps and state arrive over the event stream.
        get().say("", "task", { taskId: said.task_id, intent: said.intent });
      } else {
        get().say(said.text, said.kind === "pointer" ? "notice" : "answer", {
          intent: said.intent,
          taskId: said.task_id,
        });
      }
    } catch (error) {
      get().say(
        error instanceof Error ? error.message : "Nie udało się wysłać.",
        "failure",
      );
    } finally {
      set({ sending: false });
    }
  },

  /** The explicitly executable path. Only a caller that already knows it has a
   *  goal — "Wykonaj mimo to" — may create a task without classification. */
  runAsTask: async (goal) => {
    const { api } = get();
    const text = goal.trim();
    if (!api || !text) return;
    set({ sending: true });
    try {
      const task = await api.submit(text);
      get().say("", "task", { taskId: task.task_id });
    } catch (error) {
      get().say(
        error instanceof Error ? error.message : "Nie udało się zlecić zadania.",
        "failure",
      );
    } finally {
      set({ sending: false });
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
