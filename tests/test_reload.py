"""Changing a setting or adding a key takes effect now, not after a restart.

The audit measured the failure this replaces: a key saved through the API stayed
invisible until the engine was restarted, while the interface cheerfully said
"key saved". True and useless at the same time. The end-to-end case at the bottom
of this file is the one the roadmap says would not pass before.
"""

from __future__ import annotations

import json

from garis.config import Config, ProviderConfig
from garis.events import Topic
from garis.models import ModelRouter
from garis.models.health import HealthMonitor
from garis.models.pool import ProviderPool
from garis.net import FakeTransport, HttpClient, Request, Response
from garis.settings import SettingsService


def ok_response(request: Request) -> Response:
    return Response(200, {}, b'{"data": []}', request.url)


# ------------------------------------------------------------------ adopting


def test_a_reloaded_config_keeps_the_objects_everyone_is_holding() -> None:
    """The policy engine holds ``config.autonomy``; rebinding it would orphan them."""
    live = Config()
    autonomy, models = live.autonomy, live.models

    incoming = Config()
    incoming.autonomy.max_repair_attempts = 7
    incoming.models.privacy = "quality_first"

    changed = live.adopt(incoming)

    assert live.autonomy is autonomy
    assert live.models is models
    assert autonomy.max_repair_attempts == 7
    assert models.privacy == "quality_first"
    assert set(changed) == {"autonomy.max_repair_attempts", "models.privacy"}


def test_adopting_an_identical_config_changes_nothing() -> None:
    """No change means no event, so a rewritten file does not wake the engine."""
    assert Config().adopt(Config()) == ()


def test_a_provider_added_or_removed_is_a_change_the_router_must_hear() -> None:
    live = Config()
    live.models.providers = {"openai": ProviderConfig(key_ref="vault://openai_api_key")}
    incoming = Config()
    incoming.models.providers = {"gemini": ProviderConfig(key_ref="vault://gemini_api_key")}

    changed = live.adopt(incoming)

    assert set(live.models.providers) == {"gemini"}
    assert "models.providers.openai" in changed
    assert "models.providers.gemini" in changed


# ------------------------------------------------------------------- settings


def test_changing_a_setting_saves_it_and_says_what_moved(paths, bus) -> None:
    config = Config()
    settings = SettingsService(config, paths, bus=bus)

    changed = settings.apply({"dev.verbose": True, "tasks.max_parallel": 8})

    assert set(changed) == {"dev.verbose", "tasks.max_parallel"}
    assert json.loads(paths.config_file.read_text("utf-8"))["dev"]["verbose"] is True
    event = bus.recent(Topic.CONFIG_CHANGED)[-1]
    assert set(event.payload["paths"]) == {"dev.verbose", "tasks.max_parallel"}


def test_a_request_that_is_partly_wrong_is_wholly_rejected(paths) -> None:
    """A settings screen must never leave half of what the user asked for applied."""
    config = Config()
    settings = SettingsService(config, paths)
    try:
        settings.apply({"dev.verbose": True, "nie.ma.takiego": 1})
    except Exception:
        pass
    else:
        raise AssertionError("nieznane ustawienie powinno zostać odrzucone")
    assert config.dev.verbose is False
    assert not paths.config_file.exists()


def test_a_file_edited_by_hand_is_picked_up(paths, bus) -> None:
    config = Config()
    settings = SettingsService(config, paths)

    edited = Config()
    edited.identity.name = "Michał"
    edited.save(paths)

    assert settings.reload() == ("identity.name",)
    assert config.identity.name == "Michał"
    assert settings.reload() == (), "drugi odczyt bez zmian nie jest zmianą"


def test_a_broken_config_file_does_not_take_the_engine_down(paths, bus) -> None:
    config = Config()
    settings = SettingsService(config, paths, bus=bus)
    paths.config_file.write_text("{ to nie jest JSON", "utf-8")

    assert settings.reload() == ()
    assert config.identity.language == "pl", "stare ustawienia zostają w mocy"
    assert any("ustawień" in e.payload.get("message", "") for e in bus.recent(Topic.NOTICE))


