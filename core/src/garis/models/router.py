"""Model selection.

This is the module that makes "the user does not choose a model" true. Callers
state a :class:`Need`; the router scores every model every available provider
offers and picks one, then falls back down the ranking if the first choice fails.

Scoring inputs, in the order the spec lists them: kind of work, quality, speed,
cost, privacy, and remaining budget.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..config import ModelsConfig
from ..errors import NoModelAvailable, ProviderError
from ..events import EventBus
from .base import (
    Capability,
    Completion,
    Job,
    Message,
    ModelSpec,
    Need,
    Privacy,
    Provider,
    Usage,
)
from .health import HealthMonitor, ProviderHealth, ProviderStatus


@dataclass(frozen=True, slots=True)
class Weights:
    quality: float
    speed: float
    cost: float
    local_bonus: float


# One profile per privacy setting. Everything the user can tune about model
# choice is expressed here, in four numbers, instead of a model picker.
PROFILES: dict[str, Weights] = {
    "local_only": Weights(quality=0.5, speed=0.3, cost=0.0, local_bonus=1.0),
    "prefer_local": Weights(quality=0.55, speed=0.25, cost=0.2, local_bonus=0.35),
    "balanced": Weights(quality=0.6, speed=0.2, cost=0.2, local_bonus=0.1),
    "quality_first": Weights(quality=0.85, speed=0.1, cost=0.05, local_bonus=0.0),
}

# Jobs where being wrong is expensive, so quality outranks the profile.
_QUALITY_CRITICAL = frozenset({Job.PLAN, Job.REASON, Job.CODE})

# Rough ceiling used to normalise price into 0..1. Above this a model is simply
# "expensive"; the exact figure stops mattering.
_COST_CEILING = 40.0


@dataclass(slots=True)
class Choice:
    provider: Provider
    model: ModelSpec
    score: float
    reasons: dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.model.provider}/{self.model.name} ({self.score:.2f})"


class ModelRouter:
    def __init__(
        self,
        providers: Sequence[Provider],
        config: ModelsConfig | None = None,
        *,
        bus: EventBus | None = None,
        health: HealthMonitor | None = None,
    ) -> None:
        self.providers = list(providers)
        self.config = config or ModelsConfig()
        self.bus = bus
        # Optional so a test can build a router without a network story. Without
        # it the router falls back to the old question — "is a key present?" —
        # which is exactly the question the health monitor exists to replace.
        self.health = health
        self.usage = Usage()
        self._spend = 0.0

    # ------------------------------------------------------------- live providers

    def replace(self, providers: Sequence[Provider]) -> None:
        """Swap the provider set without becoming a different router.

        The planner, the verifier and the voice layer all hold this object. A new
        key must reach them, and rebuilding the router would not.
        """
        self.providers = list(providers)

    def usable(self, provider: Provider) -> bool:
        """Would the router send work here right now, and is that a measured yes."""
        if not provider.available():
            return False
        return self.health is None or self.health.usable(provider.name)

    def state_of(self, provider: Provider) -> ProviderHealth:
        if self.health is not None:
            return self.health.status(provider.name)
        status = ProviderStatus.ONLINE if provider.available() else ProviderStatus.INVALID_CONFIG
        return ProviderHealth(provider=provider.name, status=status)

    # ------------------------------------------------------------------ ranking

    def _profile(self, need: Need) -> Weights:
        privacy = (
            "local_only"
            if need.privacy is Privacy.LOCAL_ONLY
            else "prefer_local"
            if need.privacy is Privacy.PREFER_LOCAL
            else self.config.privacy
        )
        weights = PROFILES.get(privacy, PROFILES["balanced"])
        if need.job in _QUALITY_CRITICAL and privacy not in ("local_only", "prefer_local"):
            return Weights(quality=0.8, speed=0.1, cost=weights.cost, local_bonus=0.0)
        if need.prefer_speed:
            return Weights(quality=weights.quality * 0.6, speed=0.5, cost=weights.cost,
                           local_bonus=weights.local_bonus)
        return weights

    def candidates(self, need: Need) -> list[Choice]:
        weights = self._profile(need)
        over_budget = self._over_budget()
        out: list[Choice] = []

        for provider in self.providers:
            if not self.usable(provider):
                continue
            for model in provider.models():
                if not model.supports(need.job):
                    continue
                if need.requires - model.capabilities:
                    continue
                if need.min_context and model.context_tokens < need.min_context:
                    continue
                if need.privacy is Privacy.LOCAL_ONLY and not model.local:
                    continue
                if not self.config.allow_cloud and not model.local:
                    continue
                # Budget exhausted: only free (local) models remain eligible.
                if over_budget and not model.local:
                    continue
                if need.max_cost is not None:
                    typical = model.price_for(8_000, 1_500)
                    if typical > need.max_cost:
                        continue

                cost_index = min(
                    1.0, (model.input_cost + model.output_cost * 3) / _COST_CEILING
                )
                reasons = {
                    "quality": weights.quality * model.quality,
                    "speed": weights.speed * model.speed,
                    "cost": -weights.cost * cost_index,
                    "local": weights.local_bonus if model.local else 0.0,
                }
                out.append(Choice(provider, model, sum(reasons.values()), reasons))

        out.sort(key=lambda c: (-c.score, c.model.name))
        return out

    def select(self, need: Need) -> Choice:
        found = self.candidates(need)
        if not found:
            raise NoModelAvailable(
                f"Brak modelu dla zadania {need.job.value} "
                f"(prywatność: {need.privacy.value}); {self.why_unavailable()}",
                user_message=self._no_model_sentence(),
            )
        return found[0]

    def why_unavailable(self) -> str:
        """One line naming every provider and the reason it is out.

        "No model available" is a useless thing to tell somebody. "The Gemini key
        was rejected" is something they can fix.
        """
        return ", ".join(
            f"{p.name}: {self.state_of(p).sentence()}" for p in self.providers
        ) or "brak skonfigurowanych dostawców"

    def _no_model_sentence(self) -> str:
        blocked = [
            (p.name, self.state_of(p))
            for p in self.providers
            if not self.usable(p)
        ]
        if not blocked:
            return NoModelAvailable.user_message or ""
        name, health = blocked[0]
        return f"Nie mam teraz dostępnego modelu — {name}: {health.sentence().lower()}"

    # ------------------------------------------------------------------ calling

    async def complete(
        self,
        need: Need,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
        timeout: float = 120.0,
        max_attempts: int = 3,
    ) -> Completion:
        """Complete with automatic fallback.

        A provider failure is GARIS's problem, not the user's: try the next best
        model rather than surfacing "OpenAI returned 503".
        """
        ranked = self.candidates(need)
        if not ranked:
            raise NoModelAvailable(
                f"Brak modelu dla zadania {need.job.value}; {self.why_unavailable()}",
                user_message=self._no_model_sentence(),
            )

        errors: list[str] = []
        for choice in ranked[:max_attempts]:
            if json_mode and Capability.JSON_MODE not in choice.model.capabilities:
                json_mode_for_call = False
            else:
                json_mode_for_call = json_mode
            try:
                completion = await choice.provider.complete(
                    choice.model,
                    messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode_for_call,
                    timeout=timeout,
                )
            except ProviderError as exc:
                errors.append(f"{choice}: {exc}")
                self._note_failure(choice, exc)
                continue
            except Exception as exc:
                errors.append(f"{choice}: {type(exc).__name__}: {exc}")
                self._note_failure(choice, exc)
                continue

            self._record(completion)
            return completion

        raise NoModelAvailable(
            "Wszyscy dostawcy zawiedli: " + "; ".join(errors),
        )

    async def stream(
        self,
        need: Need,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ):
        choice = self.select(Need(
            job=need.job,
            privacy=need.privacy,
            requires=need.requires | {Capability.STREAMING},
            max_cost=need.max_cost,
            prefer_speed=need.prefer_speed,
            min_context=need.min_context,
        ))
        async for piece in choice.provider.stream(
            choice.model, messages, temperature=temperature, max_tokens=max_tokens,
            timeout=timeout
        ):
            yield piece

    # ------------------------------------------------------------------ budget

    def _over_budget(self) -> bool:
        cap = self.config.monthly_budget
        return bool(cap) and self._spend >= cap

    def _record(self, completion: Completion) -> None:
        self.usage = self.usage + completion.usage
        self._spend += completion.usage.cost

    @property
    def spend(self) -> float:
        return self._spend

    def reset_spend(self) -> None:
        self._spend = 0.0

    def _emit(self, topic: str, **payload: Any) -> None:
        if self.bus is not None:
            self.bus.emit(topic, **payload)

    def _note_failure(self, choice: Choice, exc: BaseException) -> None:
        """A failure during real work is a health measurement like any other.

        Without this, a key revoked at noon keeps looking healthy until the next
        hourly check, and every task in between pays for the lie.
        """
        self._emit("model.fallback", model=str(choice), error=str(exc))
        if self.health is not None:
            self.health.note_failure(choice.provider.name, exc)

    # ------------------------------------------------------------------ report

    def describe(self) -> dict[str, Any]:
        """Used by ``garis doctor`` and every surface — who answers, and why not.

        ``available`` now means "the router will send work here", not "a key is
        on file". Those were treated as the same thing, and they are not.
        """
        return {
            "privacy": self.config.privacy,
            "allow_cloud": self.config.allow_cloud,
            "monthly_budget": self.config.monthly_budget,
            "spend": round(self._spend, 4),
            "providers": [self.describe_provider(p) for p in self.providers],
            "picks": {
                job.value: str(self.candidates(Need(job=job))[0])
                if self.candidates(Need(job=job))
                else "—"
                for job in Job
            },
        }

    def describe_provider(self, provider: Provider) -> dict[str, Any]:
        health = self.state_of(provider)
        return {
            "name": provider.name,
            "available": self.usable(provider),   # every surface already reads this
            "models": [m.name for m in provider.models()],
            "status": health.status.value,
            "reason": health.sentence(),
            "detail": health.detail,
            "checked_at": health.checked_at,
            "latency_ms": round(health.latency_ms, 1),
            "retry_after": health.retry_after,
            "failures": health.failures,
        }

    def available_providers(self) -> list[str]:
        return [p.name for p in self.providers if self.usable(p)]

    def add(self, provider: Provider) -> None:
        self.providers.append(provider)

    def has_any(self) -> bool:
        return any(self.usable(p) for p in self.providers)


def jobs_of(models: Iterable[ModelSpec]) -> set[Job]:
    covered: set[Job] = set()
    for model in models:
        covered |= model.jobs
    return covered


__all__ = ["PROFILES", "Choice", "ModelRouter", "Weights", "jobs_of"]
