"""A provider's state is measured, not assumed.

The audit found a completely invalid key reporting itself as ready, because
"ready" meant "a key exists in the vault". These tests are about the difference:
GARIS asks the provider, believes the answer, says why, and refuses to route work
to something it knows is broken.
"""

from __future__ import annotations

import json

import pytest

from garis.config import Config, ModelsConfig, ProviderConfig
from garis.errors import NetworkTimeout, NetworkUnreachable, NoModelAvailable
from garis.models import Job, ModelRouter, Need
from garis.models.health import (
    HealthMonitor,
    ProviderHealth,
    ProviderStatus,
    classify_exception,
    classify_response,
)
from garis.models.pool import ProviderPool
from garis.models.providers.fake import FakeProvider
from garis.net import FakeTransport, HttpClient, Request, Response


def reply(status: int, body: str = "{}", headers: dict[str, str] | None = None) -> Response:
    return Response(status, headers or {}, body.encode(), "https://example.test/models")


def http_returning(response: Response | Exception) -> HttpClient:
    def handle(request: Request) -> Response:
        if isinstance(response, Exception):
            raise response
        return response

    return HttpClient(FakeTransport(handle))


class Probed(FakeProvider):
    """A provider with a health endpoint, so the monitor has something to call."""

    def __init__(self, name: str = "probed", *, key: str = "k", **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.name = name
        self._key = key

    def available(self) -> bool:
        return bool(self._key)

    def health_request(self) -> tuple[str, dict[str, str]] | None:
        if not self._key:
            return None
        return "https://example.test/models", {"Authorization": f"Bearer {self._key}"}


# ------------------------------------------------------------------ classifying


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, "{}", ProviderStatus.ONLINE),
        (401, "unauthorized", ProviderStatus.INVALID_KEY),
        (403, "forbidden", ProviderStatus.INVALID_KEY),
        (400, '{"error": {"message": "API key not valid"}}', ProviderStatus.INVALID_KEY),
        (400, '{"error": "malformed"}', ProviderStatus.INVALID_CONFIG),
        (404, "no such endpoint", ProviderStatus.INVALID_CONFIG),
        (408, "", ProviderStatus.TIMEOUT),
        (429, "slow down", ProviderStatus.RATE_LIMIT),
        (402, "payment required", ProviderStatus.RATE_LIMIT),
        (500, "boom", ProviderStatus.OFFLINE),
        (503, "maintenance", ProviderStatus.OFFLINE),
    ],
)
def test_every_answer_a_provider_can_give_maps_to_one_state(
    status: int, body: str, expected: ProviderStatus
) -> None:
    got, reason, _ = classify_response(status, body, {}, now=1000.0)
    assert got is expected
    assert reason, "każdy stan musi mieć zdanie dla użytkownika"


def test_a_provider_that_is_unreachable_is_told_apart_from_one_that_is_slow() -> None:
    offline, _, _ = classify_exception(NetworkUnreachable("brak trasy"), now=0.0)
    slow, _, _ = classify_exception(NetworkTimeout("za wolno"), now=0.0)
    assert offline is ProviderStatus.OFFLINE
    assert slow is ProviderStatus.TIMEOUT


def test_a_rate_limit_says_when_it_lifts() -> None:
    status, _, retry_after = classify_response(429, "", {"retry-after": "120"}, now=1000.0)
    assert status is ProviderStatus.RATE_LIMIT
    assert retry_after == 1120.0


def test_exhausted_credit_does_not_reopen_by_itself() -> None:
    """Money does not come back on a timer, so nothing may pretend it does."""
    _, _, retry_after = classify_response(402, "insufficient funds", {}, now=1000.0)
    assert retry_after == 0.0
    health = ProviderHealth(provider="x", status=ProviderStatus.RATE_LIMIT, retry_after=0.0)
    assert not health.usable_at(10_000_000.0)


# -------------------------------------------------------------------- measuring


async def test_a_rejected_key_is_reported_as_rejected_not_as_ready() -> None:
    monitor = HealthMonitor(http=http_returning(reply(401, "invalid api key")))
    health = await monitor.check(Probed())
    assert health.status is ProviderStatus.INVALID_KEY
    assert health.reason == "Klucz odrzucony przez dostawcę."
    assert not monitor.usable("probed")


async def test_a_working_key_is_online_and_timed() -> None:
    ticks = iter([100.0, 100.25, 100.25, 100.25])
    monitor = HealthMonitor(http=http_returning(reply(200)), clock=lambda: next(ticks))
    health = await monitor.check(Probed())
    assert health.status is ProviderStatus.ONLINE
    assert health.latency_ms == pytest.approx(250.0)


async def test_a_provider_with_no_key_needs_no_network_to_be_called_misconfigured() -> None:
    monitor = HealthMonitor(http=http_returning(reply(500)))
    health = await monitor.check(Probed(key=""))
    assert health.status is ProviderStatus.INVALID_CONFIG


