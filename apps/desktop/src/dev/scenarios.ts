/**
 * Deterministic visual states, for screenshots and nothing else.
 *
 * **This is not evidence about the backend.** Every scenario below is a
 * hand-written store snapshot. It proves that the window renders a given state
 * correctly; it proves nothing whatsoever about whether the engine can produce
 * that state, whether a provider authenticated, or whether a task ran. Anything
 * captured from these is a picture of the frontend, and must be described that
 * way.
 *
 * Four hard constraints, enforced by the gate in `fixture.ts` and by a test:
 *
 *   - never reachable in a production build;
 *   - never calls a provider or any network;
 *   - never creates a vault, a key, or a secret value;
 *   - never changes backend semantics — it only fills the store the engine
 *     would otherwise fill.
 *
 * The shapes come from `test/fixtures.ts`, which is itself shaped from
 * `core/src/garis/api/protocol.py`. A scenario that drifts from the protocol is
 * a screenshot of something the engine cannot send.
 */

import type { EngineState, Link } from "../lib/api";
import { DEFAULT_APPEARANCE } from "../lib/appearance";
import type { ChatEntry } from "../lib/store";
import type { Approval, Memory, Provider, Secret, Task, Tool } from "../lib/api";

const now = () => Date.UTC(2026, 7, 3, 12, 0, 0) / 1000;

// ------------------------------------------------------------------- pieces

const provider = (over: Partial<Provider> = {}): Provider => ({
  name: "gemini",
  available: true,
  models: ["gemini-2.5-pro", "gemini-2.5-flash"],
  status: "online",
  reason: "Gotowy.",
  detail: "",
  checked_at: now() - 40,
  latency_ms: 214,
  retry_after: 0,
  failures: 0,
  ...over,
});

const task = (over: Partial<Task> = {}): Task => ({
  id: "t1",
  goal: "sprawdź, ile miejsca zostało na dysku",
  state: "running",
  criteria: [],
  origin: "user",
  target: "local",
  created_at: now() - 90,
  updated_at: now() - 4,
  ...over,
});

const entry = (over: Partial<ChatEntry> & { id: string }): ChatEntry => ({
  text: "",
  at: now() - 60,
  kind: "chat",
  ...over,
});

const memory = (over: Partial<Memory> = {}): Memory => ({
  id: "m1",
  kind: "preference",
  subject: "styl",
  preview: "Wolę krótkie odpowiedzi, bez wstępów.",
  content: "Wolę krótkie odpowiedzi, bez wstępów.",
  tags: [],
  scope: "forever",
  pinned: false,
  created_at: now() - 90_000,
  updated_at: now() - 90_000,
  ...over,
});

const secret = (over: Partial<Secret> = {}): Secret => ({
  name: "openai_api_key",
  kind: "api_key",
  // Note what is absent: no value, and nothing resembling one.
  note: "Klucz dostawcy modeli",
  ref: "",
  used_at: now() - 400,
  use_count: 4,
  ...over,
});

const tool = (over: Partial<Tool> = {}): Tool => ({
  name: "disk_usage",
  summary: "Sprawdza wolne miejsce na dysku",
  category: "system",
  effects: ["read"],
  available: true,
  reversible: true,
  ...over,
});

const engine = (over: Partial<EngineState> = {}): EngineState => ({
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
  counts: { active_tasks: 0, pending_approvals: 0, memories: 2, secrets: 1 },
  models: {
    privacy: "balanced",
    allow_cloud: true,
    spend: 0.4213,
    providers: [provider()],
    picks: { plan: "gemini-2.5-pro", chat: "gemini-2.5-flash" },
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
    categories: { system: 12, pliki: 9, sieć: 7 },
    unsupported_here: [],
    policy: {
      confirm_effects: ["payment", "publish", "send_message", "credentials"],
      download_notice: "2 GB",
      allow_admin_elevation: true,
      never: ["modyfikacja klucza głównego, sejfu i dziennika audytu"],
    },
  },
  active_tasks: [],
  ...over,
});

/** Real Polish with every diacritic, long enough to test wrapping. */
const LONG =
  "Sprawdź, proszę, czy w katalogu z zeszłorocznymi fakturami znajdują się " +
  "dokumenty wystawione przez Zażółć Gęślą Jaźń spółka z ograniczoną " +
  "odpowiedzialnością, i przenieś je do archiwum.";

const CONVERSATION: ChatEntry[] = [
  entry({ id: "1", kind: "user", text: "cześć", at: now() - 900 }),
  entry({ id: "2", kind: "chat", text: "Cześć, szefie. W czym mogę pomóc?", at: now() - 895 }),
  entry({ id: "3", kind: "user", text: "ile to 17 * 3", at: now() - 700 }),
  entry({ id: "4", kind: "answer", text: "51", intent: "arithmetic", at: now() - 698 }),
  entry({ id: "5", kind: "user", text: LONG, at: now() - 400 }),
];

