"""What happens when a person says something to GARIS.

One door for everything typed or spoken, and the first thing behind it is a
decision: is this work, or is this talking? Until 0.1.2 there was no such
decision — every sentence went to the planner and became a durable task, so
"cześć" acquired an id, a plan, a failure and a permanent place in the task
list.

The order here is deliberate. Rules first, because a greeting must be answerable
on a machine with no API key at all. A blocked task's pending question next,
because "tak" means something different while GARIS is waiting for an answer.
The model last, and only for what the rules could not decide — and if there is no
model, the sentence still becomes a task rather than a silent shrug.

Facts, not fabrication: every answer this module composes is built from
something measurable — how many tasks are running, how many tools exist, what the
person is called. Nothing here invents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .agent.intent import Intent, Verdict, classify
from .events import EventBus, Topic
from .models import Job, Message, ModelRouter, Need
from .tasks import TaskRecord, TaskState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Config
    from .runtime import ToolRegistry
    from .tasks import TaskSupervisor

CHAT = "chat"
TASK = "task"
POINTER = "pointer"          # an answer that belongs to a question already asked


@dataclass(slots=True)
class Answer:
    """What GARIS says back, and whether anything was started because of it."""

    kind: str                       # chat | task | pointer
    text: str = ""                  # the sentence to show; empty for a bare task
    task: TaskRecord | None = None
    intent: Intent = Intent.UNSURE
    reason: str = ""                # diagnostic, developer mode only

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "kind": self.kind,
            "text": self.text,
            "intent": self.intent.value,
        }
        if self.task is not None:
            body["task_id"] = self.task.id
        return body


ROUTING_PROMPT = """\
Rozstrzygasz jedną rzecz: czy wiadomość od użytkownika to POLECENIE do wykonania \
na jego komputerze, czy zwykła rozmowa.

Zadanie powstaje tylko wtedy, gdy jest co śledzić — coś do zrobienia, sprawdzenia, \
znalezienia, zmienienia. Pytanie, na które umiesz odpowiedzieć zdaniem, zadaniem \
nie jest.

