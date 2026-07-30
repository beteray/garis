"""Reasoning layer: providers, model catalogue, router.

Nothing above this package names a model. Ask for a :class:`Need`, get an answer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..config import Config, ProviderConfig
from ..events import EventBus
from ..net import HttpClient
from .base import (
    Capability,
    Completion,
    Job,
    Message,
    ModelSpec,
    Need,
    Privacy,
    Provider,
    Role,
    ToolCall,
    Usage,
    parse_json_lenient,
)
from .providers import (
    AnthropicProvider,
    FakeProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
)
from .router import Choice, ModelRouter

if TYPE_CHECKING:  # pragma: no cover
    from ..vault import Vault

# Provider name -> how to build it. Keys come from config.models.providers, so a
# user enabling "gemini" in onboarding is all it takes to light this up.
_CLOUD_BUILDERS: dict[str, Callable[..., Provider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}

# Common aliases so onboarding can accept what a person would actually say.
PROVIDER_ALIASES = {
    "claude": "anthropic",
    "chatgpt": "openai",
    "gpt": "openai",
    "google": "gemini",
    "local": "ollama",
}


def resolve_provider_name(name: str) -> str:
    cleaned = name.strip().lower()
    return PROVIDER_ALIASES.get(cleaned, cleaned)


def default_provider_config() -> dict[str, ProviderConfig]:
    """What a fresh install offers: the three cloud providers, keys in the vault."""
    return {
        "openai": ProviderConfig(key_ref="vault://openai_api_key"),
        "anthropic": ProviderConfig(key_ref="vault://anthropic_api_key"),
        "gemini": ProviderConfig(key_ref="vault://gemini_api_key"),
    }


def build_providers(
    config: Config,
    *,
    vault: Vault | None = None,
    http: HttpClient | None = None,
    include_fake: bool = False,
) -> list[Provider]:
    """Instantiate every configured provider whose key is actually present.

    A provider without a key is left out entirely rather than added and failed
    later — the router should never rank something that cannot answer.
    """
    http = http or HttpClient()
    providers: list[Provider] = []
    configured = config.models.providers or default_provider_config()

    for raw_name, settings in configured.items():
        name = resolve_provider_name(raw_name)
        if not settings.enabled:
            continue
        if name == "ollama":
            providers.append(OllamaProvider(http=http, base_url=settings.base_url))
            continue
        builder = _CLOUD_BUILDERS.get(name)
        if builder is None:
            continue
        key = _resolve_key(settings, vault)
        if not key:
            continue
        providers.append(builder(key, http=http, base_url=settings.base_url))

    if include_fake or not providers:
        # GARIS must still start and do simple work with no keys configured.
        providers.append(FakeProvider())
    return providers


def _resolve_key(settings: ProviderConfig, vault: Vault | None) -> str | None:
    ref = settings.key_ref
    if not ref:
        return None
    if not ref.startswith("vault://"):
        return ref  # a literal key, discouraged but supported
    if vault is None:
        return None
    return vault.get_optional(ref[len("vault://"):])


def build_router(
    config: Config,
    *,
    vault: Vault | None = None,
    http: HttpClient | None = None,
    bus: EventBus | None = None,
    include_fake: bool = False,
) -> ModelRouter:
    providers = build_providers(config, vault=vault, http=http, include_fake=include_fake)
    return ModelRouter(providers, config.models, bus=bus)


__all__ = [
    "PROVIDER_ALIASES",
    "AnthropicProvider",
    "Capability",
    "Choice",
    "Completion",
    "FakeProvider",
    "GeminiProvider",
    "Job",
    "Message",
    "ModelRouter",
    "ModelSpec",
    "Need",
    "OllamaProvider",
    "OpenAIProvider",
    "Privacy",
    "Provider",
    "Role",
    "ToolCall",
    "Usage",
    "build_providers",
    "build_router",
    "default_provider_config",
    "parse_json_lenient",
    "resolve_provider_name",
]
