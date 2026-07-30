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
    ) -> None:
        self.providers = list(providers)
        self.config = config or ModelsConfig()
        self.bus = bus
        self.usage = Usage()
        self._spend = 0.0

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
            if not provider.available():
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
                f"(prywatność: {need.privacy.value}, dostawcy: "
                f"{[p.name for p in self.providers if p.available()]})"
            )
        return found[0]

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
                f"Brak modelu dla zadania {need.job.value}",
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
                self._emit("model.fallback", model=str(choice), error=str(exc))
                if not exc.retryable:
                    continue
                continue
            except Exception as exc:
                errors.append(f"{choice}: {type(exc).__name__}: {exc}")
                self._emit("model.fallback", model=str(choice), error=str(exc))
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

    # ------------------------------------------------------------------ report

    def describe(self) -> dict[str, Any]:
        """Used by ``garis doctor`` — which providers answer, what they offer."""
        return {
            "privacy": self.config.privacy,
            "allow_cloud": self.config.allow_cloud,
            "monthly_budget": self.config.monthly_budget,
            "spend": round(self._spend, 4),
            "providers": [
                {
                    "name": p.name,
                    "available": p.available(),
                    "models": [m.name for m in p.models()],
                }
                for p in self.providers
            ],
            "picks": {
                job.value: str(self.candidates(Need(job=job))[0])
                if self.candidates(Need(job=job))
                else "—"
                for job in Job
            },
        }

    def available_providers(self) -> list[str]:
        return [p.name for p in self.providers if p.available()]

    def add(self, provider: Provider) -> None:
        self.providers.append(provider)

    def has_any(self) -> bool:
        return any(p.available() for p in self.providers)


def jobs_of(models: Iterable[ModelSpec]) -> set[Job]:
    covered: set[Job] = set()
    for model in models:
        covered |= model.jobs
    return covered


__all__ = ["PROFILES", "Choice", "ModelRouter", "Weights", "jobs_of"]