Odpowiedz wyłącznie obiektem JSON:
{"kind": "task"} — gdy trzeba coś wykonać
{"kind": "chat", "reply": "<jedno zdanie po polsku>"} — gdy wystarczy odpowiedź
"""


@dataclass
class Conversation:
    """The intake. Owns the decision; owns none of the machinery it calls."""

    tasks: TaskSupervisor
    router: ModelRouter
    registry: ToolRegistry
    config: Config
    bus: EventBus | None = None
    _last: Verdict | None = field(default=None, init=False, repr=False)

    async def say(
        self,
        text: str,
        *,
        origin: str = "user",
        target: str = "local",
    ) -> Answer:
        said = text.strip()
        if not said:
            return Answer(CHAT, "Nic nie powiedziałeś.", reason="pusta wiadomość")

        verdict = classify(said)
        self._last = verdict

        pointer = self._pending_question(verdict)
        if pointer is not None:
            return pointer

        if verdict.intent.is_conversation:
            return self._reply(verdict)

        if verdict.intent is Intent.UNSURE:
            decided = await self._ask_the_model(said)
            if decided is not None:
                return decided

        return self._start(verdict.remainder or said, origin=origin, target=target)

    # ------------------------------------------------------------- the answers

    def _reply(self, verdict: Verdict) -> Answer:
        text = {
            Intent.GREETING: self._greeting,
            Intent.SMALL_TALK: self._how_i_am,
            Intent.STATUS: self._how_i_am,
            Intent.THANKS: lambda: "Proszę.",
            Intent.FAREWELL: lambda: "Na razie. Zostaję w tle.",
            Intent.ACKNOWLEDGEMENT: lambda: "Jasne.",
            Intent.ABOUT_SELF: self._who_i_am,
            Intent.CAPABILITIES: self._what_i_can_do,
            Intent.ARITHMETIC: lambda: verdict.answer,
        }[verdict.intent]()
        self._speak(text)
        return Answer(CHAT, text, intent=verdict.intent, reason=verdict.reason)

    def _working_on(self) -> int:
        """Tasks actually moving. A blocked one is waiting for the user, not busy."""
        return sum(
            1 for task in self.tasks.list(active_only=True)
            if task.state is not TaskState.BLOCKED
        )

    def _greeting(self) -> str:
        opening = self.config.identity.address_as or self.config.identity.name
        hello = f"Cześć, {opening}." if opening else "Cześć."
        running = self._working_on()
        if running:
            return f"{hello} {self._running_sentence(running)}"
        return f"{hello} W czym mogę pomóc?"

    def _how_i_am(self) -> str:
        running = self._working_on()
        if not running:
            return "Działam. Nic teraz nie robię — powiedz, co mam zrobić."
        return f"Działam. {self._running_sentence(running)}"

    @staticmethod
    def _running_sentence(count: int) -> str:
        if count == 1:
            return "Mam teraz jedno zadanie w toku."
        # 2-4 → "zadania", 5+ → "zadań"; Polish plurals are not a suffix.
        form = "zadania" if 2 <= count % 10 <= 4 and count % 100 not in range(12, 15) else "zadań"
        return f"Mam teraz {count} {form} w toku."

    def _who_i_am(self) -> str:
        return (
            "Jestem GARIS — wykonuję za Ciebie pracę na tym komputerze. "
            "Mówisz, co ma być zrobione, a ja decyduję jak."
        )

    def _what_i_can_do(self) -> str:
        by_category = {
            name: tools
            for name, tools in self.registry.by_category().items()
            if any(tool.supported_here() for tool in tools)
        }
        count = sum(len(tools) for tools in by_category.values())
        if not count:
            return "Na tym komputerze nie mam jeszcze żadnego narzędzia."
        names = ", ".join(sorted(by_category)[:6])
        return f"Mam tu {count} narzędzi — między innymi: {names}. Powiedz, co zrobić."

    # ------------------------------------------------------- an open question

    def _pending_question(self, verdict: Verdict) -> Answer | None:
        """A short answer while GARIS is waiting for one must not be small talk.

        "tak" means yes to the question on screen, not a cheerful acknowledgement
        of nothing. GARIS cannot yet feed the answer back into the task, so it
        says where the answer belongs instead of quietly swallowing it.
        """
        if verdict.intent is not Intent.ACKNOWLEDGEMENT:
            return None
        waiting = [
            task
            for task in self.tasks.list(state=TaskState.BLOCKED, limit=5)
            if task.error
        ]
        if not waiting:
            return None
        task = waiting[0]
        text = f"Czeka pytanie do zadania „{task.goal}”: {task.error}"
        self._speak(text)
        return Answer(POINTER, text, task=task, intent=verdict.intent,
                      reason="zadanie czeka na odpowiedź")

    # ------------------------------------------------------------- the model

    async def _ask_the_model(self, said: str) -> Answer | None:
        """Let a model settle what the rules could not. Absence of one is not fatal."""
        if not self.router.has_any():
            return None
        try:
            completion = await self.router.complete(
                Need(job=Job.CLASSIFY),
                [Message.system(ROUTING_PROMPT), Message.user(said)],
                json_mode=True,
                max_tokens=200,
                temperature=0.0,
            )
        except Exception:
            # A router that cannot answer must not eat the request; the task
            # path below will fail loudly and say why.
            return None

        try:
            decision = completion.json()
        except Exception:
            return None
        if not isinstance(decision, dict) or decision.get("kind") != CHAT:
            return None
        reply = str(decision.get("reply", "")).strip()
        if not reply:
            return None
        self._speak(reply)
        return Answer(CHAT, reply, intent=Intent.SMALL_TALK, reason="rozstrzygnięte modelem")

    # -------------------------------------------------------------- the task

    def _start(self, goal: str, *, origin: str, target: str) -> Answer:
        record = self.tasks.submit(goal, origin=origin, target=target)
        return Answer(TASK, "", task=record, intent=Intent.TASK,
                      reason=(self._last.reason if self._last else ""))

    def _speak(self, text: str) -> None:
        if self.bus is not None:
            self.bus.emit(Topic.SPEAK, text=text, source="conversation")


__all__ = ["CHAT", "POINTER", "TASK", "Answer", "Conversation"]
