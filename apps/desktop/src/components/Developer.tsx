/**
 * The machinery, for people who want to see it.
 *
 * Everything here was previously in the primary navigation, which told the user
 * that the tool catalogue and the audit trail were things they were supposed to
 * operate. They are not. This route only exists when `dev.developer_mode` is on,
 * and the setting that turns it on says plainly what it is for.
 */

import { motion } from "framer-motion";
import { useEffect, useState } from "react";
import { TWEEN, stagger, variants } from "../lib/motion";
import { useStore } from "../lib/store";
import { Glass, Section } from "./ui";
import { ProviderRow, RecheckButton } from "./Providers";

export function DeveloperView() {
  const engine = useStore((s) => s.engine);
  const tools = useStore((s) => s.tools);
  const refreshTools = useStore((s) => s.refreshTools);
  const api = useStore((s) => s.api);
  const [audit, setAudit] = useState<Record<string, unknown>[]>([]);

  useEffect(() => {
    void refreshTools();
    if (api) void api.audit(40).then((response) => setAudit(response.audit)).catch(() => {});
  }, [refreshTools, api]);

  return (
    <motion.div variants={stagger()} initial="hidden" animate="visible" className="screen">
      <Section title="Silnik">
        <Glass className="glass--card diagnostic">
          <dl className="kv">
            <dt>Wersja</dt>
            <dd>{engine?.version ?? "—"}</dd>
            <dt>Protokół</dt>
            <dd>{engine?.protocol ?? "—"}</dd>
            <dt>Prywatność</dt>
            <dd>{engine?.models.privacy ?? "—"}</dd>
            <dt>Wydane</dt>
            <dd>{engine?.models.spend?.toFixed(4) ?? "0"}</dd>
          </dl>
        </Glass>
      </Section>

      <Section title="Modele" action={<RecheckButton />}>
        <div className="memory-list">
          {(engine?.models.providers ?? []).map((provider) => (
            <Glass key={provider.name} className="glass--card diagnostic">
              <strong>{provider.name}</strong>
              <ProviderRow provider={provider} />
              <div className="tiny faint">
                {provider.models.length} modeli
                {provider.latency_ms > 0 && ` · ${provider.latency_ms} ms`}
              </div>
              <code className="mono tiny faint selectable">
                {provider.models.slice(0, 8).join(", ")}
              </code>
            </Glass>
          ))}
        </div>
        <div className="kv tiny">
          {Object.entries(engine?.models.picks ?? {}).map(([job, model]) => (
            <span key={job} className="tiny faint">
              {job}: <code className="mono">{model}</code>
            </span>
          ))}
        </div>
      </Section>

      <Section title="Zgody">
        <Glass className="glass--card diagnostic">
          <div className="tiny soft">Pytam przed:</div>
          <div className="message__effects">
            {engine?.capabilities.policy.confirm_effects.map((effect) => (
              <span key={effect} className="tiny effect-chip effect-chip--danger">
                {effect}
              </span>
            ))}
          </div>
          <div className="tiny soft">Nigdy, nawet za zgodą:</div>
          {engine?.capabilities.policy.never.map((rule) => (
            <div key={rule} className="tiny faint">
              — {rule}
            </div>
          ))}
        </Glass>
      </Section>

      <Section title={`Narzędzia (${tools.filter((tool) => tool.available).length} z ${tools.length})`}>
        <div className="scroll tool-list">
          {tools.map((tool) => (
            <motion.div
              key={tool.name}
              variants={variants("card")}
              className="tool-row"
              style={{ opacity: tool.available ? 1 : 0.45 }}
            >
              <code className="mono">{tool.name}</code>
              <span className="tiny soft">{tool.summary}</span>
              <span className="tiny faint">{tool.effects.join(" ") || "—"}</span>
            </motion.div>
          ))}
        </div>
      </Section>

      <Section title="Dziennik">
        <div className="scroll tool-list">
          {audit.map((entry, index) => (
            <motion.div
              key={index}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ ...TWEEN.content, delay: Math.min(index * 0.012, 0.3) }}
              className="mono tiny faint"
            >
              {String(entry.tool)} → {String(entry.outcome)}{" "}
              {entry.rule ? `(${String(entry.rule)})` : ""}
            </motion.div>
          ))}
          {audit.length === 0 && <span className="tiny faint">Pusto.</span>}
        </div>
      </Section>
    </motion.div>
  );
}
