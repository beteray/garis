/**
 * This computer, the machines GARIS knows about, and what it can actually do
 * on each.
 *
 * The claim being avoided here: "GARIS has 62 tools" is not the same as "GARIS
 * can do 62 things on this machine". The registry knows three things and only
 * three, so only three tiers are shown:
 *
 *   registered           — the tool exists in this build
 *   available here       — `ToolSpec.supported_here()`, i.e. the platform matches
 *   needs administrator  — the tool declares `Effect.ELEVATE`
 *
 * "Tested" and "verified on this device" are in the brief and have **no
 * backend representation**: nothing records that a tool ever ran successfully
 * on this machine. The audit log holds executions, but there is no contract
 * saying what counts as verified, so inventing a badge for it would be exactly
 * the boolean-shaped lie the provider work just removed. Recorded in
 * docs/STATE_MACHINES.md instead.
 *
 * Integrations that do not exist are not listed. There is no integration
 * registry in the engine.
 */

import { motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import type { Tool } from "../lib/api";
import { stagger, variants } from "../lib/motion";
import { useStore } from "../lib/store";
import { Empty, Glass, Section, relativeTime } from "./ui";

type Tier = "available" | "elevated" | "unavailable";

const TIER: Record<Tier, { tone: string; mark: string; label: string; note: string }> = {
  available: {
    tone: "var(--state-ok)",
    mark: "●",
    label: "Dostępne tutaj",
    note: "Działa na tym systemie.",
  },
  elevated: {
    tone: "var(--state-warn)",
    mark: "▲",
    label: "Wymaga administratora",
    note: "Zadziała, ale poproszę o podniesienie uprawnień.",
  },
  unavailable: {
    tone: "var(--ink-faint)",
    mark: "○",
    label: "Nie na tym systemie",
    note: "Narzędzie istnieje, ale nie na tej platformie.",
  },
};

const tierOf = (tool: Tool): Tier => {
  if (!tool.available) return "unavailable";
  return tool.effects.includes("elevate") ? "elevated" : "available";
};

/** Device state as the engine stores it (`tools/remote.py` writes these). */
const DEVICE_STATE: Record<string, { tone: string; label: string }> = {
  online: { tone: "var(--state-ok)", label: "Odpowiada" },
  offline: { tone: "var(--state-error)", label: "Nie odpowiada" },
  unknown: { tone: "var(--state-busy)", label: "Nie sprawdzałem" },
};

export function ConnectionsView() {
  const devices = useStore((s) => s.devices);
  const tasks = useStore((s) => s.tasks);
  const tools = useStore((s) => s.tools);
  const engine = useStore((s) => s.engine);
  const refreshDevices = useStore((s) => s.refreshDevices);
  const refreshTools = useStore((s) => s.refreshTools);
  const [openCategory, setOpenCategory] = useState<string | null>(null);

  useEffect(() => {
    void refreshDevices();
    void refreshTools();
  }, [refreshDevices, refreshTools]);

  const byTier = useMemo(() => {
    const groups: Record<Tier, Tool[]> = { available: [], elevated: [], unavailable: [] };
    for (const tool of tools) groups[tierOf(tool)].push(tool);
    return groups;
  }, [tools]);

  const byCategory = useMemo(() => {
    const groups = new Map<string, Tool[]>();
    for (const tool of tools) {
      const list = groups.get(tool.category) ?? [];
      list.push(tool);
      groups.set(tool.category, list);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b, "pl"));
  }, [tools]);

  const hereTasks = tasks.filter(
    (task) => task.target === "local" && (task.state === "running" || task.state === "pending"),
  );

  return (
    <motion.div variants={stagger()} initial="hidden" animate="visible" className="screen">
      <Section title="Ten komputer">
        <Glass className="glass--card device">
          <span aria-hidden className="device__icon">
            💻
          </span>
          <div className="device__body">
            <strong>GARIS Desktop</strong>
            <span className="tiny faint">
              {engine ? `silnik ${engine.version}` : "silnik nie odpowiada"}
              {hereTasks.length > 0 && ` · ${hereTasks.length} zadań w toku`}
            </span>
          </div>
          <span
            className="tiny"
            style={{ color: engine ? "var(--state-ok)" : "var(--state-error)" }}
          >
            {engine ? "● połączony" : "✕ brak silnika"}
          </span>
        </Glass>

        <div className="tiers">
          {(Object.keys(TIER) as Tier[]).map((tier) => (
            <div key={tier} className="tier">
              <span aria-hidden style={{ color: TIER[tier].tone }}>
                {TIER[tier].mark}
              </span>
              <div>
                <strong className="tiny">
                  {byTier[tier].length} · {TIER[tier].label}
                </strong>
                <span className="tiny faint">{TIER[tier].note}</span>
              </div>
            </div>
          ))}
        </div>
        <p className="tiny faint">
          To, że narzędzie tu jest, nie znaczy, że kiedykolwiek go użyłem na tym
          komputerze — tego jeszcze nie mierzę i nie będę udawać, że mierzę.
        </p>

        {byCategory.map(([category, list]) => (
          <div key={category} className="capability-group">
            <button
              className="btn btn--quiet tiny capability-group__head"
              aria-expanded={openCategory === category}
              onClick={() => setOpenCategory(openCategory === category ? null : category)}
            >
              <span>{category}</span>
              <span className="tiny faint">
                {list.filter((tool) => tool.available).length} z {list.length}
              </span>
            </button>
            {openCategory === category && (
              <div className="capability-list">
                {list.map((tool) => {
                  const tier = TIER[tierOf(tool)];
                  return (
                    <div key={tool.name} className="capability">
                      <span aria-hidden style={{ color: tier.tone }}>
                        {tier.mark}
                      </span>
                      <div>
                        <span>{tool.summary}</span>
                        <span className="tiny faint">{tier.label}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </Section>

      <Section title="Serwery i inne urządzenia">
        {devices.length === 0 ? (
          <Empty
            icon="🖥"
            title="Nie znam jeszcze żadnego serwera."
            hint="Powiedz: „dodaj serwer 10.0.0.5 i nazwij go produkcja”."
          />
        ) : (
          <motion.div variants={stagger()} className="memory-list">
            {devices.map((device) => {
              const state = DEVICE_STATE[device.state] ?? DEVICE_STATE.unknown;
              const busy = tasks.filter(
                (task) =>
                  task.target === device.name &&
                  (task.state === "running" || task.state === "pending"),
              ).length;
              return (
                <motion.div key={device.id} variants={variants("card")}>
                  <Glass className="glass--card device">
                    <span aria-hidden className="device__icon">
                      🖥
                    </span>
                    <div className="device__body">
                      <strong>{device.name}</strong>
                      <span className="tiny faint">
                        {device.address} · {device.role}
                        {busy > 0 && ` · ${busy} zadań`}
                      </span>
                      <span className="tiny faint">
                        ostatni kontakt {relativeTime(device.seen_at)}
                      </span>
                    </div>
                    <span className="tiny" style={{ color: state.tone }}>
                      {state.label}
                    </span>
                  </Glass>
                </motion.div>
              );
            })}
          </motion.div>
        )}
        <p className="tiny faint">
          Zdolności zdalnej maszyny sprawdzam dopiero przy zadaniu — nie trzymam
          ich listy, więc jej tu nie zmyślam.
        </p>
      </Section>
    </motion.div>
  );
}
