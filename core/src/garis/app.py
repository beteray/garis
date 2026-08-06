"""Wiring: build one GARIS instance from config to task supervisor.

Every other module in this package is a piece; this is where the pieces become
a running agent. Tests build a :class:`Garis` against a temporary ``GARIS_HOME``
with ``include_fake=True`` and get the exact same object graph the CLI and the
future API server use.
"""

from __future__ import annotations

from dataclasses import dataclass

from .agent import Planner, Verifier
from .capabilities import REGISTRY as CAPABILITIES
from .capabilities.native import NativeCapabilityExecutor
from .config import Config, load_config
from .conversation import Answer, Conversation
from .crypto import SecretBox, load_or_create_master_key, subkey
from .events import EventBus
from .kernel.contracts import RuntimeProfile
from .kernel.effects import EffectStore
from .kernel.outbox import EventOutbox
from .memory import MemoryService
from .models import HealthMonitor, ModelRouter, ProviderPool, build_router
from .net import HttpClient
from .notifications import NotificationGate
from .paths import Paths
from .profiles import ProfileContents, check_requested_doubles, inspect, validate
from .runtime import (
    ApprovalBroker,
    AuditLog,
    LeaseManager,
    PolicyEngine,
    Runtime,
    TargetResolver,
    ToolRegistry,
)
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
    conversation: Conversation
    profile: RuntimeProfile = RuntimeProfile.PRODUCTION
    contents: ProfileContents | None = None

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

    async def say(self, text: str, *, origin: str = "user", target: str = "local") -> Answer:
        """The one door for anything a person says.

        Not everything said is work. This decides, and only then does a task
        exist — which is the difference between answering "cześć" and filing it.
        """
        return await self.conversation.say(text, origin=origin, target=target)

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
    profile: RuntimeProfile = RuntimeProfile.PRODUCTION,
) -> Garis:
    # Refused before anything is constructed, so a process that asked for a stub
    # it may not have never gets far enough to answer a question with one.
    check_requested_doubles(profile, include_fake)
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
    effects = EffectStore(db)
    outbox = EventOutbox(db, bus)
    runtime = Runtime(
        registry=registry, policy=policy, approvals=approvals, audit=audit,
        leases=leases, bus=bus, paths=paths, config=config, db=db,
        vault=vault, memory=memory, http=http, effects=effects, outbox=outbox,
        profile=profile,
    )
    # Both kinds of target now reach the same envelope: the runtime registered
    # the legacy executor when it built the runner, and this adds the native one.
    runtime.runner.register_executor(
        "capability", NativeCapabilityExecutor(CAPABILITIES)
    )
    CAPABILITIES.bind(runtime.runner)

    # Anything reserved and never settled is the footprint of a process that
    # died mid-action. It comes back as uncertain, not as free to retry.
    effects.sweep_unsettled()

    health = HealthMonitor(http=http, bus=bus)
    router = build_router(config, vault=vault, http=http, bus=bus,
                          include_fake=include_fake, health=health)
    providers = ProviderPool(router, config, vault=vault, http=http, bus=bus,
                             monitor=health, include_fake=include_fake)
    settings = SettingsService(config, paths, bus=bus)
    planner = Planner(
        router, registry, memory=memory, language=config.identity.language or "pl",
        # One resolver, knowing both catalogues, shared by the planner and
        # by `garis do --dry-run`.
        resolver=TargetResolver(registry, CAPABILITIES),
    )
    verifier = Verifier(router, language=config.identity.language or "pl")

    store = TaskStore(db)
    tasks = TaskSupervisor(store, runtime, planner, verifier, bus=bus, config=config)
    conversation = Conversation(tasks, router, registry, config, bus=bus)

    # The gate is what makes ctx.note() honest: tools raise candidates, and this
    # decides which ones actually interrupt a person.
    notifications = NotificationGate(config.notifications, bus=bus)
    voice = VoiceService(config=config.voice, bus=bus, router=router)

    # Startup validation, not a promise in a docstring: a PRODUCTION process
    # that ended up holding a stub provider or a fixture capability refuses to
    # start rather than answering questions with one.
    contents = validate(
        inspect(profile, providers=router.providers, capabilities=CAPABILITIES.all())
    )

    return Garis(
        paths=paths, config=config, bus=bus, db=db, vault=vault, memory=memory,
        http=http, registry=registry, runtime=runtime, router=router,
        planner=planner, verifier=verifier, tasks=tasks,
        notifications=notifications, voice=voice, settings=settings,
        providers=providers, conversation=conversation,
        profile=profile, contents=contents,
    )


def _box(key: bytes) -> SecretBox:
    return SecretBox(key)


__all__ = ["Garis", "build"]
