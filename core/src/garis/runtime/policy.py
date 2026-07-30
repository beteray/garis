"""Policy: what runs silently, what needs a yes, what never runs.

The product rule this file encodes: safety works *underneath*. GARIS does not ask
permission to read a file, start a program or write to its workspace. It asks
before effects the user cannot take back or would not want done in their name —
money, publication, messages, credentials, permanent deletion — and it mentions
large downloads, real costs and serious system changes before committing to them.

Two invariants:

* **Persona never reaches this file.** Tone, humour and verbosity are style; they
  cannot loosen a gate. There is no code path from ``PersonaConfig`` to a verdict.
* **Effects come from the tool.** A model's description of its own action is not
  evidence about what the action does.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..config import AutonomyConfig
from ..paths import Paths
from .action import Action, Effect
from .registry import ToolSpec


class Decision(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: Decision
    rule: str
    prompt: str = ""       # question put to the user, Polish, one sentence
    reason: str = ""       # technical note for the audit log

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


ALLOW = Verdict(Decision.ALLOW, "default")

# Effects that always mean "a person must say yes", regardless of configuration.
# These are the spec's list; the config can add to it but not remove from it.
NON_NEGOTIABLE_CONFIRM: frozenset[Effect] = frozenset(
    {
        Effect.PAYMENT,
        Effect.PUBLISH,
        Effect.SEND_MESSAGE,
        Effect.CREDENTIALS,
        Effect.DELETE_PERMANENT,
    }
)

_GB = 1024**3


class PolicyEngine:
    def __init__(self, autonomy: AutonomyConfig, paths: Paths) -> None:
        self.autonomy = autonomy
        self.paths = paths

    # --------------------------------------------------------------- evaluate

    def evaluate(self, spec: ToolSpec, action: Action) -> Verdict:
        effects = spec.effects
        params = action.params

        deny = self._hard_deny(spec, action)
        if deny is not None:
            return deny

        gated = self._confirm_effects() & effects
        if gated:
            return Verdict(
                Decision.CONFIRM,
                f"effect:{sorted(gated)[0]}",
                self._effect_prompt(sorted(gated), spec, action),
                reason=f"skutki wymagające zgody: {sorted(e.value for e in gated)}",
            )

        if not spec.reversible or not action.reversible:
            return Verdict(
                Decision.CONFIRM,
                "irreversible",
                self._sentence(
                    action, "Ta operacja jest nieodwracalna", spec.danger_note
                ),
                reason="narzędzie zadeklarowane jako nieodwracalne",
            )

        est_bytes, est_cost = self._estimates(spec, action)

        if est_cost > self.autonomy.spend_notice_amount:
            return Verdict(
                Decision.CONFIRM,
                "cost",
                f"To zadanie będzie kosztować około {est_cost:.2f}. Kontynuować?",
                reason=f"szacowany koszt {est_cost}",
            )

        if est_bytes > self.autonomy.download_notice_bytes:
            return Verdict(
                Decision.CONFIRM,
                "download_size",
                f"Do wykonania zadania potrzebne jest pobranie około "
                f"{_human_bytes(est_bytes)}. Kontynuować?",
                reason=f"szacowane pobranie {est_bytes} B",
            )

        if Effect.INSTALL in effects:
            free = est_cost <= 0
            if not (self.autonomy.auto_install_free_software and spec.trusted_source
                    and free and spec.reversible):
                return Verdict(
                    Decision.CONFIRM,
                    "install",
                    self._sentence(
                        action,
                        f"Muszę zainstalować oprogramowanie ({_target_hint(params)})",
                        spec.danger_note,
                    ),
                    reason="instalacja poza regułą automatyczną",
                )

        if Effect.ELEVATE in effects and not self.autonomy.allow_admin_elevation:
            return Verdict(
                Decision.CONFIRM,
                "elevation",
                self._sentence(action, "Potrzebuję uprawnień administratora", ""),
                reason="podniesienie uprawnień wyłączone w konfiguracji",
            )

        return ALLOW

    # ------------------------------------------------------------- hard denies

    def _hard_deny(self, spec: ToolSpec, action: Action) -> Verdict | None:
        """Things GARIS refuses even with a yes, because they disable its own controls.

        This is not a general-purpose "dangerous command" blocklist — formatting a
        disk is the user's call and goes through confirmation like anything else.
        What is off-limits is GARIS quietly removing the parts of itself that keep
        it accountable: the master key, the vault, the audit trail, the gate list.
        """
        mutating = spec.effects & {
            Effect.WRITE,
            Effect.DELETE_PERMANENT,
            Effect.EXEC,
            Effect.SYSTEM_CONFIG,
        }
        if mutating and spec.category != "vault":
            for value in _string_values(action.params):
                target = _norm(value)
                for protected in (
                    self.paths.key_file,
                    self.paths.vault_db,
                    self.paths.state_db,
                ):
                    if target == _norm(str(protected)):
                        return Verdict(
                            Decision.DENY,
                            "guardrails",
                            reason=f"próba modyfikacji własnych zabezpieczeń: {value}",
                        )

        if spec.name == "config_set":
            key = str(action.params.get("key", ""))
            if key.startswith("autonomy.confirm_effects") or key.startswith("autonomy.allow"):
                return Verdict(
                    Decision.DENY,
                    "policy_self_edit",
                    reason="zmiana własnych reguł zgody może nastąpić tylko z interfejsu",
                )
        return None

    # ------------------------------------------------------------------ helpers

    def _confirm_effects(self) -> frozenset[Effect]:
        configured = set()
        for name in self.autonomy.confirm_effects:
            try:
                configured.add(Effect(name))
            except ValueError:
                continue  # unknown effect name in config: ignore, never fail closed-open
        return frozenset(configured) | NON_NEGOTIABLE_CONFIRM

    def _estimates(self, spec: ToolSpec, action: Action) -> tuple[int, float]:
        spec_bytes, spec_cost = spec.estimates(action.params)
        return (
            max(spec_bytes, action.estimated_bytes),
            max(spec_cost, action.estimated_cost),
        )

    def _effect_prompt(self, effects: list[Effect], spec: ToolSpec, action: Action) -> str:
        match effects[0]:
            case Effect.PAYMENT:
                head = "Mam dokonać płatności"
            case Effect.PUBLISH:
                head = "Mam opublikować to publicznie"
            case Effect.SEND_MESSAGE:
                head = "Mam wysłać wiadomość w Twoim imieniu"
            case Effect.CREDENTIALS:
                head = "Mam zmienić dane logowania"
            case Effect.DELETE_PERMANENT:
                head = "Mam trwale usunąć te dane"
            case _:
                head = f"Mam wykonać operację: {effects[0].label_pl}"
        return self._sentence(action, head, spec.danger_note)

    def _sentence(self, action: Action, head: str, note: str) -> str:
        detail = _target_hint(action.params)
        parts = [head]
        if detail:
            parts.append(f"({detail})")
        if action.intent:
            parts.append(f"— {action.intent}")
        text = " ".join(parts).rstrip(".")
        if note:
            text = f"{text}. {note.rstrip('.')}"
        return f"{text}. Potwierdzasz?"

    def describe(self) -> dict[str, Any]:
        """Human-readable summary of the active gates — used by ``garis doctor``."""
        return {
            "confirm_effects": sorted(e.value for e in self._confirm_effects()),
            "download_notice": _human_bytes(self.autonomy.download_notice_bytes),
            "spend_notice": self.autonomy.spend_notice_amount,
            "auto_install_free_software": self.autonomy.auto_install_free_software,
            "allow_admin_elevation": self.autonomy.allow_admin_elevation,
            "never": [
                "modyfikacja klucza głównego, sejfu i dziennika audytu",
                "zmiana własnych reguł zgody z poziomu narzędzia",
            ],
        }


def _string_values(params: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(params)
    return out


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _target_hint(params: dict[str, Any]) -> str:
    for key in ("to", "recipient", "path", "target", "package", "url", "name", "amount"):
        if params.get(key):
            return f"{key}: {params[key]}"
    return ""


def _human_bytes(size: int) -> str:
    if size >= _GB:
        return f"{size / _GB:.1f} GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} kB"
    return f"{size} B"


__all__ = [
    "ALLOW",
    "NON_NEGOTIABLE_CONFIRM",
    "Decision",
    "PolicyEngine",
    "Verdict",
]
