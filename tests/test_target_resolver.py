"""One answer to "could this step run", shared by everyone who asks.

Etap B of `docs/PLANSTEP_STEP_TARGET_DESIGN.md`. The planner and
`garis do --dry-run` both need to know what a target is before anything happens.
They used to each read the tool registry directly, which was fine while a step
could only be a tool and quietly wrong the moment it could be a capability: a
planner that consults only the tool registry stops scheduling capabilities and
never says why.

The resolver answers; it does not run. That separation is the point — see the
structural test at the bottom.
"""

from __future__ import annotations

import sys

import pytest

from garis.capabilities.base import Capability, Field, Outcome, Permission, Risk, evidence_verifier
from garis.capabilities.registry import CapabilityRegistry
from garis.errors import ToolValidationError
from garis.kernel import CapabilityTarget, ToolTarget
from garis.runtime import Effect, ParamSpec, TargetResolver, ToolRegistry


def _capability(**over) -> Capability:
    async def run(_):
        return Outcome(ok=True, value={}, evidence={"seen": 1})

    base = {
        "id": "test.measure",
        "version": 1,
        "summary": "Mierzy.",
        "risk": Risk.READ,
        "permission": Permission.NONE,
        "executor": run,
        "verifier": evidence_verifier(),
        "evidence": (Field("seen", "int", "Co zmierzono"),),
        "effects": frozenset({Effect.READ}),
    }
    return Capability(**{**base, **over})


@pytest.fixture
def tools() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.tool("look", "Patrzy.", params={}, effects=[Effect.READ])
    async def look(ctx):
        return {}

    @registry.tool("wipe", "Kasuje.", params={}, effects=[Effect.DELETE_PERMANENT])
    async def wipe(ctx):
        return {}

    return registry


@pytest.fixture
def capabilities() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.add(_capability())
    return registry


# ------------------------------------------------------------------- the two kinds


def test_a_known_tool_resolves_to_something_runnable(tools) -> None:
    resolved = TargetResolver(tools).resolve(ToolTarget("look"))

    assert resolved.executable
    assert resolved.name == "look"
    assert resolved.reason == "", "nie ma czego wyjaśniać, gdy wszystko gra"


def test_a_known_capability_resolves_the_same_way(tools, capabilities) -> None:
    resolved = TargetResolver(tools, capabilities).resolve(CapabilityTarget("test.measure"))

    assert resolved.executable
    assert resolved.effects == frozenset({Effect.READ})


def test_an_unknown_target_explains_itself_in_polish(tools, capabilities) -> None:
    resolver = TargetResolver(tools, capabilities)

    tool = resolver.resolve(ToolTarget("nie_ma_takiego"))
    capability = resolver.resolve(CapabilityTarget("test.nie.ma"))

    assert not tool.executable and "nieznane narzędzie" in tool.reason
    assert not capability.executable and "nieznana zdolność" in capability.reason


def test_without_a_capability_catalogue_capabilities_are_simply_unknown(tools) -> None:
    """A resolver built with tools alone must not claim a capability runs."""
    resolved = TargetResolver(tools).resolve(CapabilityTarget("test.measure"))

    assert not resolved.executable
    assert not resolved.known


def test_a_target_for_another_platform_is_known_but_not_runnable(tools, capabilities) -> None:
    other = "linux" if sys.platform != "linux" else "win32"
    capabilities.add(_capability(id="test.elsewhere", platforms=(other,)))

    resolved = TargetResolver(tools, capabilities).resolve(CapabilityTarget("test.elsewhere"))

    assert resolved.known, "istnieje — po prostu nie tutaj"
    assert not resolved.runs_here
    assert "nie działa na tym systemie" in resolved.reason


# ------------------------------------------------------------- declared consequences


def test_the_preview_reports_what_a_step_would_touch(tools) -> None:
    """`garis do --dry-run` is built on this: effects come from the declaration."""
    resolver = TargetResolver(tools)

    assert resolver.resolve(ToolTarget("wipe")).effects == frozenset({Effect.DELETE_PERMANENT})
    assert resolver.resolve(ToolTarget("look")).effects == frozenset({Effect.READ})


def test_parameters_are_checked_by_the_thing_that_owns_the_schema(tools) -> None:
    """The resolver never invents validation — it hands back the owner's."""
    registry = ToolRegistry()

    @registry.tool("count", "Liczy.", params={"n": ParamSpec("int", required=True)},
                   effects=[Effect.READ])
    async def count(ctx, n: int):
        return {}

    resolved = TargetResolver(registry).resolve(ToolTarget("count"))

    assert resolved.validate({"n": "3"}) == {"n": 3}
    with pytest.raises(ToolValidationError):
        resolved.validate({})


def test_an_unresolvable_target_refuses_to_validate_anything(tools) -> None:
    resolved = TargetResolver(tools).resolve(ToolTarget("nie_ma_takiego"))

    with pytest.raises(ValueError):
        resolved.validate({})


# --------------------------------------------------------------- what it must not be


def test_the_resolver_cannot_run_anything() -> None:
    """Structural, because the guarantee is architectural.

    The alternative design — exposing `CapabilityRunner`'s first step — would
    have made the runner's sixteen-step order a public contract, so that
    reordering execution could break plan validation. This layer holds no runner,
    no policy engine and no effect store, and the moment it acquires one it stops
    being a translation layer.
    """
    import ast
    import inspect

    from garis.runtime import resolve

    # Prose is stripped first: this module names the runner in order to explain
    # why it is absent, and a check that cannot tell an explanation from an
    # import would forbid saying why.
    tree = ast.parse(inspect.getsource(resolve))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)

    for forbidden in ("CapabilityRunner", "runner", "PolicyEngine", "EffectStore",
                      "await ", "async def"):
        assert forbidden not in code, f"resolver zaczął wykonywać: {forbidden}"
