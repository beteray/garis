/**
 * Client for the local engine API (see docs/API.md).
 *
 * The UI owns no logic that the engine owns: no deciding what needs approval, no
 * inventing task state, no local queue of goals. It sends intent and renders what
 * comes back. Everything here is transport.
 */

export type AgentState =
  | "idle"
  | "listening"
  | "thinking"
  | "working"
  | "verifying"
  | "speaking"
  | "blocked"
  | "done"
  | "failed";

export type TaskState =
  | "pending"
  | "running"
  | "blocked"
  | "finished"
  | "failed"
  | "stopped";

export interface Report {
  short: string;
  details?: string;
  verified?: boolean;
  problems?: string[];
  developer?: Record<string, unknown>;
}

/** The answer to `POST /api/say`: an answer, a started task, or a pointer to
 *  the question already on screen. `kind` is the contract — never infer it. */
export interface Said {
  kind: "chat" | "task" | "pointer";
  text: string;
  intent: string;
  task_id?: string;
  task?: Task;
}

export interface Task {
  id: string;
  goal: string;
  state: TaskState;
  criteria: string[];
  origin: string;
  target: string;
  created_at: number;
  updated_at: number;
  started_at?: number | null;
  finished_at?: number | null;
  error?: string;
  question?: string;
  report?: Report;
  plan?: { summary?: string; steps?: PlanStep[]; assumptions?: string[] };
  steps?: Step[];
  approvals?: Approval[];
}

export interface PlanStep {
  key: string;
  tool: string;
  purpose?: string;
  expects?: string;
}

export interface Step {
  task_id: string;
  step_key: string;
  ordinal: number;
  tool: string;
  state: "pending" | "running" | "done" | "failed";
  error?: string;
  attempts: number;
  started_at?: number | null;
  finished_at?: number | null;
}

export interface Approval {
  id: string;
  task_id?: string | null;
  tool: string;
  prompt: string;
  effects: string[];
  state: string;
  requested_at: number;
  resolved_by?: string | null;
}

export interface Memory {
  id: string;
  kind: string;
  subject: string;
  preview: string;
  content?: string;
  tags: string[];
  scope: string;
  pinned: boolean;
  created_at: number;
  updated_at: number;
}

export interface Secret {
  name: string;
  kind: string;
  note: string;
  ref: string;
  used_at?: number | null;
  use_count: number;
}

export interface Device {
  id: string;
  name: string;
  role: string;
  address: string;
  state: string;
  seen_at?: number | null;
}

export interface Tool {
  name: string;
  summary: string;
  category: string;
  effects: string[];
  available: boolean;
  reversible: boolean;
}

export interface EngineState {
  protocol: number;
  version: string;
  identity: {
    name: string;
    address_as: string;
    language: string;
    onboarded: boolean;
  };
  persona: { preset: string; brevity: number; humor: number };
  voice: {
    enabled: boolean;
    wake_word: string;
    wake_word_enabled: boolean;
    push_to_talk: string;
    voice_id: string;
  };
  dev: { verbose: boolean; developer_mode: boolean };
  counts: {
    active_tasks: number;
    pending_approvals: number;
    memories: number;
    secrets: number;
  };
  models: {
    privacy: string;
    spend: number;
    providers: { name: string; available: boolean; models: string[] }[];
    picks: Record<string, string>;
  };
  capabilities: {
    tools_total: number;
    tools_available: number;
    categories: Record<string, number>;
    unsupported_here: string[];
    policy: {
      confirm_effects: string[];
      download_notice: string;
      never: string[];
    };
  };
  active_tasks: Task[];
}

export interface Envelope {
  v: number;
  topic: string;
  at: number;
  data: Record<string, unknown>;
}

const DEFAULT_BASE = "http://127.0.0.1:8756";

export function inTauri(): boolean {
  return Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
}

