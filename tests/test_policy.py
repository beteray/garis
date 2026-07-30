"""The approval gates.

These tests pin the product's central promise: GARIS works without interrupting,
except for effects the user must own. If any of these ever fail, GARIS has either
become annoying or become unsafe.
"""

from __future__ import annotations

import pytest

from garis.config import AutonomyConfig, PersonaConfig
from garis.paths import Paths
from garis.runtime import Action, Decision, Effect, PolicyEngine, ToolRegistry
from garis.runtime.policy import NON_NEGOTIABLE_CONFIRM
from garis.runtime.registry import ToolSpec


async def _noop(ctx, **kw):  # type: ignore[no-untyped-def]
    return None


def spec(name: str, *effects: Effect, **kwargs) -> ToolSpec:  # type: ignore[no-untyped-def]
    return ToolSpec(name=name, summary=name, handler=_noop, effects=frozenset(effects), **kwargs)


def action(tool: str, **params) -> Action:  # type: ignore[no-untyped-def]
    return Action(tool=tool, params=params, intent="test")


@pytest.fixture
def policy(paths: Paths) -> PolicyEngine:
    return PolicyEngine(AutonomyConfig(), paths)


# --------------------------------------------------------------- silent by default


@pytest.mark.parametrize(
    "effects",
    [
        (Effect.READ,),
        (Effect.WRITE,),
        (Effect.EXEC,),
        (Effect.NETWORK,),
        (Effect.READ, Effect.WRITE),
        (Effect.SYSTEM_CONFIG,),      # reversible config change: audited, not gated
        (Effect.INPUT_CONTROL,),
        (Effect.CAPTURE,),
    ],
)
def test_ordinary_work_needs_no_permission(policy: PolicyEngine, effects) -> None:
    verdict = policy.evaluate(spec("t", *effects), action("t"))
    assert verdict.decision is Decision.ALLOW, f"{effects} nie powinno pytać: {verdict.rule}"


# ------------------------------------------------------------------- gated effects


@pytest.mark.parametrize(
    ("effect", "expected_phrase"),
    [
        (Effect.PAYMENT, "płatnoś"),
        (Effect.PUBLISH, "publiczn"),
        (Effect.SEND_MESSAGE, "wiadomość"),
        (Effect.CREDENTIALS, "dane logowania"),
        (Effect.DELETE_PERMANENT, "trwale usun"),
    ],
)
def test_final_effects_require_confirmation(
    policy: PolicyEngine, effect: Effect, expected_phrase: str
) -> None:
    verdict = policy.evaluate(spec("t", effect), action("t", to="ktoś"))
    assert verdict.decision is Decision.CONFIRM
    assert expected_phrase.lower() in verdict.prompt.lower()
    assert verdict.prompt.endswith("?"), "prośba o zgodę musi być pytaniem"


def test_config_cannot_remove_a_non_negotiable_gate(paths: Paths) -> None:
    """Emptying the list in config must not unlock payments or publishing."""
    permissive = PolicyEngine(AutonomyConfig(confirm_effects=[]), paths)
    for effect in NON_NEGOTIABLE_CONFIRM:
        verdict = permissive.evaluate(spec("t", effect), action("t"))
        assert verdict.decision is Decision.CONFIRM, f"{effect} przeszło bez zgody"


def test_config_can_add_a_gate(paths: Paths) -> None:
    strict = PolicyEngine(AutonomyConfig(confirm_effects=["install", "system_config"]), paths)
    assert strict.evaluate(spec("t", Effect.SYSTEM_CONFIG), action("t")).decision \
        is Decision.CONFIRM


def test_irreversible_tool_requires_confirmation(policy: PolicyEngine) -> None:
    verdict = policy.evaluate(
        spec("wipe", Effect.WRITE, reversible=False, danger_note="Nie da się cofnąć."),
        action("wipe", path="C:/dane"),
    )
    assert verdict.decision is Decision.CONFIRM
    assert "cofnąć" in verdict.prompt


# ------------------------------------------------------------------ notice gates


def test_large_download_is_announced_with_size(policy: PolicyEngine) -> None:
    twenty_gb = 20 * 1024**3
    verdict = policy.evaluate(
        spec("fetch_model", Effect.NETWORK, estimate=lambda p: (twenty_gb, 0.0)),
        action("fetch_model"),
    )
    assert verdict.decision is Decision.CONFIRM
    assert verdict.rule == "download_size"
    assert "20.0 GB" in verdict.prompt and "Kontynuować?" in verdict.prompt


def test_small_download_stays_silent(policy: PolicyEngine) -> None:
    verdict = policy.evaluate(
        spec("fetch", Effect.NETWORK, estimate=lambda p: (5 * 1024**2, 0.0)),
        action("fetch"),
    )
    assert verdict.decision is Decision.ALLOW


def test_any_spend_is_confirmed(policy: PolicyEngine) -> None:
    verdict = policy.evaluate(
        spec("buy", Effect.NETWORK, estimate=lambda p: (0, 0.01)), action("buy")
    )
    assert verdict.decision is Decision.CONFIRM
    assert verdict.rule == "cost"