def test_our_own_save_does_not_look_like_an_outside_edit(paths) -> None:
    config = Config()
    settings = SettingsService(config, paths)
    settings.apply({"dev.verbose": True})
    assert settings.reload() == ()


# ----------------------------------------------------------------- the vault


def test_saving_a_credential_announces_itself(vault, bus) -> None:
    vault.bus = bus
    vault.set("openai_api_key", "sk-test")
    event = bus.recent(Topic.VAULT_CHANGED)[-1]
    assert event.payload == {"name": "openai_api_key", "action": "set"}


def test_the_announcement_never_carries_the_secret(vault, bus) -> None:
    vault.bus = bus
    vault.set("openai_api_key", "sk-bardzo-tajne")
    assert "sk-bardzo-tajne" not in json.dumps([e.to_dict() for e in bus.recent()])


async def test_a_key_written_to_the_vault_reaches_the_router(vault, bus) -> None:
    config = Config()
    config.models.providers = {"gemini": ProviderConfig(key_ref="vault://gemini_api_key")}
    http = HttpClient(FakeTransport(ok_response))
    monitor = HealthMonitor(http=http)
    router = ModelRouter([], config.models, health=monitor)
    pool = ProviderPool(router, config, vault=vault, http=http, bus=bus, monitor=monitor)

    await pool.rebuild(reason="start")
    # No key, no provider. There used to be a stub here "to keep GARIS alive",
    # and what it kept alive was a planner that guessed and a verifier that said
    # yes to everything.
    assert router.available_providers() == []

    vault.set("gemini_api_key", "test-key")
    await pool.rebuild(reason="vault")

    assert router.available_providers() == ["gemini"]


async def test_a_secret_no_provider_uses_does_not_disturb_the_model_layer(vault) -> None:
    config = Config()
    config.models.providers = {"gemini": ProviderConfig(key_ref="vault://gemini_api_key")}
    pool = ProviderPool(
        ModelRouter([], config.models), config, vault=vault,
        http=HttpClient(FakeTransport(ok_response)),
    )
    assert not pool._concerns_models(Topic.VAULT_CHANGED, {"name": "haslo_do_wifi"})
    assert pool._concerns_models(Topic.VAULT_CHANGED, {"name": "gemini_api_key"})


async def test_a_setting_unrelated_to_models_does_not_rebuild_them(vault) -> None:
    config = Config()
    pool = ProviderPool(
        ModelRouter([], config.models), config, vault=vault,
        http=HttpClient(FakeTransport(ok_response)),
    )
    assert not pool._concerns_models(Topic.CONFIG_CHANGED, {"paths": ["identity.name"]})
    assert pool._concerns_models(Topic.CONFIG_CHANGED, {"paths": ["models.privacy"]})


# ------------------------------------------------------------------ end to end


async def test_a_key_saved_through_the_api_works_without_a_restart(garis) -> None:
    """The roadmap's definition of M1 being done, as a test.

    Save a key, then have the same running engine plan and finish a task with the
    provider that key unlocked. Before this milestone the engine had to be
    restarted in between, and nothing said so.
    """
    from garis.api.server import ApiServer

    garis.config.models.providers = {
        "gemini": ProviderConfig(key_ref="vault://gemini_api_key")
    }
    garis.http._transport = FakeTransport(ok_response)  # nothing leaves the machine

    server = ApiServer(garis, host="127.0.0.1", port=0)
    await garis.providers.rebuild(reason="start")
    assert "gemini" not in garis.router.available_providers()

    # Exactly what the settings screen does: POST /api/vault.
    reply = await server._store_secret(_request({"name": "gemini_api_key",
                                                 "value": "test-key"}))

    assert reply.status == 201
    assert "gemini" in garis.router.available_providers()
    # The reply already carries the measured state, so the UI can show it.
    assert [p["status"] for p in reply.body["providers"] if p["name"] == "gemini"] == ["online"]

    state = await server._state(_request({}))
    gemini = next(p for p in state.body["models"]["providers"] if p["name"] == "gemini")
    assert gemini["available"] is True
    assert gemini["reason"] == "Gotowy."