/**
 * Every way the window can fail to reach the engine.
 *
 * One word — "łączę się…" — for all of them is the difference between an app
 * that is starting and an app that is broken, and the user could not tell which.
 * Each of these needs a different action from them, so each gets its own name.
 */
export type Link =
  | { state: "starting" }          // the shell is launching the engine
  | { state: "handshake" }         // engine launched, waiting for it to say where it is
  | { state: "connected" }
  | { state: "engine-failed"; detail: string }
  | { state: "bad-token"; detail: string }
  | { state: "no-response"; detail: string }
  | { state: "retrying"; attempt: number };

export interface Discovery {
  base: string;
  token: string;
  /** Why the shell has no engine to offer, if it has none. */
  error: string;
  /** True when the shell answered — i.e. we are packaged, not in a dev browser. */
  fromShell: boolean;
}

/** Where the engine is and how to prove we may talk to it. */
export async function discover(): Promise<Discovery> {
  // Inside Tauri the shell knows, because it started the engine itself.
  if (inTauri()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const info = await invoke<{ base: string; token: string; error?: string }>("engine_info");
      if (info?.token) {
        return { base: info.base, token: info.token, error: "", fromShell: true };
      }
      return { base: info?.base || DEFAULT_BASE, token: "", error: info?.error ?? "", fromShell: true };
    } catch (cause) {
      return {
        base: DEFAULT_BASE,
        token: "",
        error: `Powłoka nie odpowiedziała na pytanie o silnik (${String(cause)}).`,
        fromShell: true,
      };
    }
  }
  const params = new URLSearchParams(window.location.search);
  return {
    base: params.get("base") ?? localStorage.getItem("garis.base") ?? DEFAULT_BASE,
    token: params.get("token") ?? localStorage.getItem("garis.token") ?? "",
    error: "",
    fromShell: false,
  };
}

/** Ask the shell to start the engine again, and hand back what it found. */
export async function restartEngine(): Promise<Discovery> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("restart_engine");
  return discover();
}

/** Turn a failed request into the one sentence that says what to do about it. */
export function classify(cause: unknown): Link {
  if (cause instanceof ApiError) {
    if (cause.status === 401 || cause.status === 403) {
      return {
        state: "bad-token",
        detail: "Silnik odrzucił token tego okna. Uruchom silnik ponownie, żeby dostać nowy.",
      };
    }
    return { state: "no-response", detail: `Silnik odpowiedział błędem ${cause.status}: ${cause.message}` };
  }
  return {
    state: "no-response",
    detail:
      "Silnik nie odpowiada pod wskazanym adresem. Albo jeszcze wstaje, albo nie wystartował.",
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message);
  }
}

export class GarisApi {
  private socket: WebSocket | null = null;
  private retry = 0;
  private closing = false;

  constructor(
    private base: string,
    private token: string,
  ) {}

  setCredentials(base: string, token: string) {
    this.base = base;
    this.token = token;
    // Only a dev browser session needs these remembered. In a packaged app the
    // shell hands the token over on every start, so writing it to localStorage
    // would leave a live credential lying around for no benefit at all.
    if (inTauri()) return;
    localStorage.setItem("garis.base", base);
    localStorage.setItem("garis.token", token);
  }

