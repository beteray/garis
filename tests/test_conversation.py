"""Talking to GARIS is not the same as giving it work.

The measured failure this file exists to prevent: everything typed into the
window became a durable task, so "cześć" was planned, failed, and stayed in the
list as `blocked` forever. A greeting with a progress bar.

The guarantee, stated once: **a task exists only when there is something to
track.** Everything below is that sentence, checked.
"""

from __future__ import annotations

import pytest

from garis.agent.intent import Intent, classify
from garis.conversation import CHAT, POINTER, TASK
from garis.tasks import TaskState

# --------------------------------------------------------------- the rules


@pytest.mark.parametrize(
    "said",
    ["cześć", "Cześć!", "hej", "siema", "Dzień dobry", "witaj", "Hej, GARIS"],
)
def test_a_greeting_is_a_greeting(said: str) -> None:
    assert classify(said).intent is Intent.GREETING


@pytest.mark.parametrize(
    ("said", "intent"),
    [
        ("dzięki", Intent.THANKS),
        ("Dziękuję!", Intent.THANKS),
        ("ok", Intent.ACKNOWLEDGEMENT),
        ("jasne", Intent.ACKNOWLEDGEMENT),
        ("na razie", Intent.FAREWELL),
        ("dobranoc", Intent.FAREWELL),
        ("jak się masz?", Intent.SMALL_TALK),
        ("co słychać", Intent.SMALL_TALK),
        ("kim jesteś?", Intent.ABOUT_SELF),
        ("co potrafisz?", Intent.CAPABILITIES),
        ("nad czym pracujesz", Intent.STATUS),
    ],
)
def test_small_talk_is_recognised_in_polish(said: str, intent: Intent) -> None:
    assert classify(said).intent is intent


@pytest.mark.parametrize(
    ("said", "answer"),
    [
        ("ile to 2+2", "4"),
        ("2+2", "4"),
        ("policz 15 * 3", "45"),
        ("100 / 8", "12,5"),
        ("ile to 2^10", "1024"),
        ("(3 + 4) * 2 =", "14"),
    ],
)
def test_arithmetic_is_answered_not_planned(said: str, answer: str) -> None:
    verdict = classify(said)
    assert verdict.intent is Intent.ARITHMETIC
    assert verdict.answer == answer


def test_dividing_by_zero_is_answered_rather_than_crashed() -> None:
    assert classify("ile to 1/0").answer == "Przez zero nie dzielimy."


def test_a_date_is_not_a_subtraction() -> None:
    """"2026-08-02" is a date. Answering "2016" would be worse than useless."""
    assert classify("2026-08-02").intent is not Intent.ARITHMETIC


@pytest.mark.parametrize(
    "said",
    [
        "sprawdź, ile miejsca zostało na dysku",
        "cześć, sprawdź ile miejsca na dysku",
        "Hej! Znajdź mi fakturę z marca",
        "posprzątaj pulpit",
        "wyślij raport na maila",
    ],
)
def test_a_greeting_in_front_of_work_is_still_work(said: str) -> None:
    assert classify(said).intent is Intent.TASK


def test_a_long_message_that_mentions_thanks_is_not_a_thank_you_note() -> None:
    said = "przygotuj podsumowanie kwartału i dopisz na końcu podziękowania dla zespołu"
    assert classify(said).intent is Intent.TASK


def test_what_the_rules_cannot_place_is_not_silently_called_chatter() -> None:
    """Refusing to guess is the point: the layer above decides, not this one."""
    assert classify("stolica Australii").intent is Intent.UNSURE


# ------------------------------------------------------- the whole engine


async def test_a_greeting_creates_no_task(garis) -> None:
    answer = await garis.say("cześć")

    assert answer.kind == CHAT
    assert answer.task is None
    assert answer.text.startswith("Cześć")
    assert garis.tasks.list() == [], "powitanie nie ma czego śledzić"


async def test_a_greeting_is_answered_with_no_provider_at_all(garis) -> None:
    """The failure was worst on a fresh machine: no key, so not even "cześć" worked."""
    garis.router.providers.clear()

    answer = await garis.say("dzień dobry")

    assert answer.kind == CHAT and answer.text
    assert not garis.tasks.list()


