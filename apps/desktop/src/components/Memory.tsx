/**
 * What GARIS remembers, and — separately — what it keeps under lock.
 *
 * The separation is the point. A password is not a memory: it is never used to
 * reason, never sent to a model, never shown. The old screen put them on the
 * same page with the same card and printed `vault://openai_api_key` next to
 * each one, which taught the user that secrets are just another kind of note.
 *
 * Two things changed. Secrets get their own panel with their own explanation,
 * and they are named in human words — "Klucz OpenAI", not the storage key.
 * The reference is never rendered at all; a `vault://` path on screen is a
 * `vault://` path in a screenshot.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import type { Memory } from "../lib/api";
import { stagger, variants } from "../lib/motion";
import { useStore } from "../lib/store";
import { AnimatedNumber, Empty, Glass, Pill, Section, relativeTime } from "./ui";

/** Every kind `garis.memory.MemoryKind` defines, in the user's words. Missing
 *  one here would silently hide those entries from the filter. */
const KINDS: [string, string][] = [
  ["", "wszystko"],
  ["preference", "preferencje"],
  ["fact", "fakty"],
  ["project", "projekty"],
  ["person", "ludzie"],
  ["device", "urządzenia"],
  ["app", "programy"],
  ["solution", "rozwiązania"],
  ["routine", "rutyny"],
  ["style", "styl"],
  ["credential_hint", "gdzie co leży"],
];

const KIND_LABEL = Object.fromEntries(KINDS) as Record<string, string>;

/** Storage names → what a person calls the thing. Anything unknown falls back
 *  to a readable form of its own name rather than the raw key. */
const SECRET_NAMES: Record<string, string> = {
  openai_api_key: "Klucz OpenAI",
  anthropic_api_key: "Klucz Claude",
  gemini_api_key: "Klucz Gemini",
  api_token: "Token lokalnego API",
};

export const humanSecretName = (name: string): string =>
  SECRET_NAMES[name] ??
  name.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase());

function MemoryCard({ memory }: { memory: Memory }) {
  const api = useStore((s) => s.api);
  const refreshMemories = useStore((s) => s.refreshMemories);
  const developer = useStore((s) => s.engine?.dev.developer_mode ?? false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const save = async () => {
    if (!api) return;
    await api.correctMemory(memory.id, { content: draft });
    setEditing(false);
    await refreshMemories();
  };

  return (
    <motion.div layout variants={variants("row")} initial="hidden" animate="visible" exit="exit">
      <Glass className="glass--card memory">
        <div className="memory__meta tiny faint">
          <span>{KIND_LABEL[memory.kind] ?? memory.kind}</span>
          {memory.pinned && <span title="Nie zapomnę tego samo">★ na stałe</span>}
          {memory.scope && memory.scope !== "forever" && <span>· {memory.scope}</span>}
          <span>· zapisane {relativeTime(memory.created_at)}</span>
          {memory.updated_at !== memory.created_at && (
            <span>· poprawione {relativeTime(memory.updated_at)}</span>
          )}
          {developer && <code className="mono">{memory.id}</code>}
        </div>

        {editing ? (
          <textarea
            className="field selectable"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={3}
            autoFocus
            aria-label="Popraw wpis"
          />
        ) : (
          <p className="memory__text selectable">{memory.content ?? memory.preview}</p>
        )}

        {memory.tags.length > 0 && (
          <div className="memory__tags">
            {memory.tags.map((tag) => (
              <span key={tag} className="tiny effect-chip">
                {tag}
              </span>
            ))}
          </div>
        )}

        <div className="message__actions">
          {editing ? (
            <>
              <button className="btn tiny" onClick={() => void save()}>
                Zapisz
              </button>
              <button className="btn btn--quiet tiny" onClick={() => setEditing(false)}>
                Anuluj
              </button>
            </>
          ) : (
            <>
              <button
                className="btn btn--quiet tiny"
                onClick={() => {
                  setDraft(memory.content ?? memory.preview);
                  setEditing(true);
                }}
              >
                Popraw
              </button>
              <button
                className="btn btn--quiet tiny"
                onClick={async () => {
                  if (!api) return;
                  await api.forget(memory.id);
                  await refreshMemories();
                }}
              >
                Zapomnij
              </button>
            </>
          )}
        </div>
      </Glass>
    </motion.div>
  );
}

export function MemoryView() {
  const memories = useStore((s) => s.memories);
  const secrets = useStore((s) => s.secrets);
  const refreshMemories = useStore((s) => s.refreshMemories);
  const refreshSecrets = useStore((s) => s.refreshSecrets);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");

  useEffect(() => {
    void refreshMemories();
    void refreshSecrets();
  }, [refreshMemories, refreshSecrets]);

  useEffect(() => {
    const timer = setTimeout(() => void refreshMemories(query), 220);
    return () => clearTimeout(timer);
  }, [query, refreshMemories]);

  const shown = kind ? memories.filter((memory) => memory.kind === kind) : memories;
  const present = new Set(memories.map((memory) => memory.kind));

  return (
    <motion.div variants={stagger()} initial="hidden" animate="visible" className="screen">
      <Section
        title="Pamięć"
        action={
          <span className="tiny soft">
            <AnimatedNumber value={memories.length} /> wpisów
          </span>
        }
      >
        <p className="tiny soft">
          To Twoja własność. Możesz każdy wpis poprawić albo kazać mi go zapomnieć.
        </p>
        <input
          className="field"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Szukaj w pamięci…"
          aria-label="Szukaj w pamięci"
        />
        <div className="filters">
          {/* Only kinds that exist here: an empty category is a dead end. */}
          {KINDS.filter(([value]) => !value || present.has(value)).map(([value, label]) => (
            <Pill key={value} active={kind === value} onClick={() => setKind(value)}>
              {label}
            </Pill>
          ))}
        </div>

        {shown.length === 0 ? (
          <Empty
            icon="🧠"
            title={query ? "Nic takiego nie pamiętam." : "Nic tu jeszcze nie ma."}
            hint={query ? undefined : "Powiedz „zapamiętaj, że…”."}
          />
        ) : (
          <motion.div layout className="memory-list">
            <AnimatePresence initial={false}>
              {shown.map((memory) => (
                <MemoryCard key={memory.id} memory={memory} />
              ))}
            </AnimatePresence>
          </motion.div>
        )}
      </Section>

      <Section title="Sejf">
        <p className="tiny soft">
          Hasła, tokeny i klucze nie są pamięcią. Leżą tu zaszyfrowane osobno,
          nigdy nie trafiają do modelu i nigdy ich nie pokazuję — także sobie:
          podstawiam je dopiero w chwili użycia.
        </p>
        {secrets.length === 0 ? (
          <Empty icon="🔒" title="Sejf jest pusty." hint="Klucze modeli dodasz w Ustawieniach." />
        ) : (
          <motion.div variants={stagger()} className="memory-list">
            {secrets.map((secret) => (
              <motion.div key={secret.name} variants={variants("card")}>
                <Glass className="glass--card secret">
                  <span aria-hidden className="secret__icon">
                    🔑
                  </span>
                  <div className="secret__body">
                    <strong>{humanSecretName(secret.name)}</strong>
                    <span className="tiny faint">
                      {secret.note || "dostęp"} · użyte {secret.use_count}×
                      {secret.used_at ? ` · ostatnio ${relativeTime(secret.used_at)}` : ""}
                    </span>
                  </div>
                  {/* No value, no reference, no storage key. */}
                  <span className="tiny faint">ukryte</span>
                </Glass>
              </motion.div>
            ))}
          </motion.div>
        )}
      </Section>
    </motion.div>
  );
}
