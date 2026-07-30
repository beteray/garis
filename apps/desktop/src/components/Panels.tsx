/** Memory, devices, subscriptions, diagnostics. */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { base, listItemVariants, quick, spring, staggerContainer, staggerItem } from "../lib/motion";
import { useStore } from "../lib/store";
import { AnimatedNumber, Empty, Glass, Pill, Section, relativeTime } from "./ui";

const KINDS = [
  ["", "wszystko"],
  ["preference", "preferencje"],
  ["project", "projekty"],
  ["device", "urządzenia"],
  ["person", "ludzie"],
  ["solution", "rozwiązania"],
  ["style", "styl"],
  ["credential_hint", "dostępy"],
] as const;

/**
 * What GARIS remembers — visible and editable, because it is the user's property.
 * Vault entries appear as *names only*, in their own clearly marked place.
 */
export function MemoryView() {
  const memories = useStore((s) => s.memories);
  const secrets = useStore((s) => s.secrets);
  const api = useStore((s) => s.api);
  const refreshMemories = useStore((s) => s.refreshMemories);
  const refreshSecrets = useStore((s) => s.refreshSecrets);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    void refreshMemories();
    void refreshSecrets();
  }, [refreshMemories, refreshSecrets]);

  useEffect(() => {
    const timer = setTimeout(() => void refreshMemories(query), 220);
    return () => clearTimeout(timer);
  }, [query, refreshMemories]);

  const shown = kind ? memories.filter((m) => m.kind === kind) : memories;

  const save = async (id: string) => {
    if (!api) return;
    await api.correctMemory(id, { content: draft });
    setEditing(null);
    await refreshMemories(query);
  };

  const forget = async (id: string) => {
    if (!api) return;
    await api.forget(id);
    await refreshMemories(query);
  };

  return (
    <motion.div variants={staggerContainer} initial="hidden" animate="visible"
                style={{ display: "grid", gap: 20 }}>
      <Section
        title="Pamięć"
        action={
          <span className="tiny soft">
            <AnimatedNumber value={memories.length} /> wpisów
          </span>
        }
      >
        <input
          className="field"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Szukaj w pamięci…"
        />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {KINDS.map(([value, label]) => (
            <Pill key={value} active={kind === value} onClick={() => setKind(value)}>
              {label}
            </Pill>
          ))}
        </div>

        {shown.length === 0 ? (
          <Empty icon="🧠" title="Nic tu jeszcze nie ma." hint="Powiedz „zapamiętaj to”." />
        ) : (
          <motion.div layout>
            <AnimatePresence initial={false}>
              {shown.map((memory) => (
                <motion.div
                  key={memory.id}
                  layout
                  variants={listItemVariants}
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                >
                  <Glass style={{ padding: 13, borderRadius: "var(--radius-card)" }}>
                    <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                      <div style={{ flex: 1, display: "grid", gap: 4 }}>
                        <div className="tiny faint" style={{ display: "flex", gap: 8 }}>
                          <span>{memory.kind}</span>
                          {memory.pinned && <span title="przypięte">★</span>}
                          <span>·</span>
                          <span>{relativeTime(memory.updated_at)}</span>
                        </div>
                        {editing === memory.id ? (
                          <textarea
                            className="field selectable"
                            value={draft}
                            onChange={(event) => setDraft(event.target.value)}
                            rows={3}
                            autoFocus
                          />
                        ) : (
                          <div className="selectable">{memory.content ?? memory.preview}</div>
                        )}
                      </div>

                      <div style={{ display: "flex", gap: 6 }}>
                        {editing === memory.id ? (
                          <>
                            <button className="btn tiny" onClick={() => void save(memory.id)}>
                              Zapisz
                            </button>
                            <button
                              className="btn btn--quiet tiny"
                              onClick={() => setEditing(null)}
                            >
                              Anuluj
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              className="btn btn--quiet tiny"
                              onClick={() => {
                                setEditing(memory.id);
                                setDraft(memory.content ?? "");
                              }}
                            >
                              Popraw
                            </button>
                            <button
                              className="btn btn--quiet tiny"
                              onClick={() => void forget(memory.id)}
                            >
                              Zapomnij
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  </Glass>
                </motion.div>
              ))}
            </AnimatePresence>
          </motion.div>
        )}
      </Section>

      <Section title="Sejf">
        <p className="tiny soft">
          Hasła, tokeny i klucze API nigdy nie są pamięcią. Leżą tutaj, zaszyfrowane
          osobno — widzisz nazwy, nigdy wartości.
        </p>
        {secrets.length === 0 ? (
          <Empty icon="🔒" title="Sejf jest pusty." />
        ) : (
          <motion.div variants={staggerContainer} style={{ display: "grid", gap: 8 }}>
            {secrets.map((secret) => (
              <motion.div key={secret.name} variants={staggerItem}>
                <Glass style={{ padding: 12, borderRadius: "var(--radius-card)" }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <span aria-hidden>🔑</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 550 }}>{secret.name}</div>
                      <div className="tiny faint">
                        {secret.note || secret.kind} · użyte {secret.use_count}×
                      </div>
                    </div>
                    <code className="mono faint">{secret.ref}</code>
                  </div>
                </Glass>
              </motion.div>
            ))}
          </motion.div>
        )}
      </Section>
    </motion.div>
  );
}

