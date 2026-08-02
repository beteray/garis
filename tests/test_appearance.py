"""How the window looks is a setting, and settings must be true.

Two guarantees live here. First: a value the stylesheet cannot honour is
rejected at the door rather than saved, reported as saved, and quietly ignored —
that is the worst kind of settings screen. Second: upgrading from 0.1.0 or 0.1.1
keeps everything the user already configured and fills in only what is new.
"""

from __future__ import annotations

import json

import pytest

from garis.config import ACCENTS, Config, ProviderConfig
from garis.errors import ConfigError
from garis.settings import SettingsService

# ------------------------------------------------------------------ defaults


def test_a_fresh_install_looks_like_something_without_being_configured() -> None:
    look = Config().appearance
    assert look.theme == "system", "domyślnie idziemy za systemem, nie narzucamy"
    assert look.animation == "system"
    assert look.accent in ACCENTS
    assert look.glass == 1.0


def test_the_defaults_are_valid() -> None:
    """A default that fails its own validation is a bug shipped to everyone."""
    Config().validate()


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("appearance.theme", "purpurowy"),
        ("appearance.density", "luźno"),
        ("appearance.animation", "trochę"),
        ("appearance.orb", "kula"),
        ("appearance.navigation", "boczne"),
        ("appearance.accent", "różowawy"),
        ("appearance.accent", "#ggg"),
        ("appearance.glass", 4.0),
        ("appearance.text_scale", 3.0),
    ],
)
def test_a_value_the_interface_cannot_honour_is_refused(paths, path: str, value) -> None:
    config = Config()
    settings = SettingsService(config, paths)

    with pytest.raises(ConfigError):
        settings.apply({path: value})

    assert config.appearance == Config().appearance, "nic się nie zmieniło"
    assert not paths.config_file.exists(), "i nic się nie zapisało"


@pytest.mark.parametrize("accent", [*ACCENTS, "#ff8800", "#FF8800"])
def test_every_offered_accent_is_accepted(paths, accent: str) -> None:
    config = Config()
    SettingsService(config, paths).apply({"appearance.accent": accent})
    assert config.appearance.accent == accent


def test_a_bad_appearance_setting_does_not_take_a_good_one_with_it(paths) -> None:
    """Atomic means atomic: half a look is worse than the old one."""
    config = Config()
    settings = SettingsService(config, paths)

    with pytest.raises(ConfigError):
        settings.apply({"appearance.theme": "dark", "appearance.density": "bardzo"})

    assert config.appearance.theme == "system"


def test_a_hand_edited_file_gets_the_same_scrutiny(paths, bus) -> None:
    """There is no back door: the file on disk is not more trusted than the UI."""
    from garis.events import Topic

    config = Config()
    settings = SettingsService(config, paths, bus=bus)
    paths.ensure()
    paths.config_file.write_text(
        json.dumps({"appearance": {"theme": "neonowy"}}), "utf-8"
    )

    assert settings.reload() == ()
    assert config.appearance.theme == "system", "stare ustawienie zostaje w mocy"
    assert bus.recent(Topic.NOTICE), "i użytkownik się o tym dowiaduje"


# ----------------------------------------------------------------- migration


def test_upgrading_from_an_older_garis_keeps_everything_and_adds_the_rest(paths) -> None:
    """A 0.1.1 config has no ``appearance`` block at all. That is not an error."""
    paths.ensure()
    before = {
        "identity": {"name": "Michał", "address_as": "szefie", "onboarded": True},
        "models": {"providers": {"openai": {"key_ref": "vault://openai_api_key"}},
                   "privacy": "quality_first"},
        "autonomy": {"max_repair_attempts": 7},
        "tasks": {"max_parallel": 8},
    }
    paths.config_file.write_text(json.dumps(before, ensure_ascii=False), "utf-8")

    config = Config.load(paths)
    config.validate()

    # Nothing the user had chosen was lost.
    assert config.identity.name == "Michał"
    assert config.identity.address_as == "szefie"
    assert config.identity.onboarded is True
    assert config.models.privacy == "quality_first"
    assert config.models.providers["openai"] == ProviderConfig(
        key_ref="vault://openai_api_key"
    )
    assert config.autonomy.max_repair_attempts == 7
    assert config.tasks.max_parallel == 8

    # And the new section simply exists, at its defaults.
    assert config.appearance == Config().appearance


def test_a_config_from_a_newer_garis_still_opens(paths) -> None:
    """Downgrades happen. An unknown key is ignored, not fatal."""
    paths.ensure()
    paths.config_file.write_text(
        json.dumps({"appearance": {"theme": "dark", "hologram": True},
                    "czegos_takiego_nie_ma": 1}),
        "utf-8",
    )

    config = Config.load(paths)
    config.validate()
    assert config.appearance.theme == "dark"


async def test_the_look_travels_over_the_api_like_any_other_setting(garis) -> None:
    from garis.api.server import ApiServer

    server = ApiServer(garis, host="127.0.0.1", port=0)

    reply = await _patch(server, {"appearance.theme": "dark", "appearance.accent": "violet"})
    assert reply.status == 200
    assert set(reply.body["paths"]) == {"appearance.theme", "appearance.accent"}
    assert garis.config.appearance.theme == "dark"

    refused = await _patch(server, {"appearance.theme": "neonowy"})
    assert refused.status == 400
    assert garis.config.appearance.theme == "dark", "odrzucone nie znaczy nadpisane"

    # And it rides in the first frame, so the window never paints the old theme.
    state = await server._state(_request({}, token=server.token))
    assert state.body["appearance"]["accent"] == "violet"


async def _patch(server, changes: dict[str, object]):
    """Through the dispatcher, not the handler: the refusal is part of the route."""
    return await server._dispatch(_request(changes, token=server.token))


def _request(body: dict[str, object], *, token: str = ""):
    from garis.api.server import Request as ApiRequest

    return ApiRequest("PATCH", "/api/config", {},
                      {"authorization": f"Bearer {token}"},
                      json.dumps(body, ensure_ascii=False).encode())