def test_planner_estimate_raises_the_floor(policy: PolicyEngine) -> None:
    """A tool with no estimator still gets gated if the plan declares a big download."""
    act = action("fetch")
    act.estimated_bytes = 40 * 1024**3
    verdict = policy.evaluate(spec("fetch", Effect.NETWORK), act)
    assert verdict.decision is Decision.CONFIRM and verdict.rule == "download_size"


# --------------------------------------------------------------------- installing


def test_free_trusted_install_is_automatic(policy: PolicyEngine) -> None:
    verdict = policy.evaluate(
        spec("package_install", Effect.INSTALL, trusted_source=True), action("package_install")
    )
    assert verdict.decision is Decision.ALLOW


def test_untrusted_install_asks_first(policy: PolicyEngine) -> None:
    verdict = policy.evaluate(
        spec("install_exe", Effect.INSTALL, trusted_source=False),
        action("install_exe", package="cos.exe"),
    )
    assert verdict.decision is Decision.CONFIRM and verdict.rule == "install"


def test_paid_install_asks_first(policy: PolicyEngine) -> None:
    verdict = policy.evaluate(
        spec("install_paid", Effect.INSTALL, estimate=lambda p: (0, 49.0)),
        action("install_paid"),
    )
    assert verdict.decision is Decision.CONFIRM


def test_install_can_be_switched_off(paths: Paths) -> None:
    manual = PolicyEngine(AutonomyConfig(auto_install_free_software=False), paths)
    assert manual.evaluate(spec("package_install", Effect.INSTALL), action("x")).decision \
        is Decision.CONFIRM


def test_elevation_allowed_by_default_but_configurable(paths: Paths) -> None:
    default = PolicyEngine(AutonomyConfig(), paths)
    assert default.evaluate(spec("admin", Effect.ELEVATE, Effect.EXEC),
                            action("admin")).decision is Decision.ALLOW
    locked = PolicyEngine(AutonomyConfig(allow_admin_elevation=False), paths)
    assert locked.evaluate(spec("admin", Effect.ELEVATE, Effect.EXEC),
                           action("admin")).decision is Decision.CONFIRM


# ---------------------------------------------------------------- self-protection


def test_touching_the_vault_is_refused_outright(policy: PolicyEngine, paths: Paths) -> None:
    verdict = policy.evaluate(
        spec("file_delete", Effect.DELETE_PERMANENT), action("x", path=str(paths.vault_db))
    )
    assert verdict.decision is Decision.DENY and verdict.rule == "guardrails"


def test_touching_the_master_key_is_refused(policy: PolicyEngine, paths: Paths) -> None:
    verdict = policy.evaluate(
        spec("file_write", Effect.WRITE), action("x", path=str(paths.key_file))
    )
    assert verdict.decision is Decision.DENY


def test_guardrail_check_sees_nested_parameters(policy: PolicyEngine, paths: Paths) -> None:
    verdict = policy.evaluate(
        spec("shell_run", Effect.EXEC),
        action("x", args={"targets": ["--force", str(paths.state_db)]}),
    )
    assert verdict.decision is Decision.DENY


def test_vault_tool_may_touch_the_vault(policy: PolicyEngine, paths: Paths) -> None:
    """The vault's own tools are how credentials are stored; they are not the threat."""
    verdict = policy.evaluate(
        spec("vault_store", Effect.CREDENTIALS, category="vault"),
        action("vault_store", path=str(paths.vault_db)),
    )
    assert verdict.decision is Decision.CONFIRM  # gated as credentials, not denied


def test_agent_cannot_rewrite_its_own_gates(policy: PolicyEngine) -> None:
    verdict = policy.evaluate(
        spec("config_set", Effect.WRITE),
        action("config_set", key="autonomy.confirm_effects", value="[]"),
    )
    assert verdict.decision is Decision.DENY and verdict.rule == "policy_self_edit"


# --------------------------------------------------------------- persona firewall


def test_persona_cannot_reach_the_policy_layer() -> None:
    """Structural check: tone settings are not an input to any verdict.

    Personality may change how GARIS speaks. If it could ever change what GARIS is
    allowed to do, a prompt asking for a "relaxed" persona would be an escalation
    path. The signature is the enforcement.
    """
    import inspect

    signature = inspect.signature(PolicyEngine.__init__)
    accepted = set(signature.parameters) - {"self"}
    assert accepted == {"autonomy", "paths"}

    source = inspect.getsource(PolicyEngine)
    assert "persona" not in source.lower()
    assert not hasattr(PersonaConfig(), "confirm_effects")


def test_registry_declares_effects_not_the_caller(registry: ToolRegistry) -> None:
    """A model cannot relabel a payment as a read by wording the intent differently."""
    pay = registry.get("pay")
    assert Effect.PAYMENT in pay.effects
    sneaky = Action(tool="pay", params={"amount": 5.0},
                    intent="tylko sprawdź, nic nie płać, to zwykły odczyt")
    verdict = PolicyEngine(AutonomyConfig(), Paths.resolve()).evaluate(pay, sneaky)
    assert verdict.decision is Decision.CONFIRM


def test_describe_lists_active_gates(policy: PolicyEngine) -> None:
    described = policy.describe()
    assert "payment" in described["confirm_effects"]
    assert described["never"], "twarde odmowy muszą być widoczne w diagnostyce"
