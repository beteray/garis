"""Model selection: the user never picks a model, so the router must pick well."""

from __future__ import annotations

import pytest

from garis.config import Config, ModelsConfig, ProviderConfig
from garis.errors import NoModelAvailable
from garis.models import (
    Capability,
    Job,
    Message,
    ModelRouter,
    ModelSpec,
    Need,
    Privacy,
    build_providers,
)
from garis.models.providers.fake import FakeProvider


def model(
    name: str,
    *,
    provider: str = "test",
    jobs: set[Job] | None = None,
    quality: float = 0.5,
    speed: float = 0.5,
    input_cost: float = 1.0,
    output_cost: float = 4.0,
    local: bool = False,
    capabilities: set[Capability] | None = None,
    context: int = 128_000,
) -> ModelSpec:
    return ModelSpec(
        name=name,
        provider=provider,
        jobs=frozenset(jobs or {Job.PLAN, Job.CHAT, Job.REASON, Job.CLASSIFY}),
        capabilities=frozenset(capabilities or {Capability.TOOLS}),
        quality=quality,
        speed=speed,
        input_cost=input_cost,
        output_cost=output_cost,
        context_tokens=context,
        local=local,
    )


class Stub(FakeProvider):
    """Fake provider that advertises a chosen catalogue."""

    def __init__(self, name: str, models: list[ModelSpec], **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self.name = name
        self._catalogue = models

    def models(self):  # type: ignore[no-untyped-def]
        return self._catalogue


# --------------------------------------------------------------------- picking


def test_hard_reasoning_prefers_the_strong_model() -> None:
    cheap = model("cheap", quality=0.4, speed=0.95, input_cost=0.1, output_cost=0.4)
    strong = model("strong", quality=0.95, speed=0.3, input_cost=5.0, output_cost=25.0)
    router = ModelRouter([Stub("test", [cheap, strong])], ModelsConfig())

    assert router.select(Need(job=Job.REASON)).model.name == "strong"


def test_high_volume_classification_prefers_the_fast_cheap_model() -> None:
    cheap = model("cheap", jobs={Job.CLASSIFY}, quality=0.4, speed=0.95,
                  input_cost=0.1, output_cost=0.4)
    strong = model("strong", jobs={Job.CLASSIFY}, quality=0.95, speed=0.3,
                   input_cost=5.0, output_cost=25.0)
    router = ModelRouter([Stub("test", [cheap, strong])], ModelsConfig())

    assert router.select(Need(job=Job.CLASSIFY, prefer_speed=True)).model.name == "cheap"


def test_a_model_that_cannot_do_the_job_is_not_offered() -> None:
    router = ModelRouter([Stub("test", [model("text-only", jobs={Job.CHAT})])], ModelsConfig())
    with pytest.raises(NoModelAvailable):
        router.select(Need(job=Job.VISION))


def test_required_capability_filters_candidates() -> None:
    plain = model("plain", capabilities={Capability.TOOLS})
    seeing = model("seeing", capabilities={Capability.TOOLS, Capability.VISION})
    router = ModelRouter([Stub("test", [plain, seeing])], ModelsConfig())
    choice = router.select(Need(job=Job.CHAT, requires=frozenset({Capability.VISION})))
    assert choice.model.name == "seeing"


def test_context_requirement_is_respected() -> None:
    small = model("small", context=8_000)
    large = model("large", context=1_000_000)
    router = ModelRouter([Stub("test", [small, large])], ModelsConfig())
    assert router.select(Need(job=Job.CHAT, min_context=200_000)).model.name == "large"


# --------------------------------------------------------------------- privacy


def test_private_work_stays_on_the_machine() -> None:
    cloud = model("cloud", quality=0.99)
    local = model("local", quality=0.5, local=True,
                  capabilities={Capability.TOOLS, Capability.LOCAL})
    router = ModelRouter([Stub("test", [cloud, local])], ModelsConfig())

    assert router.select(Need(job=Job.CHAT, privacy=Privacy.LOCAL_ONLY)).model.name == "local"


def test_local_only_with_no_local_model_is_an_honest_failure() -> None:
    router = ModelRouter([Stub("test", [model("cloud")])], ModelsConfig())
    with pytest.raises(NoModelAvailable):
        router.select(Need(job=Job.CHAT, privacy=Privacy.LOCAL_ONLY))


def test_prefer_local_still_allows_cloud_when_needed() -> None:
    router = ModelRouter([Stub("test", [model("cloud")])], ModelsConfig())
    assert router.select(Need(job=Job.CHAT, privacy=Privacy.PREFER_LOCAL)).model.name == "cloud"


def test_cloud_can_be_switched_off_globally() -> None:
    cloud = model("cloud")
    local = model("local", local=True, capabilities={Capability.LOCAL})
    router = ModelRouter([Stub("test", [cloud, local])],
                         ModelsConfig(allow_cloud=False))
    assert router.select(Need(job=Job.CHAT)).model.local


# ---------------------------------------------------------------------- budget


def test_budget_exhaustion_falls_back_to_free_models() -> None:
    cloud = model("cloud", quality=0.9, input_cost=10.0, output_cost=30.0)
    local = model("local", quality=0.4, local=True, input_cost=0.0, output_cost=0.0,
                  capabilities={Capability.LOCAL})
    router = ModelRouter([Stub("test", [cloud, local])],
                         ModelsConfig(monthly_budget=1.0))
    assert router.select(Need(job=Job.CHAT)).model.name == "cloud"

    router._spend = 1.5      # simulate a month of usage
    assert router.select(Need(job=Job.CHAT)).model.name == "local"


def test_per_call_cost_ceiling() -> None:
    pricey = model("pricey", input_cost=50.0, output_cost=150.0)
    router = ModelRouter([Stub("test", [pricey])], ModelsConfig())
    with pytest.raises(NoModelAvailable):
        router.select(Need(job=Job.CHAT, max_cost=0.01))


# -------------------------------------------------------------------- fallback


async def test_provider_failure_falls_through_silently() -> None:
    broken = Stub("broken", [model("broken-1", provider="broken", quality=0.99)],
                  fail_times=5)
    working = Stub("working", [model("working-1", provider="working", quality=0.5)],
                   replies=["gotowe"])
    router = ModelRouter([broken, working], ModelsConfig())

    completion = await router.complete(Need(job=Job.CHAT), [Message.user("cześć")])
    assert completion.text == "gotowe"
    assert completion.provider == "working"


async def test_all_providers_failing_is_reported() -> None:
    router = ModelRouter(
        [Stub("a", [model("a1", provider="a")], fail_times=9)], ModelsConfig()
    )
    with pytest.raises(NoModelAvailable) as caught:
        await router.complete(Need(job=Job.CHAT), [Message.user("cześć")])
    assert "zawiedli" in str(caught.value)


async def test_usage_and_spend_are_tracked() -> None:
    priced = model("priced", input_cost=1000.0, output_cost=1000.0)

    class Costed(Stub):
        async def complete(self, model_spec, messages, **kw):  # type: ignore[no-untyped-def]
            completion = await super().complete(model_spec, messages, **kw)
            completion.usage.cost = 0.25
            return completion

    router = ModelRouter([Costed("c", [priced], replies=["ok"])], ModelsConfig())
    await router.complete(Need(job=Job.CHAT), [Message.user("x")])
    assert router.spend == pytest.approx(0.25)


# ------------------------------------------------------------------- assembly


def test_providers_without_a_key_are_not_built(home) -> None:
    config = Config()
    config.models.providers = {
        "openai": ProviderConfig(key_ref="vault://openai_api_key"),
        "anthropic": ProviderConfig(key_ref="vault://anthropic_api_key"),
    }
    providers = build_providers(config, vault=None)
    # No vault, no keys, no providers — not even a stub. A stub here is a model
    # that always answers, which is worse than no model at all: it plans by
    # keyword and certifies its own work.
    assert [p.name for p in providers] == []


def test_configured_key_produces_a_live_provider(vault) -> None:
    vault.set("gemini_api_key", "test-key")
    config = Config()
    config.models.providers = {"gemini": ProviderConfig(key_ref="vault://gemini_api_key")}
    providers = build_providers(config, vault=vault)
    assert [p.name for p in providers] == ["gemini"]
    assert providers[0].available()


def test_provider_aliases() -> None:
    from garis.models import resolve_provider_name

    assert resolve_provider_name("Claude") == "anthropic"
    assert resolve_provider_name("ChatGPT") == "openai"
    assert resolve_provider_name("google") == "gemini"


def test_describe_reports_a_pick_for_every_job() -> None:
    router = ModelRouter([Stub("test", [model("m", jobs=set(Job))])], ModelsConfig())
    described = router.describe()
    assert set(described["picks"]) == {job.value for job in Job}


def test_json_repair_handles_fenced_and_messy_output() -> None:
    from garis.models import parse_json_lenient

    assert parse_json_lenient('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_lenient('Oto plan: {"a": [1, 2,]}') == {"a": [1, 2]}
    assert parse_json_lenient('{"a": 1}') == {"a": 1}