async def test_an_unchecked_provider_is_unknown_and_still_usable() -> None:
    """Not having looked is not evidence of a fault."""
    monitor = HealthMonitor()
    assert monitor.status("openai").status is ProviderStatus.UNKNOWN
    assert monitor.usable("openai")


async def test_the_answer_is_cached_so_no_task_pays_for_a_check() -> None:
    transport = FakeTransport(lambda request: reply(200))
    monitor = HealthMonitor(http=HttpClient(transport))
    provider = Probed()
    await monitor.check(provider)
    await monitor.check(provider)
    assert len(transport.calls) == 1
    await monitor.check(provider, force=True)
    assert len(transport.calls) == 2


async def test_a_stale_answer_is_checked_again() -> None:
    transport = FakeTransport(lambda request: reply(200))
    clock = {"now": 1000.0}
    monitor = HealthMonitor(http=HttpClient(transport), ttl=60.0, clock=lambda: clock["now"])
    provider = Probed()
    await monitor.check(provider)
    clock["now"] += 3600.0
    await monitor.check(provider)
    assert len(transport.calls) == 2


async def test_a_failure_during_real_work_updates_health_immediately() -> None:
    """A key revoked at noon must not look healthy until the next hourly check."""
    monitor = HealthMonitor()
    monitor.note_failure("openai", NetworkUnreachable("brak sieci"))
    assert monitor.status("openai").status is ProviderStatus.OFFLINE
    assert not monitor.usable("openai")


async def test_only_a_real_change_of_state_is_announced(bus) -> None:
    monitor = HealthMonitor(http=http_returning(reply(200)), bus=bus, ttl=0.0)
    provider = Probed()
    await monitor.check(provider)
    await monitor.check(provider, force=True)
    events = bus.recent("provider.health")
    assert len(events) == 1, "godzinne potwierdzenie „nadal działa” nie jest wiadomością"


# ---------------------------------------------------------------------- routing


def test_the_router_refuses_a_provider_it_knows_is_broken() -> None:
    monitor = HealthMonitor()
    monitor.record(ProviderHealth(
        provider="probed", status=ProviderStatus.INVALID_KEY, checked_at=1.0
    ))
    router = ModelRouter([Probed()], ModelsConfig(), health=monitor)
    with pytest.raises(NoModelAvailable):
        router.select(Need(job=Job.CHAT))


def test_the_reason_a_provider_is_out_reaches_the_user() -> None:
    monitor = HealthMonitor()
    monitor.record(ProviderHealth(
        provider="probed",
        status=ProviderStatus.INVALID_KEY,
        reason="Klucz odrzucony przez dostawcę.",
        checked_at=1.0,
    ))
    router = ModelRouter([Probed()], ModelsConfig(), health=monitor)
    with pytest.raises(NoModelAvailable) as caught:
        router.select(Need(job=Job.CHAT))
    assert "klucz odrzucony przez dostawcę" in caught.value.explain().lower()


def test_a_rate_limited_provider_comes_back_when_the_window_passes() -> None:
    clock = {"now": 1000.0}
    monitor = HealthMonitor(clock=lambda: clock["now"])
    monitor.record(ProviderHealth(
        provider="probed", status=ProviderStatus.RATE_LIMIT,
        checked_at=1000.0, retry_after=1060.0,
    ))
    router = ModelRouter([Probed()], ModelsConfig(), health=monitor)
    with pytest.raises(NoModelAvailable):
        router.select(Need(job=Job.CHAT))
    clock["now"] = 1100.0
    assert router.select(Need(job=Job.CHAT)).provider.name == "probed"


def test_without_a_monitor_the_router_behaves_as_before() -> None:
    """Health is optional wiring; a router built without it still works."""
    router = ModelRouter([Probed()], ModelsConfig())
    assert router.select(Need(job=Job.CHAT)).provider.name == "probed"


def test_state_reaches_every_surface_through_describe() -> None:
    monitor = HealthMonitor()
    monitor.record(ProviderHealth(
        provider="probed", status=ProviderStatus.RATE_LIMIT,
        reason="Limit u dostawcy wyczerpany.", checked_at=5.0,
    ))
    router = ModelRouter([Probed()], ModelsConfig(), health=monitor)
    described = router.describe()["providers"][0]
    assert described["status"] == "rate_limit"
    assert described["reason"] == "Limit u dostawcy wyczerpany."
    assert described["available"] is False


# ------------------------------------------------------------------------- pool


async def test_the_pool_checks_every_provider_it_builds(vault) -> None:
    vault.set("gemini_api_key", "test-key")
    config = Config()
    config.models.providers = {"gemini": ProviderConfig(key_ref="vault://gemini_api_key")}

    http = http_returning(reply(401, "API key not valid"))
    monitor = HealthMonitor(http=http)
    router = ModelRouter([], config.models, health=monitor)
    pool = ProviderPool(router, config, vault=vault, http=http, monitor=monitor)

    await pool.rebuild(reason="test")
    assert [p.name for p in router.providers] == ["gemini"]
    assert monitor.status("gemini").status is ProviderStatus.INVALID_KEY
    assert not router.has_any()


