"""Turning an outcome into a runnable plan.

The planner is the only place a model is asked to decide *how*. Everything it
produces is validated against the tool registry before anything runs: unknown
tool names, bad parameter shapes and impossible steps are caught here, not
halfway through execution.

It is also where "ask as few questions as possible" is enforced. The prompt makes
clarification the exception and requires a stated assumption instead, so GARIS
proceeds on a sensible reading rather than stalling on a form.
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import ProviderError
from ..memory import MemoryService
from ..models import Job, Message, ModelRouter, Need, Privacy
from ..runtime import ToolRegistry
from . import reflex
from .goal import MAX_STEPS, Goal, Plan, PlanStep

SYSTEM_PROMPT = """\
Jesteś silnikiem planowania GARIS-a — osobistego agenta, który wykonuje pracę \
cyfrową za użytkownika na komputerze z Windows 11.

Twoje zadanie: zamienić CEL użytkownika w plan konkretnych wywołań narzędzi.

Zasady:
1. Użytkownik mówi, jaki chce rezultat. Ty decydujesz jak. Nie pytaj o metodę.
2. Używaj wyłącznie narzędzi z listy. Nie wymyślaj nazw ani parametrów.
3. Plan musi być wykonalny od początku do końca bez udziału użytkownika.
4. Najpierw rozpoznanie (odczyt stanu), potem zmiany. Nie zmieniaj czegoś,
   czego wcześniej nie sprawdziłeś.
5. Nie zadawaj pytań, jeśli da się przyjąć rozsądne założenie — zapisz je w
   "assumptions". Pytaj wyłącznie wtedy, gdy bez odpowiedzi zadanie zrobiłbyś
   źle albo nieodwracalnie (np. nie wiesz, który z dwóch serwerów).
6. Poświadczeń nie wpisuj w parametry. Używaj referencji "vault://nazwa".
7. Krok "expects" opisuje, jak wygląda dobry wynik — służy do weryfikacji.
8. Maksymalnie {max_steps} kroków. Krok, który tylko informuje użytkownika,
   jest zbędny — raport powstaje automatycznie na końcu.

Odpowiadaj WYŁĄCZNIE obiektem JSON o strukturze:
{{
  "summary": "jedno zdanie, co zrobisz",
  "assumptions": ["przyjęte założenia"],
  "steps": [
    {{"key": "krotki-identyfikator", "tool": "nazwa_narzedzia",
      "params": {{}}, "purpose": "po co ten krok", "expects": "dobry wynik",
      "optional": false}}
  ],
  "question": ""
}}
Jeśli naprawdę brakuje informacji: zwróć puste "steps" i jedno pytanie w
"question". Odpowiadaj w języku: {language}.
"""

REPAIR_PROMPT = """\
Wykonanie planu napotkało problem. Zaproponuj INNE podejście do tego samego celu.

Co się nie udało:
{failures}

Co już wykonano (nie powtarzaj tych kroków):
{done}

