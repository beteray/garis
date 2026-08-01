"""Provider health: what a provider's state actually is, and why.

Before this module a provider was ``available: true`` whenever a key existed in
the vault. That is a statement about the vault, not about the provider — a
completely invalid key reported as ready, and the first real task died with a 401
somewhere in the middle. Health is measured here instead of assumed, with the
cheapest call each vendor offers, and the answer is cached so no task pays for it.

Seven states, because seven are what a person can act on:

===============  ==========================================  ==================
Status           What happened                               Router uses it
===============  ==========================================  ==================
``ONLINE``       the provider answered                       yes
``OFFLINE``      nothing answered, or the provider is down   no
``INVALID_CONFIG`` no key, disabled, or a wrong address      no
``INVALID_KEY``  the provider rejected the credential        no
``TIMEOUT``      something is there but too slow             no
``RATE_LIMIT``   quota or credit exhausted                   not until it lifts
``UNKNOWN``      not checked yet                             yes
===============  ==========================================  ==================

``UNKNOWN`` is deliberately usable. "I have not looked" is not evidence of a
fault, and a machine that boots without a network would otherwise have no agent
at all. Guilt blocks; absence of proof does not.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ..errors import NetworkTimeout, NetworkUnreachable
from ..events import EventBus, Topic
from ..net import HttpClient

DEFAULT_TTL = 3600.0        # roadmap: re-check every hour
DEFAULT_TIMEOUT = 8.0       # a health check nobody waits for is not a health check
DEFAULT_COOLDOWN = 60.0     # assumed pause when a provider rate-limits without saying


class ProviderStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    INVALID_CONFIG = "invalid_config"
    INVALID_KEY = "invalid_key"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


# One sentence per state, Polish, no jargon — this is what a person reads.
REASONS: dict[ProviderStatus, str] = {
    ProviderStatus.ONLINE: "Gotowy.",
    ProviderStatus.OFFLINE: "Dostawca nie odpowiada.",
    ProviderStatus.INVALID_CONFIG: "Brak klucza.",
    ProviderStatus.INVALID_KEY: "Klucz odrzucony przez dostawcę.",
    ProviderStatus.TIMEOUT: "Dostawca nie odpowiedział na czas.",
    ProviderStatus.RATE_LIMIT: "Limit u dostawcy wyczerpany.",
    ProviderStatus.UNKNOWN: "Jeszcze nie sprawdzałem.",
}


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """One provider's measured state. Immutable: a check produces a new one."""

    provider: str
    status: ProviderStatus = ProviderStatus.UNKNOWN
    reason: str = ""                 # sentence for the user
    detail: str = ""                 # technical, developer mode only
    checked_at: float = 0.0
    latency_ms: float = 0.0
    retry_after: float = 0.0         # epoch second the provider becomes usable again
    failures: int = 0                # consecutive, so flapping is visible

    def usable_at(self, now: float) -> bool:
        if self.status in (ProviderStatus.ONLINE, ProviderStatus.UNKNOWN):
            return True
        if self.status is ProviderStatus.RATE_LIMIT:
            # Only a stated window reopens by itself. Exhausted credit has no
            # end time, so it stays shut until someone checks again.
            return bool(self.retry_after) and now >= self.retry_after
        return False

    @property
    def usable(self) -> bool:
        return self.usable_at(time.time())

    def sentence(self) -> str:
        return self.reason or REASONS[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "reason": self.sentence(),
            "detail": self.detail,
            "checked_at": self.checked_at,
            "latency_ms": round(self.latency_ms, 1),
            "retry_after": self.retry_after,
            "failures": self.failures,
            "usable": self.usable,
        }


@runtime_checkable
class Checkable(Protocol):
    """A provider that can be asked, cheaply, whether it would answer.

    Returns the request to make, or ``None`` when the provider needs no network
    (the built-in fake). Adapters contribute an address; they never decide what a
    response *means* — that lives in :func:`classify_response`, once, so a new
    vendor cannot invent its own idea of "invalid key".
    """

    def health_request(self) -> tuple[str, dict[str, str]] | None: ...


# --------------------------------------------------------------------- classify