async def test_an_invalid_key_says_so_within_seconds_of_being_saved(garis) -> None:
    """M4's definition of done: rejection is reported at save time, not mid-task."""
    from garis.api.server import ApiServer

    garis.config.models.providers = {
        "gemini": ProviderConfig(key_ref="vault://gemini_api_key")
    }
    garis.http._transport = FakeTransport(
        lambda request: Response(400, {}, b'{"error": {"message": "API key not valid"}}',
                                 request.url)
    )
    server = ApiServer(garis, host="127.0.0.1", port=0)

    reply = await server._store_secret(
        _request({"name": "gemini_api_key", "value": "CALKOWICIE-NIEPRAWIDLOWY-KLUCZ"})
    )

    gemini = next(p for p in reply.body["providers"] if p["name"] == "gemini")
    assert gemini["status"] == "invalid_key"
    assert gemini["reason"] == "Klucz odrzucony przez dostawcę."
    assert gemini["available"] is False
    assert "gemini" not in garis.router.available_providers()


async def test_a_task_submitted_after_saving_a_key_finishes_on_that_provider(garis) -> None:
    """M1, whole: save a key through the API and get a finished task out of the
    same running engine — no restart anywhere in the middle.

    The provider is real code all the way down to the HTTP call; only the socket
    is replaced, so this exercises the actual Gemini adapter, router and planner.
    """
    from garis.api.server import ApiServer
    from garis.tasks import TaskState

    garis.config.models.providers = {
        "gemini": ProviderConfig(key_ref="vault://gemini_api_key")
    }
    transport = FakeTransport(_gemini)
    garis.http._transport = transport
    server = ApiServer(garis, host="127.0.0.1", port=0)

    await server._store_secret(
        _request({"name": "gemini_api_key", "value": "test-key"})
    )
    task = await garis.do("przejrzyj usługi systemowe i podsumuj stan", timeout=30)

    assert task.state is TaskState.FINISHED, task.error
    assert any(":generateContent" in call.url for call in transport.calls), \
        "zadanie musiało pójść do dostawcy odblokowanego świeżo zapisanym kluczem"


PLAN = json.dumps(
    {
        "summary": "Sprawdzę zajętość dysku.",
        "steps": [
            {
                "key": "dysk",
                "tool": "disk_usage",
                "params": {},
                "purpose": "odczyt wolnego miejsca",
                "expects": "dane odczytane",
            }
        ],
        "question": "",
    },
    ensure_ascii=False,
)
VERDICT = json.dumps({"ok": True, "note": "Sprawdzone.", "unmet": []}, ensure_ascii=False)


def _gemini(request: Request) -> Response:
    """A Gemini-shaped answer, chosen by which of GARIS's prompts arrived.

    By role, never by call order: two tasks sharing a provider would otherwise
    collide, which is the trap ``models/providers/fake.py`` documents.
    """
    if ":generateContent" not in request.url:
        return Response(200, {}, b'{"models": []}', request.url)
    prompt = (request.body or b"").decode("utf-8", errors="replace")
    if "Oceń, czy cel" in prompt:
        text = VERDICT
    elif "planowania GARIS" in prompt:
        text = PLAN
    else:
        text = "Przyjąłem."
    payload = {"candidates": [{"content": {"parts": [{"text": text}]},
                               "finishReason": "STOP"}]}
    return Response(200, {}, json.dumps(payload).encode(), request.url)


def _request(body: dict[str, object]):
    from garis.api.server import Request as ApiRequest

    return ApiRequest("POST", "/api/vault", {}, {},
                      json.dumps(body, ensure_ascii=False).encode())