Zasady: inna metoda, inne narzędzie albo inna kolejność. Jeśli poprzednie
podejście nie ma sensu, zaproponuj zupełnie inne. Ten sam format JSON.
"""


class Planner:
    def __init__(
        self,
        router: ModelRouter,
        registry: ToolRegistry,
        *,
        memory: MemoryService | None = None,
        language: str = "pl",
    ) -> None:
        self.router = router
        self.registry = registry
        self.memory = memory
        self.language = language

    # ------------------------------------------------------------------ prompts

    def _system(self) -> str:
        return SYSTEM_PROMPT.format(max_steps=MAX_STEPS, language=self.language)

    def _catalog(self, goal: Goal) -> str:
        """The tool menu. Only what exists, only what runs on this host."""
        return json.dumps(self.registry.catalog_for_model(), ensure_ascii=False, indent=None)

    def _context_block(self, goal: Goal) -> str:
        parts: list[str] = []
        if goal.context:
            parts.append(goal.context)
        if self.memory is not None:
            remembered = self.memory.context_for(goal.text)
            if remembered:
                lines = [f"- [{r.kind.value}] {r.subject or '—'}: {r.content}"
                         for r in remembered]
                parts.append("Co wiem o użytkowniku i jego środowisku:\n" + "\n".join(lines))
        if goal.criteria:
            parts.append("Kryteria ukończenia:\n" + "\n".join(f"- {c}" for c in goal.criteria))
        if goal.target != "local":
            parts.append(f"Cel dotyczy urządzenia: {goal.target}")
        return "\n\n".join(parts)

    def _need(self, goal: Goal) -> Need:
        return Need(
            job=Job.PLAN,
            privacy=Privacy.PREFER_LOCAL if goal.private else Privacy.ANY,
        )

    # ------------------------------------------------------------------ planning

    async def make_plan(self, goal: Goal) -> Plan:
        # Questions this machine can answer about itself never reach a model.
        # "Ile miejsca zostało na dysku" is a `shutil.disk_usage` call; asking a
        # language model which tool to call for it costs a round trip, a key, and
        # — on a machine with no key at all — used to produce a guess dressed up
        # as a plan. See agent/reflex.py.
        instinct = reflex.plan_for(goal, available=self._runs_here)
        if instinct is not None:
            return instinct

        messages = [
            Message.system(self._system()),
            Message.system(f"Dostępne narzędzia (JSON):\n{self._catalog(goal)}"),
            Message.user(
                f"CEL: {goal.text}\n\n{self._context_block(goal)}".strip()
            ),
        ]
        plan = await self._ask(messages, goal)
        return self._sanitise(plan, goal)

    async def repair_plan(
        self,
        goal: Goal,
        *,
        failures: list[str],
        done: list[str],
    ) -> Plan:
        """Second (or third) opinion after a failure. Not a retry — a different route."""
        messages = [
            Message.system(self._system()),
            Message.system(f"Dostępne narzędzia (JSON):\n{self._catalog(goal)}"),
            Message.user(f"CEL: {goal.text}\n\n{self._context_block(goal)}".strip()),
            Message.user(
                REPAIR_PROMPT.format(
                    failures="\n".join(f"- {f}" for f in failures[-6:]) or "- brak szczegółów",
                    done="\n".join(f"- {d}" for d in done[-12:]) or "- nic",
                )
            ),
        ]
        plan = await self._ask(messages, goal)
        return self._sanitise(plan, goal)

    async def _ask(self, messages: list[Message], goal: Goal) -> Plan:
        completion = await self.router.complete(
            self._need(goal), messages, json_mode=True, temperature=0.1, max_tokens=3000
        )
        try:
            data = completion.json()
        except ProviderError:
            # One structured retry before giving up: cheaper than a failed task.
            retry = await self.router.complete(
                self._need(goal),
                [*messages, Message.user(
                    "Poprzednia odpowiedź nie była poprawnym JSON-em. "
                    "Zwróć wyłącznie obiekt JSON zgodny ze schematem."
                )],
                json_mode=True,
                temperature=0.0,
                max_tokens=3000,
            )
            data = retry.json()
        if not isinstance(data, dict):
            raise ProviderError(f"Plan nie jest obiektem JSON: {type(data).__name__}")
        return Plan.from_dict(data)

    def _runs_here(self, tool: str) -> bool:
        return self.registry.has(tool) and self.registry.get(tool).supported_here()

    # ---------------------------------------------------------------- validation

    def _sanitise(self, plan: Plan, goal: Goal) -> Plan:
        """Drop or annotate anything that cannot run, before it is scheduled.

        A model that invents ``install_everything`` should cost one planning round,
        not a half-executed plan.
        """
        kept: list[PlanStep] = []
        problems: list[str] = []

        for step in plan.steps:
            if not self.registry.has(step.tool):
                problems.append(f"nieznane narzędzie {step.tool!r} — krok pominięty")
                continue
            spec = self.registry.get(step.tool)
            if not spec.supported_here():
                problems.append(
                    f"{step.tool} nie działa na tym systemie — krok pominięty"
                )
                continue
            try:
                step.params = spec.validate(step.params)
            except Exception as exc:
                problems.append(f"{step.tool}: {exc}")
                continue
            kept.append(step)

        plan.steps = kept[:MAX_STEPS]
        if problems:
            plan.notes = "; ".join(problems)
        if not plan.steps and not plan.question:
            plan.question = (
                "Nie udało mi się zbudować wykonalnego planu dla tego celu. "
                "Doprecyzujesz, co dokładnie ma być zrobione?"
            )
        return plan.rekey()


def describe_plan(plan: Plan) -> str:
    """Human-readable plan, used by the developer view and by `garis do --dry-run`."""
    lines = [plan.summary or "(bez opisu)"]
    for index, step in enumerate(plan.steps, start=1):
        lines.append(f"{index}. {step.name} — {step.purpose or step.expects or ''}".rstrip())
    if plan.assumptions:
        lines.append("Założenia: " + "; ".join(plan.assumptions))
    if plan.notes:
        lines.append("Uwagi: " + plan.notes)
    return "\n".join(lines)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = ["REPAIR_PROMPT", "SYSTEM_PROMPT", "Planner", "describe_plan"]
