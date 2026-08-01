"""The live provider set: rebuilt when configuration changes, checked on a schedule.

``build_providers`` reads the vault exactly once, which is why adding an API key
used to require restarting the engine — the user saw "key saved", submitted a
task and was told there was no model. This owns the part that was missing: when
a key or a setting moves, the providers are rebuilt in place and re-checked, and
the router the planner already holds starts using them.

Three things trigger a rebuild, all converging on one idempotent method:

* a credential written to the vault (``vault.changed``),
* a settings change touching ``models`` (``config.changed``),
* the hourly refresh.

What actually costs anything — the health probe — is skipped for providers whose
configuration did not move, so a rebuild storm is cheap.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from typing import TYPE_CHECKING, Any

from ..config import Config, ProviderConfig
from ..events import EventBus, Topic
from ..net import HttpClient
from .health import DEFAULT_TTL, HealthMonitor, ProviderHealth
from .router import ModelRouter

if TYPE_CHECKING:  # pragma: no cover
    from ..vault import Vault


class ProviderPool:
    """Keeps ``router.providers`` true to what the user has configured."""

    def __init__(
        self,
        router: ModelRouter,
        config: Config,
        *,
        vault: Vault | None = None,
        http: HttpClient | None = None,
        bus: EventBus | None = None,
        monitor: HealthMonitor | None = None,
        include_fake: bool = False,
        interval: float = DEFAULT_TTL,
    ) -> None:
        self.router = router
        self.config = config
        self.vault = vault
        self.http = http or HttpClient()
        self.bus = bus
        self.monitor = monitor or router.health or HealthMonitor(http=self.http, bus=bus)
        self.include_fake = include_fake
        self.interval = interval
        self.router.health = self.monitor
        self._signatures: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------ rebuild

    async def rebuild(self, *, reason: str = "", check: bool = True) -> list[ProviderHealth]:
        """Rebuild providers from the current config and vault, then re-check.

        Safe to call twice for the same change: identical configuration keeps its
        cached health, so the second call does no network work at all.
        """
        from . import build_providers

        async with self._lock:
            signatures = self._signatures_now()
            for name, signature in signatures.items():
                if self._signatures.get(name) != signature:
                    # Key or address moved: what we knew about it is worthless.
                    self.monitor.forget(name)
            for name in set(self._signatures) - set(signatures):
                self.monitor.forget(name)
            self._signatures = signatures

            providers = build_providers(
                self.config, vault=self.vault, http=self.http,
                include_fake=self.include_fake,
            )
            self.router.replace(providers)
            self._emit(reason, providers)

        # Probing a local daemon is how it learns which models are installed.
        for provider in self.router.providers:
            probe = getattr(provider, "probe", None)
            if probe is not None:
                with contextlib.suppress(Exception):
                    await probe()

        if not check:
            return []
        return await self.refresh()

    async def refresh(self, *, force: bool = False) -> list[ProviderHealth]:
        """Health-check every provider whose answer is missing or stale."""
        results = await self.monitor.check_all(self.router.providers, force=force)
        return [results[p.name] for p in self.router.providers if p.name in results]

    # ----------------------------------------------------------------- schedule

    async def run(self) -> None:
        """Re-check on a loop. The roadmap's hourly check, and nothing more."""
        while True:
            await asyncio.sleep(self.interval)
            with contextlib.suppress(Exception):
                await self.refresh()

    async def listen(self) -> None:
        """React to credentials and settings changing anywhere in this process."""
        if self.bus is None:
            return
        subscription = self.bus.subscribe(Topic.VAULT_CHANGED, Topic.CONFIG_CHANGED)
        try:
            async for event in subscription:
                if not self._concerns_models(event.topic, event.payload):
                    continue
                with contextlib.suppress(Exception):
                    await self.rebuild(reason=event.topic)
        finally:
            subscription.close()

    def start(self) -> None:
        """Begin the background loops. Idempotent."""
        if self._tasks:
            return
        self._tasks = [
            asyncio.ensure_future(self.run()),
            asyncio.ensure_future(self.listen()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []

    # ---------------------------------------------------------------- internals

    def _concerns_models(self, topic: str, payload: dict[str, Any]) -> bool:
        """Ignore changes that cannot possibly move a provider.

        Renaming the user or muting notifications must not tear down the model
        layer, and a secret unrelated to any provider must not either.
        """
        if topic == Topic.VAULT_CHANGED:
            name = str(payload.get("name", ""))
            return not name or name in self._referenced_secrets()
        changed = payload.get("paths") or ()
        return any(str(path).startswith("models") for path in changed)

    def _referenced_secrets(self) -> set[str]:
        from . import default_provider_config

        configured = self.config.models.providers or default_provider_config()
        return {
            settings.key_ref[len("vault://"):]
            for settings in configured.values()
            if settings.key_ref.startswith("vault://")
        }

    def _signatures_now(self) -> dict[str, str]:
        """Fingerprint each provider's configuration, without holding its key.

        A digest rather than the key itself: this dictionary lives for the whole
        process and would otherwise be a credential sitting in memory for no
        reason.
        """
        from . import default_provider_config, resolve_provider_name

        configured = self.config.models.providers or default_provider_config()
        out: dict[str, str] = {}
        for raw_name, settings in configured.items():
            name = resolve_provider_name(raw_name)
            out[name] = self._fingerprint(settings)
        return out

    def _fingerprint(self, settings: ProviderConfig) -> str:
        key = ""
        ref = settings.key_ref
        if ref.startswith("vault://") and self.vault is not None:
            key = self.vault.get_optional(ref[len("vault://"):]) or ""
        elif ref:
            key = ref
        material = f"{settings.enabled}|{settings.base_url}|{key}".encode()
        return hashlib.sha256(material).hexdigest()[:16]

    def _emit(self, reason: str, providers: list[Any]) -> None:
        if self.bus is None:
            return
        self.bus.emit(
            "models.reloaded",
            reason=reason,
            providers=[p.name for p in providers],
        )


__all__ = ["ProviderPool"]
