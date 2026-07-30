"""The single execution path: nothing runs without passing every gate."""

from __future__ import annotations

import pytest

from garis.errors import ApprovalDenied, ApprovalRequired, PolicyDenied
from garis.runtime import Action, ApprovalState, Runtime
from garis.runtime.audit import REDACTED


async def test_ordinary_action_runs_and_is_audited(runtime: Runtime) -> None:
    result = await runtime.perform_tool("note", intent="zapisz notatkę", text="cześć")
    assert result.ok and result.value == {"written": "cześć"}

    entry = runtime.audit.recent(1)[0]
    assert entry.tool == "note"
    assert entry.outcome == "ok"
    assert entry.decision == "allow"
    assert entry.intent == "zapisz notatkę"


async def test_unknown_tool_is_a_result_not_a_crash(runtime: Runtime) -> None:
    """The agent must be able to replan, so this is data rather than an exception."""
    result = await runtime.perform_tool("teleport")
    assert not result.ok
    assert result.error_kind == "not_found"
    assert not result.retryable
    assert "teleport" in result.error


async def test_bad_parameters_come_back_fixable(runtime: Runtime) -> None:
    result = await runtime.perform_tool("note")           # missing required 'text'
    assert not result.ok and result.error_kind == "invalid"
    assert result.retryable, "planner should be able to correct and retry"
    assert "text" in result.error


async def test_unknown_parameter_is_rejected(runtime: Runtime) -> None:
    result = await runtime.perform_tool("note", text="x", colour="blue")
    assert not result.ok and result.error_kind == "invalid"
    assert "colour" in result.error


async def test_timeout_is_recoverable(runtime: Runtime) -> None:
    result = await runtime.perform_tool("slow")
    assert not result.ok and result.error_kind == "timeout" and result.retryable


async def test_tool_crash_does_not_escape(runtime: Runtime) -> None:
    @runtime.registry.tool("boom", "Wywala się.", params={})
    async def boom(ctx):  # type: ignore[no-untyped-def]
        raise ZeroDivisionError("upa")

    result = await runtime.perform_tool("boom")
    assert not result.ok and result.error_kind == "crash"
    assert "ZeroDivisionError" in result.error


# ------------------------------------------------------------------- approvals


async def test_gated_action_stops_and_records_a_request(runtime: Runtime) -> None:
    with pytest.raises(ApprovalRequired) as caught:
        await runtime.perform_tool("pay", intent="opłata za domenę", amount=49.0)

    assert "płatnoś" in caught.value.prompt.lower()
    pending = runtime.approvals.pending()
    assert len(pending) == 1 and pending[0].id == caught.value.request_id
    assert runtime.audit.recent(1)[0].outcome == "awaiting_approval"


async def test_approval_lets_the_same_action_through_once(runtime: Runtime) -> None:
    with pytest.raises(ApprovalRequired) as caught:
        await runtime.perform_tool("pay", amount=49.0)
    runtime.approvals.resolve(caught.value.request_id, True, by="user")

    result = await runtime.perform_tool("pay", amount=49.0)
    assert result.ok and result.value == {"paid": 49.0}

    # A second identical action must ask again: one yes, one action.
    with pytest.raises(ApprovalRequired):
        await runtime.perform_tool("pay", amount=49.0)


async def test_approval_does_not_transfer_to_a_different_amount(runtime: Runtime) -> None:
    with pytest.raises(ApprovalRequired) as caught:
        await runtime.perform_tool("pay", amount=10.0)
    runtime.approvals.resolve(caught.value.request_id, True)

    with pytest.raises(ApprovalRequired):
        await runtime.perform_tool("pay", amount=9999.0)


async def test_refusal_is_respected_and_explained_kindly(runtime: Runtime) -> None:
    with pytest.raises(ApprovalRequired) as caught:
        await runtime.perform_tool("publish", text="wpis")
    runtime.approvals.resolve(caught.value.request_id, False)

    with pytest.raises(ApprovalDenied) as denied:
        await runtime.perform_tool("publish", text="wpis")
    assert "nie robię" in denied.value.explain().lower()


async def test_approval_survives_a_restart(paths, db, bus, runtime: Runtime) -> None:
    """A yes may arrive after a reboot; it is a row, not a promise in memory."""
    from garis.runtime import ApprovalBroker

    with pytest.raises(ApprovalRequired) as caught:
        await runtime.perform_tool("pay", amount=120.0)
    request_id = caught.value.request_id

    fresh_broker = ApprovalBroker(db, bus)          # as if the process restarted
    assert [r.id for r in fresh_broker.pending()] == [request_id]
    fresh_broker.resolve(request_id, True, by="mobile")

    runtime.approvals = ApprovalBroker(db, bus)
    result = await runtime.perform_tool("pay", amount=120.0)
    assert result.ok

    stored = runtime.approvals.get(request_id)
    assert stored is not None
    assert stored.state is ApprovalState.APPROVED and stored.resolved_by == "mobile"


async def test_denied_by_policy_raises(runtime: Runtime, paths) -> None:
    with pytest.raises(PolicyDenied):
        await runtime.perform_tool("note", text=str(paths.key_file))


# ----------------------------------------------------------------- secret handling


async def test_vault_reference_is_substituted_only_at_call_time(
    runtime: Runtime, vault
) -> None:
    vault.set("demo_token", "sekret-abc-123")
    result = await runtime.perform_tool("call_api", token="vault://demo_token")
    assert result.ok
    assert result.value == {"used_token": "sekret-abc-123"}

    entry = runtime.audit.recent(1)[0]
    assert entry.params["token"] == "vault://demo_token", "audyt trzyma referencję"
    assert "sekret-abc-123" not in str(entry.params)


async def test_missing_secret_fails_clearly(runtime: Runtime) -> None:
    result = await runtime.perform_tool("call_api", token="vault://nie_ma")
    assert not result.ok and result.error_kind == "missing_secret"
    assert not result.retryable


async def test_credential_shaped_values_are_redacted_in_audit(runtime: Runtime) -> None:
    @runtime.registry.tool(
        "login",
        "Loguje się.",
        params={"password": __import__("garis.runtime", fromlist=["ParamSpec"]).ParamSpec(
            "string", required=True)},
    )
    async def login(ctx, password):  # type: ignore[no-untyped-def]
        return {"ok": True}

    await runtime.perform_tool("login", password="tajne-haslo-123")
    entry = runtime.audit.recent(1)[0]
    assert entry.params["password"] == REDACTED


# -------------------------------------------------------------------- nesting


async def test_nested_tool_call_is_also_policed(runtime: Runtime) -> None:
    """A tool reaching for another tool re-enters the same gates — no side door."""

    @runtime.registry.tool("wrapper", "Woła płatność przez ctx.perform.", params={})
    async def wrapper(ctx):  # type: ignore[no-untyped-def]
        return await ctx.perform("pay", amount=5.0)

    with pytest.raises(ApprovalRequired):
        await runtime.perform_tool("wrapper")


async def test_progress_is_emitted_but_silent(runtime: Runtime, bus) -> None:
    sub = bus.subscribe("task.progress", "notice")
    await runtime.perform_tool("note", text="x")
    events = bus.recent("task.progress")
    assert events and events[-1].payload["message"] == "piszę"
    assert not bus.recent("notice"), "zwykła praca nie powiadamia użytkownika"
    sub.close()


async def test_capabilities_report(runtime: Runtime) -> None:
    described = runtime.describe_capabilities()
    assert described["tools_total"] >= 6
    assert "payment" in described["policy"]["confirm_effects"]
