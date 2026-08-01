"""Wiring: build one GARIS instance from config to task supervisor.

Every other module in this package is a piece; this is where the pieces become
a running agent. Tests build a :class:`Garis` against a temporary ``GARIS_HOME``
with ``include_fake=True`` and get the exact same object graph the CLI and the
future API server use.
"""

from __future__ import annotations

from dataclasses import dataclass

from .agent import Planner, Verifier
from .config import Config, load_config
from .crypto import SecretBox, load_or_create_master_key, subkey
from .events import EventBus
from .memory import MemoryService
from .models import HealthMonitor, ModelRouter, ProviderPool, build_router
from .net import HttpClient
from .notifications import NotificationGate
from .paths import Paths
from .runtime import ApprovalBroker, AuditLog, LeaseManager, PolicyEngine, Runtime, ToolRegistry
from .settings import SettingsService
from .store import SCHEMA, Database
from .tasks import TaskStore, TaskSupervisor
from .tools import DESKTOP_MODULES, register_all
from .vault import Vault
from .voice import VoiceService


@dataclass(slots=True)
class Garis:
    """Everything needed to accept a goal and drive it to a verified result."""

    paths: Paths
    config: Config
    bus: EventBus
    db: Database
    vault: Vault
    memory: MemoryService
    http: HttpClient
    registry: ToolRegistry
    runtime: Runtime
    router: ModelRouter
    planner: Planner
    verifier: Verifier
    tasks: TaskSupervisor
    notifications: NotificationGate
    voice: VoiceService
    settings: SettingsService
    providers: ProviderPool

    async def start(self) -> None:
        """Begin the background work a long-lived GARIS needs.

        Not done in :func:`build` because building is synchronous and a one-shot
        ``garis do`` has nothing to watch. Everything that keeps state true over
        hours — the settings watcher, the hourly provider check — starts here.
        """
        await self.providers.rebuild(reason="start")
        self.settings.start()
        self.providers.start()

    async def stop(self) -> None:
        await self.settings.stop()
        await self.providers.stop()

    async def reload_providers(self, *, reason: str = "manual") -> None:
        """Rebuild the model layer now and wait for it.

        Awaited rather than left to the event loop where the caller needs the
        result to be true immediately: after ``POST /api/vault`` the very next
        request may be "run this task", and it must find the new key.
        """
        await self.providers.rebuild(reason=reason)

    async def do(
        self,
        goal: str,
        *,
        criteria: tuple[str, ...] = (),
        origin: str = "user",
        target: str = "local",
        wait: bool = True,
        timeout: float | None = None,
    ):
        """Submit a goal and, by default, wait for it to stop being active."""
        task = self.tasks.submit(goal, criteria=criteria, origin=origin, target=target)
        if wait:
            task = await self.tasks.wait(task.id, timeout=timeout)
        return task

    def close(self) -> None:
        self.vault.close()
        self.db.close()


def build(
    home: str | None = None,
    *,
    passphrase: str | None = None,
    include_fake: bool = False,
    tool_modules: tuple = DESKTOP_MODULES,
) -> Garis:
    config, paths = load_config(home)

    master = load_or_create_master_key(paths.key_file, passphrase=passphrase)
    db = Database(paths.state_db, SCHEMA)
    bus = EventBus()
    # The vault takes the bus so that saving a key announces itself; that
    # announcement is what makes a new provider work without a restart.
    vault = Vault.open(paths.vault_db, _box(subkey(master, "vault")), bus=bus)
    memory = MemoryService(db, _box(subkey(master, "memory")), bus=bus, vault=vault)
    http = HttpClient()

    registry = ToolRegistry()
    register_all(registry, tool_modules)

    policy = PolicyEngine(config.autonomy, paths)
    approvals = ApprovalBroker(db, bus)
    audit = AuditLog(db)
    leases = LeaseManager()
    runtime = Runtime(
        registry=registry, policy=policy, approvals=approvals, audit=audit,
        leases=leases, bus=bus, paths=paths, config=config, db=db,
        vault=vault, memory=memory, http=http,
    )

    health = HealthMonitor(http=http, bus=bus)
    router = build_router(config, vault=vault, http=http, bus=bus,
                          include_fake=include_fake, health=health)
    providers = ProviderPool(router, config, vault=vault, http=http, bus=bus,
                             monitor=health, include_fake=include_fake)
    settings = SettingsService(config, paths, bus=bus)
    planner = Planner(router, registry, memory=memory, language=config.identity.language or "pl")
    verifier = Verifier(router, language=config.identity.language or "pl")

    store = TaskStore(db)
    tasks = TaskSupervisor(store, runtime, planner, verifier, bus=bus, config=config)

    # The gate is what makes ctx.note() honest: tools raise candidates, and this
    # decides which ones actually interrupt a person.
    notifications = NotificationGate(config.notifications, bus=bus)
    voice = VoiceService(config=config.voice, bus=bus, router=router)

    return Garis(
        paths=paths, config=config, bus=bus, db=db, vault=vault, memory=memory,
        http=http, registry=registry, runtime=runtime, router=router,
        planner=planner, verifier=verifier, tasks=tasks,
        notifications=notifications, voice=voice, settings=settings,
        providers=providers,
    )


def _box(key: bytes) -> SecretBox:
    return SecretBox(key)


__all__ = ["Garis", "build"]
