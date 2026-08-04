"""Falling over to another *provider*, not to another model from the same one.

The bug these guard against was in the attempt budget, not in the ranking:

    for choice in ranked[:max_attempts]:

``ranked`` is ordered by score, and the top three models for a job are routinely
two or three models from one vendor. Measured on the real catalogues with three
cloud keys and ``quality_first``, six of nine jobs put a second model from one
provider ahead of a provider that was never tried at all. When that vendor is the
one that is down, out of credit, or holding a rejected key, every retry lands on
the same broken door.

Everything here drives the **real** vendor adapters — real request building, real
response parsing, real error classification — with only the socket replaced. A
test that proves fallback works between two ``FakeProvider`` instances proves
nothing about OpenAI returning 401.
"""

from __future__ import annotations

import json

import pytest

from garis.config import ModelsConfig
from garis.errors import NoModelAvailable
from garis.models import Job, Message, ModelRouter, Need
from garis.models.providers.anthropic import AnthropicProvider
from garis.models.providers.fake import FakeProvider
from garis.models.providers.gemini import GeminiProvider
from garis.models.providers.openai import OpenAIProvider
from garis.net import FakeTransport, HttpClient, Request, Response

OPENAI = "https://api.openai.com/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
ANTHROPIC = "https://api.anthropic.com/v1"

# Bodies copied from the vendors' documented error shapes, not invented.
INVALID_OPENAI_KEY = json.dumps(
    {"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error",
               "code": "invalid_api_key"}}
)
OPENAI_QUOTA = json.dumps(
    {"error": {"message": "You exceeded your current quota, please check your plan and "
                          "billing details.", "type": "insufficient_quota",
               "code": "insufficient_quota"}}
)
GEMINI_INVALID_KEY = json.dumps(
    {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
               "status": "INVALID_ARGUMENT"}}
)
ANTHROPIC_OVERLOADED = json.dumps(
    {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
)


def openai_reply(text: str) -> str:
    return json.dumps({
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })


def gemini_reply(text: str) -> str:
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    })