export function DevicesView() {
  const devices = useStore((s) => s.devices);
  const engine = useStore((s) => s.engine);
  const refreshDevices = useStore((s) => s.refreshDevices);

  useEffect(() => {
    void refreshDevices();
  }, [refreshDevices]);

  return (
    <motion.div variants={staggerContainer} initial="hidden" animate="visible"
                style={{ display: "grid", gap: 20 }}>
      <Section title="Ten komputer">
        <Glass style={{ padding: 16, borderRadius: "var(--radius-card)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <motion.span
              aria-hidden
              animate={{ opacity: [0.7, 1, 0.7] }}
              transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
              style={{ fontSize: 22 }}
            >
              💻
            </motion.span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600 }}>GARIS Desktop</div>
              <div className="tiny soft">
                {engine?.capabilities.tools_available ?? 0} z{" "}
                {engine?.capabilities.tools_total ?? 0} narzędzi dostępnych tutaj
              </div>
            </div>
            <span className="tiny" style={{ color: "hsl(var(--state-ok))" }}>
              połączony
            </span>
          </div>
        </Glass>
      </Section>

      <Section title="Serwery i inne urządzenia">
        {devices.length === 0 ? (
          <Empty
            icon="🖥"
            title="Nie znam jeszcze żadnego serwera."
            hint="Powiedz: „dodaj serwer 10.0.0.5 i nazwij go produkcja”."
          />
        ) : (
          <motion.div variants={staggerContainer} style={{ display: "grid", gap: 8 }}>
            {devices.map((device) => (
              <motion.div key={device.id} variants={staggerItem}>
                <Glass style={{ padding: 14, borderRadius: "var(--radius-card)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <span aria-hidden style={{ fontSize: 18 }}>🖥</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 550 }}>{device.name}</div>
                      <div className="tiny faint">
                        {device.address} · {device.role}
                      </div>
                    </div>
                    <span className="tiny soft">{relativeTime(device.seen_at)}</span>
                  </div>
                </Glass>
              </motion.div>
            ))}
          </motion.div>
        )}
      </Section>
    </motion.div>
  );
}

const INTERESTS = [
  "sztuczna inteligencja",
  "technologia",
  "muzyka",
  "sport",
  "gry",
  "filmy i seriale",
  "wiadomości",
  "moje projekty",
  "serwery i usługi",
];

/**
 * Subscriptions. The engine side lands in stage 6; the panel already states the
 * contract the user is agreeing to, because "what will actually reach me" is the
 * only question that matters here.
 */
export function SubscriptionsView() {
  const [chosen, setChosen] = useState<string[]>([]);

  const toggle = (interest: string) =>
    setChosen((current) =>
      current.includes(interest)
        ? current.filter((item) => item !== interest)
        : [...current, interest],
    );

  return (
    <motion.div variants={staggerContainer} initial="hidden" animate="visible"
                style={{ display: "grid", gap: 20 }}>
      <Section title="Czym się interesujesz">
        <p className="tiny soft">
          Filtruję mocno. Dostaniesz tylko to, co naprawdę może Cię zainteresować —
          nie każdą aktualizację.
        </p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {INTERESTS.map((interest) => (
            <Pill
              key={interest}
              active={chosen.includes(interest)}
              onClick={() => toggle(interest)}
            >
              {interest}
            </Pill>
          ))}
        </div>
        <AnimatePresence>
          {chosen.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 8, height: 0 }}
              animate={{ opacity: 1, y: 0, height: "auto" }}
              exit={{ opacity: 0, y: -8, height: 0 }}
              transition={spring}
              className="tiny soft"
            >
              Wybrane: {chosen.join(", ")}. Źródła i briefing poranny dołączam w
              kolejnym etapie.
            </motion.div>
          )}
        </AnimatePresence>
      </Section>

      <Section title="Kiedy mam milczeć">
        <Glass style={{ padding: 16, borderRadius: "var(--radius-card)", display: "grid", gap: 10 }}>
          <label style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span>Godziny ciszy</span>
            <span className="soft">23:00 – 08:00</span>
          </label>
          <label style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span>Podczas grania</span>
            <span className="soft">tylko rzeczy pilne</span>
          </label>
        </Glass>
      </Section>
    </motion.div>
  );
}