async def test_a_changed_key_invalidates_what_we_knew(vault) -> None:
    vault.set("gemini_api_key", "bad-key")
    config = Config()
    config.models.providers = {"gemini": ProviderConfig(key_ref="vault://gemini_api_key")}

    answers = iter([reply(401, "API key not valid"), reply(200)])
    http = HttpClient(FakeTransport(lambda request: next(answers)))
    monitor = HealthMonitor(http=http)
    router = ModelRouter([], config.models, health=monitor)
    pool = ProviderPool(router, config, vault=vault, http=http, monitor=monitor)

    await pool.rebuild()
    assert monitor.status("gemini").status is ProviderStatus.INVALID_KEY

    vault.set("gemini_api_key", "good-key")
    await pool.rebuild()
    assert monitor.status("gemini").status is ProviderStatus.ONLINE


async def test_rebuilding_twice_for_one_change_costs_nothing(vault) -> None:
    vault.set("gemini_api_key", "test-key")
    config = Config()
    config.models.providers = {"gemini": ProviderConfig(key_ref="vault://gemini_api_key")}

    transport = FakeTransport(lambda request: reply(200))
    http = HttpClient(transport)
    monitor = HealthMonitor(http=http)
    router = ModelRouter([], config.models, health=monitor)
    pool = ProviderPool(router, config, vault=vault, http=http, monitor=monitor)

    await pool.rebuild()
    await pool.rebuild()
    assert len(transport.calls) == 1


async def test_a_voice_only_model_is_not_offered_for_text_chat() -> None:
    """Measured bug: a native-audio preview model won the ranking for chat."""
    from garis.models.providers.gemini import MODELS as GEMINI_MODELS
    from garis.models.providers.openai import MODELS as OPENAI_MODELS

    for spec in (*GEMINI_MODELS, *OPENAI_MODELS):
        if Job.VOICE in spec.jobs:
            assert spec.jobs == {Job.VOICE}, (
                f"{spec.name} deklaruje {sorted(spec.jobs)} — model rozmowy głosowej "
                "nie jest modelem do pisania"
            )


def test_health_survives_a_body_that_is_not_json() -> None:
    status, _, _ = classify_response(502, "<html>Bad Gateway</html>", {}, now=0.0)
    assert status is ProviderStatus.OFFLINE
    json.dumps(ProviderHealth(provider="x").to_dict())  # must stay serialisable


# ------------------------------------------------- what the screen may show


async def test_the_provider_view_carries_no_secret_anywhere(garis) -> None:
    """The status screen shows state, never the thing that produces it.

    A key value, or even the ``vault://`` reference that locates one, has no
    business on a settings screen — and this is the payload that screen renders.
    """
    import json

    from garis.api.server import ApiServer
    from garis.api.server import Request as ApiRequest
    from garis.config import ProviderConfig
    from garis.net import FakeTransport, Response

    garis.config.models.providers = {
        "gemini": ProviderConfig(key_ref="vault://gemini_api_key")
    }
    garis.http._transport = FakeTransport(
        lambda request: Response(200, {}, b'{"models": []}', request.url)
    )
    garis.vault.set("gemini_api_key", "sk-ABSOLUTNIE-TAJNY-KLUCZ")
    await garis.providers.rebuild(reason="test")

    server = ApiServer(garis, host="127.0.0.1", port=0)
    request = ApiRequest("GET", "/api/providers", {}, {}, b"")
    seen = json.dumps(
        [(await server._providers(request)).body,
         (await server._state(request)).body],
        ensure_ascii=False,
    )

    assert "sk-ABSOLUTNIE-TAJNY-KLUCZ" not in seen
    assert "vault://" not in seen
    assert "gemini_api_key" not in seen


async def test_every_provider_state_reaches_the_screen_with_a_polish_sentence(garis) -> None:
    """``status`` is for the machine, ``reason`` is for the person. Both, always."""
    from garis.api.server import ApiServer
    from garis.api.server import Request as ApiRequest
    from garis.config import ProviderConfig
    from garis.net import FakeTransport, Response

    garis.config.models.providers = {
        "gemini": ProviderConfig(key_ref="vault://gemini_api_key")
    }
    garis.http._transport = FakeTransport(
        lambda request: Response(429, {}, b'{"error": {"message": "quota"}}', request.url)
    )
    garis.vault.set("gemini_api_key", "test-key")
    await garis.providers.rebuild(reason="test")

    server = ApiServer(garis, host="127.0.0.1", port=0)
    reply = await server._providers(ApiRequest("GET", "/api/providers", {}, {}, b""))

    gemini = next(p for p in reply.body["providers"] if p["name"] == "gemini")
    assert gemini["status"] == "rate_limit"
    assert gemini["reason"] and gemini["reason"][0].isupper() and gemini["reason"].endswith(".")
    assert gemini["available"] is False
    # And the technical text is present but separate, for developer mode only.
    assert "detail" in gemini
