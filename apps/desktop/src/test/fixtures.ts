/**
 * Engine-shaped data for tests.
 *
 * Shapes are copied from what `core/src/garis/api/protocol.py` actually sends,
 * not from what would be convenient here — a fixture that drifts from the
 * protocol is a test that passes while the product is broken.
 */

import type { Approval, Device, EngineState, Memory, Provider, Secret, Task, Tool } from "../lib/api";
import { DEFAULT_APPEARANCE } from "../lib/appearance";
import { useStore } from "../lib/store";

export const provider = (over: Partial<Provider> = {}): Provider => ({
  name: "gemini",
  available: true,
  models: ["gemini-2.5-pro", "gemini-2.5-flash"],
  status: "online",
  reason: "Gotowy.",
  detail: "",
  checked_at: Date.now() / 1000 - 30,
  latency_ms: 214,
  retry_after: 0,
  failures: 0,
  ...over,
});

export const task = (over: Partial<Task> = {}): Task => ({
  id: "t1",
  goal: "sprawdź, ile miejsca zostało na dysku",
  state: "running",
  criteria: [],
  origin: "user",
  target: "local",
  created_at: Date.now() / 1000 - 60,
  updated_at: Date.now() / 1000 - 5,
  ...over,
});

export const approval = (over: Partial<Approval> = {}): Approval => ({
  id: "a1",
  task_id: "t1",
  tool: "install_package",
  prompt: "Zainstalować 7-Zip z repozytorium winget?",
  effects: ["install"],
  state: "pending",
  requested_at: Date.now() / 1000,
  ...over,
});

export const memory = (over: Partial<Memory> = {}): Memory => ({
  id: "m1",
  kind: "preference",
  subject: "styl",
  preview: "Wolę krótkie odpowiedzi",
  content: "Wolę krótkie odpowiedzi",
  tags: [],
  scope: "forever",
  pinned: false,
  created_at: Date.now() / 1000 - 900,
  updated_at: Date.now() / 1000 - 900,
  ...over,
});

export const secret = (over: Partial<Secret> = {}): Secret => ({
  name: "openai_api_key",
  kind: "api_key",
  note: "Klucz dostawcy modeli",
  ref: "vault://openai_api_key",
  used_at: Date.now() / 1000 - 300,
  use_count: 4,
  ...over,
});

export const device = (over: Partial<Device> = {}): Device => ({
  id: "d1",
  name: "produkcja",
  role: "server",
  address: "10.0.0.5",
  state: "online",
  seen_at: Date.now() / 1000 - 120,
  ...over,
});

export const tool = (over: Partial<Tool> = {}): Tool => ({
  name: "disk_usage",
  summary: "Sprawdza wolne miejsce na dysku",
  category: "system",
  effects: ["read"],
  available: true,
  reversible: true,
  ...over,
});

export const engine = (over: Partial<EngineState> = {}): EngineState => ({
  protocol: 1,
  version: "0.1.2",
  identity: { name: "Michał", address_as: "szefie", language: "pl", onboarded: true },
  persona: { preset: "assistant", brevity: 0.7, humor: 0.3 },
  voice: {
    enabled: true,
    wake_word: "garis",
    wake_word_enabled: true,
    push_to_talk: "ctrl+alt+space",
    voice_id: "pl_female_calm",
  },
  dev: { verbose: false, developer_mode: false },
  appearance: { ...DEFAULT_APPEARANCE },
  counts: { active_tasks: 0, pending_approvals: 0, memories: 0, secrets: 0 },
  models: {
    privacy: "balanced",
    allow_cloud: true,
    spend: 0,
    providers: [provider()],
    picks: { plan: "gemini-2.5-pro" },
  },
  notifications: {
    min_importance: 3,
    quiet_hours: { enabled: true, start: "23:00", end: "08:00", active_now: false },
    gaming: false,
    suppress_while_gaming: true,
    max_per_hour: 6,
  },
  tasks: { max_parallel: 4 },
  autostart: true,
  capabilities: {
    tools_total: 62,
    tools_available: 48,
    categories: { system: 12, files: 9 },
    unsupported_here: [],
    policy: {
      confirm_effects: ["payment", "publish"],
      download_notice: "2 GB",
      allow_admin_elevation: true,
      never: ["modyfikacja klucza głównego"],
    },
  },
  active_tasks: [],
  ...over,
});

/** A fake `GarisApi`. Only the methods a screen actually calls are defined, so
 *  a screen that starts calling something new fails loudly instead of silently
 *  rendering an empty state. */
export function fakeApi(over: Record<string, unknown> = {}) {
  return {
    say: async () => ({ kind: "chat", text: "Cześć.", intent: "greeting" }),
    submit: async () => ({ task_id: "t2" }),
    task: async (id: string) => task({ id }),
    patchConfig: async () => ({ paths: [] }),
    storeSecret: async () => ({ ref: "vault://x" }),
    deleteSecret: async () => ({ ok: true }),
    checkProviders: async () => ({ providers: [provider()] }),
    correctMemory: async () => memory(),
    forget: async () => ({ forgotten: true }),
    resolveApproval: async () => approval(),
    stopTask: async () => ({ stopped: true }),
    audit: async () => ({ audit: [] }),
    ...over,
  } as never;
}

/** Put the store into a known state. Returns nothing on purpose — tests read
 *  the screen, not the store. */
export function given(state: Partial<ReturnType<typeof useStore.getState>>): void {
  useStore.setState({
    engine: engine(),
    api: fakeApi(),
    tasks: [],
    approvals: [],
    chat: [],
    progress: [],
    memories: [],
    secrets: [],
    devices: [],
    tools: [],
    sending: false,
    view: "home",
    agentState: "idle",
    composerDraft: "",
    ...state,
  });
}

/** A paragraph of real Polish, for the wrapping and diacritic cases. */
export const LONG_POLISH =
  "Sprawdź, proszę, czy w katalogu z zeszłorocznymi fakturami znajdują się " +
  "dokumenty wystawione przez Zażółć Gęślą Jaźń spółka z ograniczoną " +
  "odpowiedzialnością, a jeśli tak — przenieś je do archiwum i zapisz " +
  "zestawienie w arkuszu, żebym mógł je później sprawdzić bez otwierania " +
  "każdego pliku osobno.";