  get endpoint(): string {
    return this.base;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const response = await fetch(`${this.base}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok) {
      throw new ApiError(
        payload?.error ?? `HTTP ${response.status}`,
        response.status,
        payload?.detail,
      );
    }
    return payload as T;
  }

  health = () => this.request<{ ok: boolean; protocol: number }>("GET", "/api/health");
  state = () => this.request<EngineState>("GET", "/api/state");

  tasks = (query = "") =>
    this.request<{ tasks: Task[] }>("GET", `/api/tasks${query}`);
  task = (id: string) => this.request<Task>("GET", `/api/tasks/${id}`);
  /** Everything a person types. The engine decides whether it is work. */
  say = (text: string, target = "local") =>
    this.request<Said>("POST", "/api/say", { text, target });
  submit = (goal: string, criteria: string[] = [], target = "local") =>
    this.request<Task & { task_id: string }>("POST", "/api/tasks", {
      goal,
      criteria,
      target,
    });
  stopTask = (id: string) =>
    this.request<{ stopped: boolean }>("POST", `/api/tasks/${id}/stop`);
  resumeTask = (id: string) => this.request<Task>("POST", `/api/tasks/${id}/resume`);

  approvals = () => this.request<{ approvals: Approval[] }>("GET", "/api/approvals");
  resolveApproval = (id: string, approved: boolean) =>
    this.request<Approval>("POST", `/api/approvals/${id}`, { approved, by: "ui" });

  memories = (query = "", kind = "") => {
    const search = new URLSearchParams();
    if (query) search.set("query", query);
    if (kind) search.set("kind", kind);
    const suffix = search.toString() ? `?${search}` : "";
    return this.request<{ memories: Memory[]; stats: Record<string, unknown> }>(
      "GET",
      `/api/memory${suffix}`,
    );
  };
  remember = (content: string, kind = "fact", subject = "") =>
    this.request<Memory>("POST", "/api/memory", { content, kind, subject });
  correctMemory = (id: string, patch: Partial<Memory>) =>
    this.request<Memory>("PATCH", `/api/memory/${id}`, patch);
  forget = (id: string) =>
    this.request<{ forgotten: boolean }>("DELETE", `/api/memory/${id}`);

  secrets = () => this.request<{ secrets: Secret[] }>("GET", "/api/vault");
  storeSecret = (name: string, value: string, note = "") =>
    this.request<{ ref: string }>("POST", "/api/vault", { name, value, note });
  deleteSecret = (name: string) =>
    this.request<{ deleted: boolean }>("DELETE", `/api/vault/${name}`);

  devices = () => this.request<{ devices: Device[] }>("GET", "/api/devices");
  tools = () => this.request<{ tools: Tool[] }>("GET", "/api/tools");
  activity = (minutes = 60) =>
    this.request<Record<string, unknown>>("GET", `/api/activity?minutes=${minutes}`);
  audit = (limit = 100, task?: string) =>
    this.request<{ audit: Record<string, unknown>[] }>(
      "GET",
      `/api/audit?limit=${limit}${task ? `&task=${task}` : ""}`,
    );

  config = () => this.request<Record<string, unknown>>("GET", "/api/config");
  patchConfig = (changes: Record<string, unknown>) =>
    this.request<{ changed: Record<string, unknown> }>("PATCH", "/api/config", changes);

  /**
   * Live event stream, with reconnection.
   *
   * The engine keeps working whether or not anyone is listening, so a dropped
   * socket is a UI problem to fix quietly — exponential backoff, and the first
   * frame after reconnecting is a full state snapshot, so nothing has to be
   * replayed.
   */
  connect(
    onEvent: (event: Envelope) => void,
    onStatus: (status: "connecting" | "open" | "closed") => void,
  ): () => void {
    this.closing = false;

    const open = () => {
      if (this.closing) return;
      onStatus(this.retry === 0 ? "connecting" : "connecting");
      const url = `${this.base.replace(/^http/, "ws")}/ws?token=${encodeURIComponent(this.token)}`;
      const socket = new WebSocket(url);
      this.socket = socket;

      socket.onopen = () => {
        this.retry = 0;
        onStatus("open");
      };
      socket.onmessage = (message) => {
        try {
          onEvent(JSON.parse(message.data as string) as Envelope);
        } catch {
          /* a malformed frame is not worth taking the UI down for */
        }
      };
      socket.onclose = () => {
        onStatus("closed");
        if (this.closing) return;
        this.retry = Math.min(this.retry + 1, 6);
        setTimeout(open, Math.min(500 * 2 ** (this.retry - 1), 15000));
      };
      socket.onerror = () => socket.close();
    };

    open();
    return () => {
      this.closing = true;
      this.socket?.close();
    };
  }
}
