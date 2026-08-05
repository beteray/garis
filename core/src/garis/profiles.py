"""Which world this process is in, decided once and checked out loud.

``include_fake=True`` used to be the whole story, and it was a keyword argument
that anything could pass. That is a bad shape for a decision this consequential:
a stub provider answers every planning prompt with a keyword guess and every
verification prompt with ``{"ok": true, "note": "Sprawdzone."}``, so a process
that acquires one silently stops being able to tell the truth about its own work.
The failure is not that the stub exists — tests need it — but that nothing
declared, at startup, whether this process was allowed to have one.

So a profile is chosen explicitly, and PRODUCTION is validated rather than
trusted. Validation compares *identities* — which provider, which capability —
not counts, because "there are three providers" is true of both a healthy
install and one that quietly picked up a fake.

No environment variable appears anywhere in this file. An env var that can turn
PRODUCTION into FIXTURE is an env var that can turn a real machine into one that
pretends; the profile comes from the caller that built the process, and the
caller is the CLI, the API server or a test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ConfigError
from .kernel.contracts import RuntimeProfile

#: Provider class names that stand in for a real model. Matched by identity so a
#: renamed class fails the check loudly instead of slipping through a count.
DOUBLE_PROVIDERS = frozenset({"FakeProvider"})


@dataclass(frozen=True, slots=True)
class ProfileContents:
    """What a built process actually contains, by name. Inspectable on purpose:
    a test that asserts on identities catches a fake that a count would miss."""

    profile: RuntimeProfile
    providers: tuple[str, ...]
    capabilities: tuple[str, ...]
    doubles: tuple[str, ...]
    fixtures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "providers": list(self.providers),
            "capabilities": list(self.capabilities),
            "doubles": list(self.doubles),
            "fixtures": list(self.fixtures),
        }


def allows_doubles(profile: RuntimeProfile) -> bool:
    """Only TEST and FIXTURE may contain stand-ins. Never the other two."""
    return profile.allows_doubles


def check_requested_doubles(profile: RuntimeProfile, include_fake: bool) -> None:
    """Refuse the request before anything is built.

    Rejected under DEVELOPMENT as well as PRODUCTION: a development process that
    silently behaves like a fixture is how a stub's cheerful verdict gets
    mistaken for a real one during exactly the work that would have caught it.
    """
    if include_fake and not allows_doubles(profile):
        raise ConfigError(
            f"Profil {profile.value} nie dopuszcza atrap — "
            f"atrapy istnieją tylko w profilach test i fixture.",
            user_message="Ta wersja nie może działać na atrapach.",
        )


def inspect(
    profile: RuntimeProfile,
    *,
    providers: Sequence[Any] = (),
    capabilities: Sequence[Any] = (),
) -> ProfileContents:
    """Name everything this process ended up with."""
    provider_names = tuple(type(p).__name__ for p in providers)
    capability_ids = tuple(getattr(c, "id", str(c)) for c in capabilities)
    return ProfileContents(
        profile=profile,
        providers=provider_names,
        capabilities=capability_ids,
        doubles=tuple(n for n in provider_names if n in DOUBLE_PROVIDERS),
        fixtures=tuple(
            getattr(c, "id", str(c)) for c in capabilities if getattr(c, "fixture", False)
        ),
    )


def validate(contents: ProfileContents) -> ProfileContents:
    """Fail startup rather than run a production process on stand-ins."""
    if allows_doubles(contents.profile):
        return contents
    if contents.doubles:
        raise ConfigError(
            f"Profil {contents.profile.value} zawiera atrapy dostawców: "
            f"{', '.join(contents.doubles)}.",
            user_message="Nie uruchomię się na atrapie modelu.",
        )
    if contents.fixtures:
        raise ConfigError(
            f"Profil {contents.profile.value} zawiera zdolności testowe: "
            f"{', '.join(contents.fixtures)}.",
            user_message="Nie uruchomię się na testowych zdolnościach.",
        )
    return contents


__all__ = [
    "DOUBLE_PROVIDERS",
    "ProfileContents",
    "RuntimeProfile",
    "allows_doubles",
    "check_requested_doubles",
    "inspect",
    "validate",
]