/** The optional view for people who want to see the machinery. */
export function DiagnosticsView() {
  const engine = useStore((s) => s.engine);
  const tools = useStore((s) => s.tools);
  const refreshTools = useStore((s) => s.refreshTools);
  const api = useStore((s) => s.api);
  const [audit, setAudit] = useState<Record<string, unknown>[]>([]);

  useEffect(() => {
    void refreshTools();
    if (api) void api.audit(40).then((response) => setAudit(response.audit));
  }, [refreshTools, api]);

  return (
    <motion.div variants={staggerContainer} initial="hidden" animate="visible"
                style={{ display: "grid", gap: 20 }}>
      <Section title="Zgody">
        <Glass style={{ padding: 16, borderRadius: "var(--radius-card)", display: "grid", gap: 8 }}>
          <div className="tiny soft">Pytam przed:</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {engine?.capabilities.policy.confirm_effects.map((effect) => (
              <span key={effect} className="tiny" style={{
                padding: "2px 9px",
                borderRadius: "var(--radius-pill)",
                border: "1px solid hsl(var(--state-error) / 0.32)",
                background: "hsl(var(--state-error) / 0.14)",
              }}>{effect}</span>
            ))}
          </div>
          <div className="tiny soft" style={{ marginTop: 6 }}>Nigdy, nawet za zgodą:</div>
          {engine?.capabilities.policy.never.map((rule) => (
            <div key={rule} className="tiny faint">— {rule}</div>
          ))}
        </Glass>
      </Section>

      <Section title={`Narzędzia (${tools.filter((t) => t.available).length})`}>
        <motion.div variants={staggerContainer} style={{ display: "grid", gap: 6 }}>
          {tools.slice(0, 60).map((tool) => (
            <motion.div
              key={tool.name}
              variants={staggerItem}
              whileHover={{ x: 3 }}
              transition={quick}
              style={{
                display: "flex",
                gap: 10,
                alignItems: "baseline",
                opacity: tool.available ? 1 : 0.4,
              }}
            >
              <code className="mono" style={{ minWidth: 190 }}>{tool.name}</code>
              <span className="tiny soft" style={{ flex: 1 }}>{tool.summary}</span>
            </motion.div>
          ))}
        </motion.div>
      </Section>

      <Section title="Dziennik">
        <div className="scroll" style={{ maxHeight: 280, display: "grid", gap: 4 }}>
          {audit.map((entry, index) => (
            <motion.div
              key={index}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ ...base, delay: Math.min(index * 0.012, 0.3) }}
              className="mono tiny faint"
            >
              {String(entry.tool)} → {String(entry.outcome)}{" "}
              {entry.rule ? `(${String(entry.rule)})` : ""}
            </motion.div>
          ))}
        </div>
      </Section>
    </motion.div>
  );
}
