"""Shared fixtures.

Every test gets an isolated ``GARIS_HOME`` in a temp directory and the fake model
provider, so the suite never touches the developer's real state, never reaches the
network and never needs an API key.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from garis import app as garis_app
from garis.capabilities.native import NativeCapabilityExecutor
from garis.capabilities.registry import CapabilityRegistry
from garis.config import Config
from garis.crypto import SecretBox, load_or_create_master_key, subkey
from garis.events import EventBus
from garis.kernel.contracts import RuntimeProfile
from garis.kernel.effects import EffectStore
from garis.kernel.outbox import EventOutbox
from garis.memory import MemoryService
from garis.paths import Paths
from garis.runtime import (
    ApprovalBroker,
    AuditLog,
    Effect,
    LeaseManager,
    ParamSpec,
    PolicyEngine,
    Runtime,
    ToolRegistry,
)
from garis.runtime.runner import CapabilityRunner
from garis.store import SCHEMA, Database
from garis.vault import Vault


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "garis-home"
    monkeypatch.setenv("GARIS_HOME", str(target))
    return target


@pytest.fixture
def paths(home: Path) -> Paths:
    return Paths.resolve().ensure()


@pytest.fixture
def master_key(paths: Paths) -> bytes:
    return load_or_create_master_key(paths.key_file, use_dpapi=False)


@pytest.fixture
def db(paths: Paths) -> Iterator[Database]:
    database = Database(paths.state_db, SCHEMA)
    yield database
    database.close()


@pytest.fixture
def vault(paths: Paths, master_key: bytes) -> Iterator[Vault]:
    box = SecretBox(subkey(master_key, "vault"))
    opened = Vault.open(paths.vault_db, box)
    yield opened
    opened.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def memory(db: Database, master_key: bytes, bus: EventBus, vault: Vault) -> MemoryService:
    return MemoryService(db, SecretBox(subkey(master_key, "memory")), bus=bus, vault=vault)


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def flaky_control() -> dict[str, int]:
    """Controls the ``flaky`` tool: it fails until its call count reaches ``succeed_on``.

    A fixture rather than a module global — pytest imports this file as top-level
    ``conftest``, so ``from tests.conftest import ...`` would hand a test a second
    copy of the module and silently lose the setting.
    """
    return {"calls": 0, "succeed_on": 2}


@pytest.fixture
def registry(flaky_control: dict[str, int]) -> ToolRegistry:
    """A tiny registry with one tool per interesting effect class."""
    reg = ToolRegistry()

    @reg.tool(
        "note",
        "Zapisuje notatkę.",
        params={"text": ParamSpec("string", required=True)},
        effects=[Effect.WRITE],
        resources=lambda p: ["file:notes.txt"],
    )
    async def note(ctx, text):  # type: ignore[no-untyped-def]
        ctx.progress("piszę")
        return {"written": text}

    @reg.tool(
        "look",
        "Czyta stan.",
        params={"what": ParamSpec("string", default="all")},
        effects=[Effect.READ],
        resources=lambda p: ["file:notes.txt"],
    )
    async def look(ctx, what="all"):  # type: ignore[no-untyped-def]
        return {"seen": what}

    @reg.tool(
        "pay",
        "Płaci za coś.",
        params={"amount": ParamSpec("float", required=True)},
        effects=[Effect.PAYMENT],
    )
    async def pay(ctx, amount):  # type: ignore[no-untyped-def]
        return {"paid": amount}

    @reg.tool(
        "publish",
        "Publikuje treść.",
        params={"text": ParamSpec("string", required=True)},
        effects=[Effect.PUBLISH],
    )
    async def publish(ctx, text):  # type: ignore[no-untyped-def]
        return {"published": text}

    @reg.tool(
        "call_api",
        "Woła API z sekretem.",
        params={"token": ParamSpec("string", required=True)},
        effects=[Effect.NETWORK],
    )
    async def call_api(ctx, token):  # type: ignore[no-untyped-def]
        return {"used_token": token}

    @reg.tool(
        "flaky",
        "Psuje się, dopóki nie zadziała.",
        params={},
        effects=[Effect.READ],
    )
    async def flaky(ctx):  # type: ignore[no-untyped-def]
        from garis.errors import ExecutionError

        flaky_control["calls"] += 1
        if flaky_control["calls"] < flaky_control["succeed_on"]:
            raise ExecutionError("chwilowa awaria", retryable=True)
        return {"calls": flaky_control["calls"]}

    @reg.tool("slow", "Trwa dłużej niż wolno.", params={}, effects=[Effect.READ], timeout=0.05)
    async def slow(ctx):  # type: ignore[no-untyped-def]
        import asyncio

        await asyncio.sleep(5)
        return "nigdy"

    return reg


@pytest.fixture
def runtime(
    registry: ToolRegistry,
    config: Config,
    paths: Paths,
    db: Database,
    bus: EventBus,
    vault: Vault,
    memory: MemoryService,
) -> Runtime:
    return Runtime(
        registry=registry,
        policy=PolicyEngine(config.autonomy, paths),
        approvals=ApprovalBroker(db, bus),
        audit=AuditLog(db),
        leases=LeaseManager(),
        bus=bus,
        paths=paths,
        config=config,
        db=db,
        vault=vault,
        memory=memory,
        profile=RuntimeProfile.TEST,
    )


@pytest.fixture
def capability_runner(
    config: Config, paths: Paths, db: Database, bus: EventBus
) -> CapabilityRunner:
    """The one envelope, wired for capabilities and nothing else.

    Built here rather than by the catalogue: a registry that can construct its
    own runner, effect store and policy engine is a second execution path with a
    different name on it.
    """
    return CapabilityRunner(
        policy=PolicyEngine(config.autonomy, paths),
        approvals=ApprovalBroker(db, bus),
        audit=AuditLog(db),
        effects=EffectStore(db),
        outbox=EventOutbox(db, bus),
        leases=LeaseManager(),
        profile=RuntimeProfile.TEST,
    )


def bind(registry: CapabilityRegistry, runner: CapabilityRunner) -> CapabilityRegistry:
    """Attach a catalogue to the envelope, the way ``app.build`` does."""
    runner.register_executor("capability", NativeCapabilityExecutor(registry))
    registry.bind(runner)
    return registry


@pytest.fixture
def garis(home: Path) -> Iterator[garis_app.Garis]:
    """A whole GARIS on a temp home, with the fake provider standing in for cloud."""
    instance = garis_app.build(include_fake=True, profile=RuntimeProfile.TEST)
    yield instance
    instance.close()


def env_home() -> str:
    return os.environ["GARIS_HOME"]