def classify_response(
    status: int, body: str, headers: Mapping[str, str], *, now: float
) -> tuple[ProviderStatus, str, float]:
    """Map one HTTP answer to a state, a sentence and a retry moment."""
    lowered = body.lower()

    if 200 <= status < 300:
        return ProviderStatus.ONLINE, REASONS[ProviderStatus.ONLINE], 0.0

    if status in (401, 403):
        return ProviderStatus.INVALID_KEY, REASONS[ProviderStatus.INVALID_KEY], 0.0

    if status == 429:
        wait = _retry_after(headers.get("retry-after", ""))
        return (
            ProviderStatus.RATE_LIMIT,
            REASONS[ProviderStatus.RATE_LIMIT],
            now + wait if wait else now + DEFAULT_COOLDOWN,
        )

    if status == 402 or "insufficient" in lowered or "billing" in lowered:
        # No end time on purpose: money does not come back on a timer.
        return ProviderStatus.RATE_LIMIT, "Brak środków na koncie u dostawcy.", 0.0

    if status == 400:
        # Google answers a bad key with 400 API_KEY_INVALID rather than 401.
        if "api key" in lowered or "api_key" in lowered:
            return ProviderStatus.INVALID_KEY, REASONS[ProviderStatus.INVALID_KEY], 0.0
        return ProviderStatus.INVALID_CONFIG, "Dostawca odrzucił zapytanie.", 0.0

    if status == 404:
        return ProviderStatus.INVALID_CONFIG, "Zły adres dostawcy.", 0.0

    if status == 408:
        return ProviderStatus.TIMEOUT, REASONS[ProviderStatus.TIMEOUT], 0.0

    return ProviderStatus.OFFLINE, REASONS[ProviderStatus.OFFLINE], 0.0


def classify_exception(exc: BaseException, *, now: float) -> tuple[ProviderStatus, str, float]:
    """Map a failed call — transport or provider — to a state."""
    if isinstance(exc, (NetworkTimeout, TimeoutError)):
        return ProviderStatus.TIMEOUT, REASONS[ProviderStatus.TIMEOUT], 0.0
    if isinstance(exc, NetworkUnreachable):
        return ProviderStatus.OFFLINE, REASONS[ProviderStatus.OFFLINE], 0.0
    status = getattr(exc, "status", 0)
    if isinstance(status, int) and status:
        return classify_response(status, str(exc), {}, now=now)
    return ProviderStatus.OFFLINE, REASONS[ProviderStatus.OFFLINE], 0.0


def _retry_after(raw: str) -> float:
    """Seconds from a ``Retry-After`` header. HTTP-date form falls back to 0."""
    value = raw.strip()
    if value.isdigit():
        return float(value)
    return 0.0


# ---------------------------------------------------------------------- monitor