async def test_asking_how_it_is_going_answers_from_facts_not_from_a_model(garis) -> None:
    assert "Nic teraz nie robię" in (await garis.say("jak się masz?")).text

    garis.tasks.submit("policz coś długo")
    assert "jedno zadanie" in (await garis.say("jak się masz?")).text


def test_the_polish_plural_of_task_is_correct() -> None:
    from garis.conversation import Conversation

    say = Conversation._running_sentence
    assert "1 zadanie" not in say(1) and "jedno zadanie" in say(1)
    assert "2 zadania" in say(2)
    assert "5 zadań" in say(5)
    assert "12 zadań" in say(12), "dwanaście, nie dwanaście zadania"
    assert "22 zadania" in say(22)


async def test_asking_what_it_can_do_answers_from_the_registry(garis) -> None:
    answer = await garis.say("co potrafisz?")

    assert answer.kind == CHAT
    tools_here = sum(1 for t in garis.registry.all() if t.supported_here())
    assert str(tools_here) in answer.text, "liczba narzędzi to fakt, nie zgadywanka"


async def test_arithmetic_is_answered_by_the_engine_without_a_task(garis) -> None:
    answer = await garis.say("ile to 17 * 3")

    assert answer.kind == CHAT
    assert answer.text == "51"
    assert not garis.tasks.list()


async def test_a_real_instruction_still_becomes_a_task(garis) -> None:
    answer = await garis.say("sprawdź, ile miejsca zostało na dysku")

    assert answer.kind == TASK
    assert answer.task is not None
    assert garis.tasks.get(answer.task.id) is not None


async def test_a_yes_while_a_question_is_open_points_at_the_question(garis) -> None:
    """"tak" means yes to what is on screen. It must not be swallowed as chatter."""
    task = garis.tasks.submit("zrób coś, o co trzeba dopytać")
    garis.tasks.store.set_state(task.id, TaskState.BLOCKED, error="Który dysk mam sprawdzić?")

    answer = await garis.say("tak")

    assert answer.kind == POINTER
    assert "Który dysk mam sprawdzić?" in answer.text
    assert answer.task is not None and answer.task.id == task.id


async def test_an_acknowledgement_with_nothing_pending_is_just_an_acknowledgement(
    garis,
) -> None:
    answer = await garis.say("ok")

    assert answer.kind == CHAT
    assert not garis.tasks.list()


async def test_the_api_says_which_kind_of_answer_it_is(garis) -> None:
    """The window must not have to guess whether a task appeared."""
    chat = await _say(garis, "cześć")
    assert chat.status == 200
    assert chat.body["kind"] == CHAT
    assert "task_id" not in chat.body

    work = await _say(garis, "sprawdź, ile miejsca zostało na dysku")
    assert work.status == 201
    assert work.body["kind"] == TASK
    assert work.body["task_id"]


async def test_the_api_never_volunteers_its_reasoning(garis) -> None:
    """Why GARIS decided something is diagnostics, not conversation."""
    assert "why" not in (await _say(garis, "cześć")).body

    garis.config.dev.verbose = True
    assert (await _say(garis, "cześć")).body["why"]


async def test_an_empty_message_is_refused_without_creating_anything(garis) -> None:
    reply = await _say(garis, "   ")
    assert reply.status == 422
    assert not garis.tasks.list()


async def _say(garis, text: str):
    import json

    from garis.api.server import ApiServer
    from garis.api.server import Request as ApiRequest

    server = ApiServer(garis, host="127.0.0.1", port=0)
    body = json.dumps({"text": text}, ensure_ascii=False).encode()
    return await server._say(ApiRequest("POST", "/api/say", {}, {}, body))


async def test_what_is_said_reaches_the_interface(garis) -> None:
    from garis.events import Topic

    await garis.say("cześć")

    spoken = garis.bus.recent(Topic.SPEAK)
    assert spoken and spoken[-1].payload["text"].startswith("Cześć")