def anthropic_reply(text: str) -> str:
    return json.dumps({
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


class Vendors:
    """A scripted internet: per-host status and body, and a record of who was called."""

    def __init__(self, **routes: tuple[int, str]) -> None:
        self.routes = routes
        self.called: list[str] = []

    def __call__(self, request: Request) -> Response:
        for host, (status, body) in self.routes.items():
            if self._matches(host, request.url):
                self.called.append(host)
                return Response(status, {}, body.encode(), request.url)
        raise AssertionError(f"nieoczekiwane wywołanie: {request.url}")

    @staticmethod
    def _matches(host: str, url: str) -> bool:
        return {"openai": "api.openai.com", "gemini": "generativelanguage",
                "anthropic": "api.anthropic.com"}[host] in url

    def http(self) -> HttpClient:
        return HttpClient(FakeTransport(self))

    def providers(self, *names: str) -> list[object]:
        http = self.http()
        builders = {"openai": OpenAIProvider, "gemini": GeminiProvider,
                    "anthropic": AnthropicProvider}
        return [builders[name]("test-key", http=http) for name in names]


# ---------------------------------------------------------------- the ordering


def test_every_provider_is_tried_before_any_provider_is_tried_twice() -> None:
    """The invariant, checked on the real catalogues across every job and profile."""
    providers = [OpenAIProvider("k"), AnthropicProvider("k"), GeminiProvider("k"),
                 FakeProvider()]
    for privacy in ("balanced", "prefer_local", "quality_first"):
        router = ModelRouter(providers, ModelsConfig(privacy=privacy))
        for job in Job:
            ranked = router.candidates(Need(job=job))
            if not ranked:
                continue
            distinct = {choice.provider.name for choice in ranked}
            opening = [c.provider.name for c in router.spread(ranked)[:len(distinct)]]
            assert set(opening) == distinct, (
                f"{privacy}/{job.value}: {opening} powtarza dostawcę, zanim spróbował "
                f"wszystkich z {sorted(distinct)}"
            )


def test_the_best_model_is_still_tried_first() -> None:
    """Spreading attempts must not cost quality on the happy path."""
    providers = [OpenAIProvider("k"), AnthropicProvider("k"), GeminiProvider("k")]
    router = ModelRouter(providers, ModelsConfig())
    for job in Job:
        ranked = router.candidates(Need(job=job))
        if ranked:
            assert router.spread(ranked)[0] is ranked[0]


# ------------------------------------------------------------- the five cases


async def test_openai_models_all_failing_still_reaches_gemini() -> None:
    """Case 1: the top-ranked OpenAI models fail; Gemini answers."""
    vendors = Vendors(openai=(500, "internal server error"),
                      gemini=(200, gemini_reply("gotowe")))
    router = ModelRouter(vendors.providers("openai", "gemini"), ModelsConfig())

    completion = await router.complete(Need(job=Job.PLAN), [Message.user("zaplanuj")])

    assert completion.provider == "gemini"
    assert completion.text == "gotowe"
    assert vendors.called.count("openai") == 1, (
        f"OpenAI wywołane {vendors.called.count('openai')} razy, zanim spróbowano Gemini "
        f"— kolejność prób: {vendors.called}"
    )


async def test_openai_and_gemini_failing_still_reaches_the_built_in_fallback() -> None:
    """Case 2: both clouds are down; the local fallback keeps GARIS working.

    This is the one the old code could not do: two cloud providers offer enough
    models between them to consume the whole attempt budget.
    """
    vendors = Vendors(openai=(503, "service unavailable"),
                      gemini=(503, "service unavailable"))
    providers = [*vendors.providers("openai", "gemini"), FakeProvider(["awaryjnie"])]
    router = ModelRouter(providers, ModelsConfig())

    completion = await router.complete(Need(job=Job.PLAN), [Message.user("zaplanuj")])

    assert completion.provider == "fake"
    assert set(vendors.called) == {"openai", "gemini"}


async def test_a_rejected_openai_key_does_not_stop_a_working_gemini_key() -> None:
    """Case 3: 401 from OpenAI, valid Gemini key."""
    vendors = Vendors(openai=(401, INVALID_OPENAI_KEY),
                      gemini=(200, gemini_reply("odpowiedź")))
    router = ModelRouter(vendors.providers("openai", "gemini"), ModelsConfig())

    completion = await router.complete(Need(job=Job.CHAT), [Message.user("cześć")])

    assert completion.provider == "gemini"


async def test_an_exhausted_openai_quota_does_not_stop_a_working_gemini_key() -> None:
    """Case 4: 429 insufficient_quota from OpenAI, valid Gemini key."""
    vendors = Vendors(openai=(429, OPENAI_QUOTA),
                      gemini=(200, gemini_reply("odpowiedź")))
    router = ModelRouter(vendors.providers("openai", "gemini"), ModelsConfig())

    completion = await router.complete(Need(job=Job.CHAT), [Message.user("cześć")])

    assert completion.provider == "gemini"


async def test_when_everything_fails_the_diagnosis_names_every_provider_tried() -> None:
    """Case 5: nothing works — say what was attempted and what each answered."""
    vendors = Vendors(openai=(401, INVALID_OPENAI_KEY),
                      gemini=(400, GEMINI_INVALID_KEY),
                      anthropic=(529, ANTHROPIC_OVERLOADED))
    router = ModelRouter(vendors.providers("openai", "gemini", "anthropic"), ModelsConfig())

    with pytest.raises(NoModelAvailable) as caught:
        await router.complete(Need(job=Job.PLAN), [Message.user("zaplanuj")])

    detail = str(caught.value)
    for name in ("openai", "gemini", "anthropic"):
        assert name in detail, f"diagnostyka nie wymienia {name}: {detail}"
        assert name in vendors.called, f"{name} nie został w ogóle wywołany"
    assert "401" in detail and "400" in detail and "529" in detail


# ------------------------------------------------------------------- health


async def test_a_provider_that_fails_a_real_call_is_marked_unusable() -> None:
    """The failure is a measurement: a rejected key must not stay 'usable'."""
    from garis.models.health import HealthMonitor, ProviderStatus

    vendors = Vendors(openai=(401, INVALID_OPENAI_KEY),
                      gemini=(200, gemini_reply("ok")))
    monitor = HealthMonitor(http=vendors.http())
    router = ModelRouter(vendors.providers("openai", "gemini"), ModelsConfig(),
                         health=monitor)

    # PLAN, not CHAT: OpenAI outranks Gemini for planning, so it is the one
    # actually called. A test whose premise is "the provider was tried" has to
    # pick a job where it is tried.
    await router.complete(Need(job=Job.PLAN), [Message.user("zaplanuj")])

    assert "openai" in vendors.called
    assert monitor.status("openai").status is ProviderStatus.INVALID_KEY
    assert not monitor.usable("openai")
    assert "gemini" in router.available_providers()


async def test_an_unhealthy_provider_is_skipped_on_the_next_call() -> None:
    """Having learned OpenAI is dead, the next task must not waste a call on it."""
    from garis.models.health import HealthMonitor

    vendors = Vendors(openai=(401, INVALID_OPENAI_KEY),
                      gemini=(200, gemini_reply("ok")))
    monitor = HealthMonitor(http=vendors.http())
    router = ModelRouter(vendors.providers("openai", "gemini"), ModelsConfig(),
                         health=monitor)

    await router.complete(Need(job=Job.PLAN), [Message.user("raz")])
    calls_after_first = vendors.called.count("openai")
    assert calls_after_first == 1
    await router.complete(Need(job=Job.PLAN), [Message.user("dwa")])

    assert vendors.called.count("openai") == calls_after_first, (
        "drugie zadanie znów zapukało do dostawcy, o którym już wiemy, że odrzuca klucz"
    )


# --------------------------------------------------------- the whole agent loop


PLAN = json.dumps(
    {
        "summary": "Sprawdzę zajętość dysku.",
        "steps": [{"key": "dysk", "tool": "disk_usage", "params": {},
                   "purpose": "odczyt wolnego miejsca", "expects": "dane odczytane"}],
        "question": "",
    },
    ensure_ascii=False,
)
VERDICT = json.dumps({"ok": True, "note": "Sprawdzone.", "unmet": []}, ensure_ascii=False)


def _openai_down_gemini_up(request: Request) -> Response:
    """OpenAI passes its health check and then fails every real call.

    Deliberately not "OpenAI is dead at probe time": that case is already handled
    a step earlier, by the router never ranking an unhealthy provider, and it
    would leave the in-call fallback path untested. A vendor whose key is fine but
    whose inference endpoint is throwing 500s is both realistic and the case that
    actually needs the fallback.
    """
    if "api.openai.com" in request.url:
        if request.url.endswith("/v1/models"):        # health probe: the key is fine
            return Response(200, {}, b'{"data": []}', request.url)
        return Response(500, {}, b"internal server error", request.url)
    if "generativelanguage" not in request.url:
        raise AssertionError(f"nieoczekiwane wywołanie: {request.url}")
    if ":generateContent" not in request.url:          # health probe
        return Response(200, {}, b'{"models": []}', request.url)
    prompt = (request.body or b"").decode("utf-8", errors="replace")
    if "Oceń, czy cel" in prompt:
        text = VERDICT
    elif "planowania GARIS" in prompt:
        text = PLAN
    else:
        text = "Przyjąłem."
    return Response(200, {}, gemini_reply(text).encode(), request.url)


async def test_a_whole_task_completes_on_the_second_provider_when_the_first_is_down(
    garis,
) -> None:
    """The real agent loop — plan, execute a tool, verify — across a provider failure.

    Not a router unit test and not a health check: a goal goes in, OpenAI refuses
    every call, Gemini plans and verifies, a real tool runs through the real
    runtime, and a finished task with a report comes out.
    """
    from garis.config import ProviderConfig
    from garis.tasks import TaskState

    garis.config.models.providers = {
        "openai": ProviderConfig(key_ref="vault://openai_api_key"),
        "gemini": ProviderConfig(key_ref="vault://gemini_api_key"),
    }
    garis.vault.set("openai_api_key", "sk-test")
    garis.vault.set("gemini_api_key", "test-key")
    transport = FakeTransport(_openai_down_gemini_up)
    garis.http._transport = transport
    await garis.providers.rebuild(reason="test")

    assert {"openai", "gemini"} <= {p.name for p in garis.router.providers}

    # Deliberately not a disk/memory/system question: those are answered by
    # agent/reflex.py without any provider at all, so they would prove nothing
    # about failover between two vendors.
    task = await garis.do("przejrzyj usługi systemowe i podsumuj stan", timeout=30)

    assert task.state is TaskState.FINISHED, task.error
    urls = [call.url for call in transport.calls]
    assert any("api.openai.com/v1/chat/completions" in url for url in urls), \
        "OpenAI nie został w ogóle spróbowany — test nie sprawdza tego, co miał"
    assert any(":generateContent" in url for url in urls), \
        "Gemini nie dokończył zadania"
    assert task.report, "zadanie skończone bez raportu"


async def test_the_provider_that_failed_mid_task_is_marked_and_skipped_afterwards(
    garis,
) -> None:
    """Having failed a real task, OpenAI must not be ranked for the next one."""
    from garis.config import ProviderConfig
    from garis.models.health import ProviderStatus
    from garis.tasks import TaskState

    garis.config.models.providers = {
        "openai": ProviderConfig(key_ref="vault://openai_api_key"),
        "gemini": ProviderConfig(key_ref="vault://gemini_api_key"),
    }
    garis.vault.set("openai_api_key", "sk-test")
    garis.vault.set("gemini_api_key", "test-key")
    transport = FakeTransport(_openai_down_gemini_up)
    garis.http._transport = transport
    await garis.providers.rebuild(reason="test")

    first = await garis.do("przejrzyj usługi systemowe i podsumuj stan", timeout=30)
    assert first.state is TaskState.FINISHED
    assert garis.providers.monitor.status("openai").status is ProviderStatus.OFFLINE

    openai_calls = sum(1 for c in transport.calls if "chat/completions" in c.url)
    second = await garis.do("przejrzyj usługi systemowe i podsumuj stan", timeout=30)

    assert second.state is TaskState.FINISHED
    assert sum(1 for c in transport.calls if "chat/completions" in c.url) == openai_calls, \
        "drugie zadanie znów pukało do dostawcy, o którym silnik już wie, że nie działa"