@dataclass(slots=True)
class HealthMonitor:
    """Cached, measured state for every provider GARIS knows about.

    Cached because a health check before every task would be a tax on every task;
    measured because the alternative is what the audit found. Nothing here calls a
    model: probes hit a free listing endpoint, so checking costs nothing.
    """

    http: HttpClient = field(default_factory=HttpClient)
    bus: EventBus | None = None
    ttl: float = DEFAULT_TTL
    timeout: float = DEFAULT_TIMEOUT
    clock: Any = time.time
    _states: dict[str, ProviderHealth] = field(default_factory=dict)

    # --- reading ---

    def status(self, name: str) -> ProviderHealth:
        return self._states.get(name) or ProviderHealth(provider=name)

    def snapshot(self) -> dict[str, ProviderHealth]:
        return dict(self._states)

    def usable(self, name: str) -> bool:
        return self.status(name).usable_at(self.clock())

    def stale(self, name: str) -> bool:
        health = self.status(name)
        if health.status is ProviderStatus.UNKNOWN:
            return True
        return (self.clock() - health.checked_at) >= self.ttl

    # --- writing ---

    def forget(self, name: str | None = None) -> None:
        """Drop what we knew. Called when a key or address changed underneath us."""
        if name is None:
            self._states.clear()
        else:
            self._states.pop(name, None)

    def note_failure(self, name: str, exc: BaseException) -> None:
        """Record a failure seen during real work.

        A key revoked at noon should not look healthy until the next hourly check,
        so the router feeds its fallbacks back in here.
        """
        now = self.clock()
        status, reason, retry_after = classify_exception(exc, now=now)
        self.record(
            ProviderHealth(
                provider=name,
                status=status,
                reason=reason,
                detail=str(exc)[:300],
                checked_at=now,
                retry_after=retry_after,
                failures=self.status(name).failures + 1,
            )
        )

    async def check(self, provider: Any, *, force: bool = False) -> ProviderHealth:
        """Ask one provider whether it would answer. Never raises."""
        name = getattr(provider, "name", "?")
        if not force and not self.stale(name):
            return self.status(name)

        request = provider.health_request() if isinstance(provider, Checkable) else None
        if request is None:
            return self.record(self._without_probe(provider, name))

        url, headers = request
        started = self.clock()
        try:
            response = await self.http.get(url, headers=headers, timeout=self.timeout)
        # Exception, not BaseException: a probe reports its failure instead of
        # raising, but a cancelled background loop must still be able to stop.
        except Exception as exc:
            now = self.clock()
            status, reason, retry_after = classify_exception(exc, now=now)
            return self.record(
                ProviderHealth(
                    provider=name, status=status, reason=reason, detail=str(exc)[:300],
                    checked_at=now, latency_ms=(now - started) * 1000,
                    retry_after=retry_after, failures=self.status(name).failures + 1,
                )
            )

        now = self.clock()
        status, reason, retry_after = classify_response(
            response.status, response.text[:2000], response.headers, now=now
        )
        healthy = status is ProviderStatus.ONLINE
        return self.record(
            ProviderHealth(
                provider=name,
                status=status,
                reason=reason,
                detail="" if healthy else f"HTTP {response.status}: {response.text[:200]}",
                checked_at=now,
                latency_ms=(now - started) * 1000,
                retry_after=retry_after,
                failures=0 if healthy else self.status(name).failures + 1,
            )
        )

    async def check_all(
        self, providers: Iterable[Any], *, force: bool = False
    ) -> dict[str, ProviderHealth]:
        import asyncio

        targets: Sequence[Any] = list(providers)
        results = await asyncio.gather(
            *(self.check(p, force=force) for p in targets), return_exceptions=True
        )
        out: dict[str, ProviderHealth] = {}
        for provider, result in zip(targets, results, strict=True):
            name = getattr(provider, "name", "?")
            out[name] = result if isinstance(result, ProviderHealth) else self.status(name)
        return out

    # --- internals ---

    def _without_probe(self, provider: Any, name: str) -> ProviderHealth:
        """A provider with nothing to probe: the local fake, or a future in-process one."""
        now = self.clock()
        if getattr(provider, "available", lambda: True)():
            return ProviderHealth(
                provider=name, status=ProviderStatus.ONLINE,
                reason="Gotowy, nie wymaga sprawdzenia.", checked_at=now,
            )
        return ProviderHealth(
            provider=name, status=ProviderStatus.INVALID_CONFIG,
            reason=REASONS[ProviderStatus.INVALID_CONFIG], checked_at=now,
            failures=self.status(name).failures + 1,
        )

    def record(self, health: ProviderHealth) -> ProviderHealth:
        """Store a state we know without probing for it (disabled, seeded, tested)."""
        previous = self._states.get(health.provider)
        self._states[health.provider] = health
        # Only a real move is worth an event: an hourly "still fine" would push
        # noise to every connected window once an hour, forever.
        if self.bus is not None and (previous is None or previous.status is not health.status):
            self.bus.emit(
                Topic.PROVIDER_HEALTH,
                provider=health.provider,
                status=health.status.value,
                reason=health.sentence(),
                was=previous.status.value if previous else ProviderStatus.UNKNOWN.value,
            )
        return health


def unconfigured(name: str, reason: str = "", *, now: float = 0.0) -> ProviderHealth:
    """State for a provider the user turned off or never gave a key."""
    return ProviderHealth(
        provider=name,
        status=ProviderStatus.INVALID_CONFIG,
        reason=reason or REASONS[ProviderStatus.INVALID_CONFIG],
        checked_at=now or time.time(),
    )


__all__ = [
    "DEFAULT_TIMEOUT",
    "DEFAULT_TTL",
    "REASONS",
    "Checkable",
    "HealthMonitor",
    "ProviderHealth",
    "ProviderStatus",
    "classify_exception",
    "classify_response",
    "unconfigured",
]