// ---------------------------------------------------------------- scenarios

/**
 * A step the screenshot runner performs after the page loads, so a surface that
 * only exists after a click — a confirmation, a provider's key field, a focus
 * ring — can be photographed. Selectors and key names only: this drives the
 * real interface, it does not stand in for it.
 */
export interface Act {
  /** Click the first element matching this CSS selector. */
  click?: string;
  /** Click the first element whose text matches (used where a class would not
   *  survive a rename but the words will). */
  text?: string;
  /** Press a key, e.g. "Tab". Repeated `times` if given. */
  press?: string;
  times?: number;
}

/** An extra capture of the same scenario at another size or in another look. */
export interface Shot {
  viewport: string;
  look: string;
}

export interface Scenario {
  /** What a screenshot of this is a picture of. */
  note: string;
  state: Record<string, unknown>;
  /** Interaction to perform before the picture is taken. */
  act?: Act[];
  /** Captures beyond the default size and look. */
  extra?: Shot[];
  /**
   * What the shell believes about its connection. Absent means "connected" —
   * which is a claim about the *window*, not about a running engine: under a
   * fixture nothing was discovered, no socket was opened, and no health check
   * was answered.
   */
  link?: Link;
}

export const SCENARIOS: Record<string, Scenario> = {
  idle: {
    note: "Start, nic w toku",
    state: { engine: engine(), chat: CONVERSATION, tasks: [], approvals: [] },
  },

  "task-running": {
    note: "Zadanie w toku, z krokami",
    state: {
      engine: engine(),
      chat: [...CONVERSATION, entry({ id: "6", kind: "task", taskId: "t1" })],
      tasks: [task()],
      progress: [
        { taskId: "t1", text: "disk_usage — odczyt wolnego miejsca", at: now() - 12 },
        { taskId: "t1", text: "list_dir — największe katalogi", at: now() - 5 },
      ],
      agentState: "working",
    },
  },

  "waiting-approval": {
    note: "Czeka na zgodę użytkownika",
    state: {
      engine: engine(),
      chat: [
        ...CONVERSATION,
        entry({
          id: "7",
          kind: "approval",
          taskId: "t1",
          approvalId: "a1",
          text: "Potrzebuję Twojej zgody.",
        }),
      ],
      tasks: [task({ state: "blocked", error: "" })],
      approvals: [
        {
          id: "a1",
          task_id: "t1",
          tool: "install_package",
          prompt: "Zainstalować 7-Zip z repozytorium winget?",
          effects: ["install", "elevate"],
          state: "pending",
          requested_at: now() - 20,
        } as Approval,
      ],
      agentState: "blocked",
    },
  },

  "waiting-input": {
    note: "Czeka na odpowiedź — z konkretnym pytaniem",
    state: {
      engine: engine(),
      chat: [
        ...CONVERSATION,
        entry({
          id: "8",
          kind: "clarification",
          taskId: "t1",
          text: "Który dysk mam sprawdzić — C czy D?",
        }),
      ],
      tasks: [task({ state: "blocked", error: "Który dysk mam sprawdzić — C czy D?" })],
      agentState: "blocked",
    },
  },

  success: {
    note: "Zrobione i sprawdzone",
    state: {
      engine: engine(),
      chat: [
        ...CONVERSATION,
        entry({
          id: "9",
          kind: "verified",
          taskId: "t1",
          text: "Na dysku C zostało 148 GB z 512 GB.",
        }),
      ],
      tasks: [
        task({
          state: "finished",
          report: { short: "Na dysku C zostało 148 GB z 512 GB.", verified: true },
        }),
      ],
      agentState: "done",
    },
  },

  partial: {
    note: "Zrobione częściowo — z tym, czego brakuje",
    state: {
      engine: engine(),
      chat: CONVERSATION,
      tasks: [
        task({
          state: "finished",
          goal: "przenieś zeszłoroczne faktury do archiwum",
          report: {
            short: "Przeniosłem 8 z 12 plików.",
            verified: true,
            problems: ["4 pliki są otwarte w innym programie"],
          },
        }),
      ],
    },
  },

  "engine-failure": {
    note: "Silnik nie odpowiada",
    state: { engine: null, chat: [], tasks: [], approvals: [] },
    link: { state: "engine-failed", detail: "Silnik nie odpowiedział w wyznaczonym czasie." },
  },

  "provider-missing": {
    note: "Dostawca nieskonfigurowany",
    state: {
      engine: engine({
        models: { ...engine().models, providers: [] },
      }),
      chat: CONVERSATION,
      secrets: [],
    },
  },

  "provider-invalid": {
    note: "Klucz odrzucony przez dostawcę",
    state: {
      engine: engine({
        models: {
          ...engine().models,
          providers: [
            provider({
              status: "invalid_key",
              available: false,
              reason: "Klucz odrzucony przez dostawcę.",
              detail: "HTTP 400: API key not valid",
            }),
          ],
        },
      }),
      chat: CONVERSATION,
      secrets: [secret({ name: "gemini_api_key" })],
    },
  },

  "provider-online": {
    note: "Dostawca odpowiada (fikstura, nie prawdziwe uwierzytelnienie)",
    state: {
      engine: engine(),
      chat: CONVERSATION,
      secrets: [secret()],
    },
  },

  memory: {
    note: "Pamięć i sejf",
    state: {
      engine: engine(),
      view: "memory",
      memories: [
        memory(),
        memory({
          id: "m2",
          kind: "project",
          preview: "Projekt „Zażółć” trzymam w D:\\praca\\zazolc.",
          content: "Projekt „Zażółć” trzymam w D:\\praca\\zazolc.",
          tags: ["praca"],
        }),
      ],
      secrets: [secret(), secret({ name: "gemini_api_key", use_count: 12 })],
    },
  },

  connections: {
    note: "Połączenia i zdolności",
    state: {
      engine: engine(),
      view: "connections",
      tools: [
        tool(),
        tool({ name: "install_msi", summary: "Instaluje program", effects: ["install", "elevate"] }),
        tool({ name: "mac_spotlight", summary: "Szuka przez Spotlight", available: false }),
      ],
      devices: [
        {
          id: "d1",
          name: "produkcja",
          role: "server",
          address: "10.0.0.5",
          state: "online",
          seen_at: now() - 120,
        },
      ],
    },
  },

  settings: {
    note: "Ustawienia — pierwsza kategoria",
    state: { engine: engine(), view: "settings" },
  },

  "settings-appearance": {
    note: "Ustawienia — wygląd, po zmianie kategorii",
    state: { engine: engine(), view: "settings" },
    act: [{ text: "Wygląd" }],
  },

  onboarding: {
    note: "Pierwsze uruchomienie",
    state: {
      engine: engine({
        identity: { name: "", address_as: "", language: "pl", onboarded: false },
      }),
      chat: [],
    },
    extra: [{ viewport: "1024x700", look: "dark" }],
  },

  "provider-key": {
    note: "Wpisywanie klucza dostawcy",
    state: { engine: engine(), view: "settings", secrets: [] },
    // The card shows its field straight away when there is no key stored, so
    // reaching the category is the whole interaction.
    act: [{ text: "Modele" }],
  },

  "confirm-forget": {
    note: "Potwierdzenie przed nieodwracalnym",
    state: {
      engine: engine(),
      view: "memory",
      memories: [memory()],
      secrets: [secret()],
    },
    act: [{ text: "Zapomnij" }],
    // The same question in the two looks most likely to break it, and in the
    // smallest window it has to survive.
    extra: [
      { viewport: "1440x900", look: "contrast" },
      { viewport: "1440x900", look: "still" },
      { viewport: "1024x700", look: "dark" },
    ],
  },

  "approval-sheet": {
    note: "Prośba o zgodę nad innym ekranem",
    state: {
      engine: engine(),
      view: "settings",
      tasks: [task({ state: "blocked" })],
      approvals: [
        {
          id: "a1",
          task_id: "t1",
          tool: "install_package",
          prompt: "Zainstalować 7-Zip z repozytorium winget?",
          effects: ["install", "elevate"],
          state: "pending",
          requested_at: now() - 20,
        } as Approval,
      ],
    },
  },

  "focus-ring": {
    note: "Widoczny pierścień ostrości — klawiatura",
    state: { engine: engine(), chat: CONVERSATION },
    act: [{ press: "Tab", times: 3 }],
    extra: [{ viewport: "1440x900", look: "contrast" }],
  },

  tasks: {
    note: "Ekran zadań, wszystkie grupy",
    state: {
      engine: engine(),
      view: "tasks",
      tasks: [
        task({ id: "a", state: "blocked", goal: "opublikuj raport", error: "Na którym koncie?" }),
        task({ id: "b", state: "running", goal: "sprawdź, ile miejsca zostało na dysku" }),
        task({
          id: "c",
          state: "finished",
          goal: "streszcz notatki ze spotkania",
          report: { short: "Gotowe, trzy akapity.", verified: true },
        }),
        task({
          id: "d",
          state: "failed",
          goal: "wyślij fakturę do księgowej",
          error: "Brak dostępu do skrzynki.",
        }),
      ],
    },
  },
};

export type ScenarioName = keyof typeof SCENARIOS;
