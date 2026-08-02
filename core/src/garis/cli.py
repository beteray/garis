"""``garis`` — the command line surface.

Stage 1 has no GUI, so this is how GARIS is actually used and demonstrated. It
follows the same rule as the rest of the product: you state an outcome
(``garis do "..."``), and GARIS decides the method. Everything else here is
inspection — what it remembers, what it did, what it can do, what it is waiting
for.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from . import __version__
from .app import Garis, build
from .console import use_utf8
from .errors import GarisError
from .events import Topic
from .memory import MemoryKind
from .tasks import TaskState

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2
EXIT_ERROR = 3


# --------------------------------------------------------------------- helpers


def _out(text: str = "") -> None:
    # flush, always: the desktop shell starts `garis serve` and reads the address
    # and token line by line off a pipe. Python block-buffers a pipe, and `serve`
    # never exits, so an unflushed handshake is one that never arrives — the
    # window waits for a line that is sitting in a buffer eight kilobytes from
    # being sent. The same applies to progress during a long task.
    print(text, flush=True)


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if sys.stdout.isatty() else text


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def _stamp(moment: float | None) -> str:
    if not moment:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(moment))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


# ------------------------------------------------------------------- commands


async def cmd_do(garis: Garis, args: argparse.Namespace) -> int:
    goal = " ".join(args.goal).strip()
    if not goal:
        _out("Podaj cel, np.: garis do \"sprawdź, co zajmuje miejsce na dysku\"")
        return EXIT_ERROR

    if args.dry_run:
        # "What would you do, and what would it touch?" — the trust-building view.
        from .agent import Goal, describe_plan

        plan = await garis.planner.make_plan(Goal(goal, criteria=tuple(args.criteria or ())))
        if plan.blocked:
            _out(plan.question)
            return EXIT_BLOCKED
        _out(_bold("Plan (nic nie zostało wykonane):"))
        _out(describe_plan(plan))
        effects: set[str] = set()
        for step in plan.steps:
            if garis.registry.has(step.tool):
                effects |= {e.label_pl for e in garis.registry.get(step.tool).effects}
        if effects:
            _out(_dim("Dotknie: " + ", ".join(sorted(effects))))
        return EXIT_OK

    follow = _follow_progress(garis) if args.verbose else None
    if args.criteria:
        # Stated acceptance criteria are a promise that there is work; nothing
        # to classify.
        task = garis.tasks.submit(
            goal, criteria=tuple(args.criteria), target=args.on or "local"
        )
    else:
        answer = await garis.say(goal, target=args.on or "local")
        if answer.task is None:
            if follow is not None:
                follow.cancel()
            _out(answer.text)
            return EXIT_OK
        task = answer.task
    _out(_dim(f"[{task.id}] przyjęte"))

    record = await garis.tasks.wait(task.id, timeout=args.timeout)
    if follow is not None:
        follow.cancel()

    report = record.report or {}
    if record.state is TaskState.BLOCKED:
        _out(report.get("short") or "Potrzebuję odpowiedzi, żeby kontynuować.")
        pending = garis.runtime.approvals.pending(task_id=task.id)
        if pending:
            _out(_dim(f"Zatwierdź: garis approve {pending[0].id}"))
        return EXIT_BLOCKED

    if record.state is TaskState.PENDING or record.state is TaskState.RUNNING:
        _out(_dim(f"Nadal pracuję. Podgląd: garis task {task.id}"))
        return EXIT_OK

    _out(report.get("short") or record.error or "(bez raportu)")
    if args.verbose and report.get("details"):
        _out()
        _out(_dim(report["details"]))
    if garis.config.dev.developer_mode and record.result:
        _out()
        _out(_dim(_json(record.result)))
    return EXIT_OK if record.state is TaskState.FINISHED else EXIT_FAILED


def _follow_progress(garis: Garis) -> asyncio.Task[None]:
    """Print progress lines while a task runs — opt-in, never the default."""
    subscription = garis.bus.subscribe(Topic.TASK_STEP, Topic.TASK_PROGRESS, Topic.NOTICE)

    async def pump() -> None:
        try:
            async for event in subscription:
                if event.topic == Topic.TASK_STEP:
                    _out(_dim(f"  → {event.payload.get('tool')}: "
                              f"{event.payload.get('purpose') or ''}".rstrip()))
                else:
                    message = event.payload.get("message")
                    if message:
                        _out(_dim(f"  · {message}"))
        except asyncio.CancelledError:
            pass
        finally:
            subscription.close()

    return asyncio.ensure_future(pump())


async def cmd_tasks(garis: Garis, args: argparse.Namespace) -> int:
    state = TaskState(args.state) if args.state else None
    records = garis.tasks.store.list(state=state, active_only=args.active, limit=args.limit)
    if not records:
        _out("Brak zadań.")
        return EXIT_OK
    for record in records:
        _out(record.summary_line())
    return EXIT_OK


async def cmd_task(garis: Garis, args: argparse.Namespace) -> int:
    record = garis.tasks.store.get(args.id)
    if record is None:
        _out(f"Nie ma zadania {args.id}.")
        return EXIT_ERROR

    _out(f"{_bold(record.goal)}")
    _out(_dim(f"{record.state.value} · utworzone {_stamp(record.created_at)}"
              f" · zmienione {_stamp(record.updated_at)}"))
    if record.criteria:
        _out("Kryteria: " + "; ".join(record.criteria))
    if record.report:
        _out()
        _out(record.report.get("short", ""))
        if record.report.get("details"):
            _out(_dim(record.report["details"]))
    steps = garis.tasks.steps(record.id)
    if steps:
        _out()
        _out(_bold("Kroki:"))
        for step in steps:
            mark = {"done": "✓", "failed": "✗", "running": "▶"}.get(step.state.value, "…")
            _out(f"  {mark} {step.tool} {_dim(step.error[:80] if step.error else '')}".rstrip())
    if args.audit:
        _out()
        _out(_bold("Audyt:"))
        for entry in garis.runtime.audit.for_task(record.id):
            _out(_dim(f"  {_stamp(entry.at)} {entry.tool} → {entry.outcome}"))
    return EXIT_OK


async def cmd_stop(garis: Garis, args: argparse.Namespace) -> int:
    stopped = garis.tasks.stop(args.id)
    _out("Zatrzymane." if stopped else "To zadanie już nie działa.")
    return EXIT_OK if stopped else EXIT_ERROR


async def cmd_approvals(garis: Garis, args: argparse.Namespace) -> int:
    pending = garis.runtime.approvals.pending()
    if not pending:
        _out("Nie czekam na żadną zgodę.")
        return EXIT_OK
    for request in pending:
        _out(f"{request.id}  {request.prompt}")
        _out(_dim(f"    zadanie {request.task_id or '—'} · {', '.join(request.effects)}"))
    return EXIT_OK


async def cmd_approve(garis: Garis, args: argparse.Namespace) -> int:
    return await _resolve(garis, args.id, approved=True)


async def cmd_reject(garis: Garis, args: argparse.Namespace) -> int:
    return await _resolve(garis, args.id, approved=False)


async def _resolve(garis: Garis, approval_id: str, *, approved: bool) -> int:
    try:
        request = garis.runtime.approvals.resolve(approval_id, approved, by="cli")
    except GarisError as exc:
        _out(exc.explain())
        return EXIT_ERROR

    _out("Zatwierdzone." if approved else "Odrzucone.")
    if request.task_id:
        # Approving is only half the story: the task has to actually continue.
        garis.tasks.resume(request.task_id)
        record = await garis.tasks.wait(request.task_id, timeout=None)
        if record.report:
            _out(record.report.get("short", ""))
        return EXIT_OK if record.state is TaskState.FINISHED else EXIT_FAILED
    return EXIT_OK


async def cmd_memory(garis: Garis, args: argparse.Namespace) -> int:
    memory = garis.memory
    match args.action:
        case "list":
            records = memory.list(kind=args.kind, limit=args.limit)
            if not records:
                _out("Pamięć jest pusta.")
            for record in records:
                pin = "★" if record.pinned else " "
                _out(f"{pin} {record.id[:8]}  [{record.kind.value}] {record.preview}")
            return EXIT_OK
        case "search":
            for record in memory.search(" ".join(args.text), limit=args.limit):
                _out(f"  {record.id[:8]}  [{record.kind.value}] {record.content}")
            return EXIT_OK
        case "remember":
            record = memory.remember(" ".join(args.text), kind=args.kind or MemoryKind.FACT)
            _out(f"Zapamiętane ({record.id[:8]}).")
            return EXIT_OK
        case "forget":
            if args.id:
                _out("Usunięte." if memory.forget(args.id) else "Nie ma takiego wpisu.")
                return EXIT_OK
            removed = memory.forget_matching(" ".join(args.text))
            _out(f"Usunięte wpisy: {len(removed)}." if removed else "Nic nie dopasowałem.")
            return EXIT_OK
        case "stats":
            _out(_json(memory.stats()))
            return EXIT_OK
    return EXIT_ERROR


async def cmd_vault(garis: Garis, args: argparse.Namespace) -> int:
    match args.action:
        case "list":
            secrets = garis.vault.list()
            if not secrets:
                _out("Sejf jest pusty.")
            for info in secrets:
                _out(f"  {info.name}  {_dim(info.note or info.kind)}")
            return EXIT_OK
        case "set":
            import getpass

            value = args.value or getpass.getpass(f"Wartość dla {args.name}: ")
            garis.memory.remember_secret(args.name, value, note=args.note or "")
            _out(f"Zapisane w sejfie jako vault://{args.name}.")
            return EXIT_OK
        case "delete":
            _out("Usunięte." if garis.vault.delete(args.name) else "Nie ma takiego wpisu.")
            return EXIT_OK
    return EXIT_ERROR


async def cmd_doctor(garis: Garis, args: argparse.Namespace) -> int:
    """What this host can do, and what GARIS will and will not do on it."""
    import platform

    _out(_bold(f"GARIS {__version__}"))
    _out(f"System:     {platform.platform()}")
    _out(f"Katalog:    {garis.paths.home}")
    _out(f"Onboarding: {'zrobiony' if garis.config.identity.onboarded else 'jeszcze nie'}")

    capabilities = garis.runtime.describe_capabilities()
    _out()
    _out(_bold("Narzędzia"))
    _out(f"  dostępne tu: {capabilities['tools_available']} z {capabilities['tools_total']}")
    for category, count in capabilities["categories"].items():
        _out(_dim(f"    {category}: {count}"))
    if capabilities["unsupported_here"]:
        _out(_dim(f"  niedostępne na tym systemie: "
                  f"{', '.join(capabilities['unsupported_here'])}"))

    _out()
    _out(_bold("Modele"))
    # Doctor is the one place that should never report a guess: check first.
    await garis.providers.refresh(force=True)
    described = garis.router.describe()
    for provider in described["providers"]:
        mark = "✓" if provider["available"] else "—"
        _out(f"  {mark} {provider['name']}: {len(provider['models'])} modeli"
             f" — {provider['reason']}")
    if not any(p["available"] and p["name"] != "fake" for p in described["providers"]):
        _out(_dim("  Brak kluczy API — działam na wbudowanej atrapie."))
        _out(_dim("  Dodaj klucz: garis vault set openai_api_key"))
    _out(_dim(f"  prywatność: {described['privacy']}, wydane: {described['spend']}"))

    _out()
    _out(_bold("Zgody"))
    policy = capabilities["policy"]
    _out(f"  pytam przed: {', '.join(policy['confirm_effects'])}")
    _out(f"  ostrzegam powyżej: {policy['download_notice']}")
    for never in policy["never"]:
        _out(_dim(f"  nigdy: {never}"))

    tasks = garis.tasks.store.list(active_only=True)
    _out()
    _out(_bold("Stan"))
    _out(f"  aktywne zadania: {len(tasks)}")
    _out(f"  oczekujące zgody: {len(garis.runtime.approvals.pending())}")
    _out(f"  pamięć: {garis.memory.stats()['total']} wpisów")
    _out(f"  sejf: {len(garis.vault.list())} dostępów")
    return EXIT_OK


async def cmd_activity(garis: Garis, args: argparse.Namespace) -> int:
    """Answers "co zrobiłeś przez ostatnią godzinę?"."""
    summary = garis.runtime.audit.activity_since(time.time() - args.minutes * 60)
    if not summary["actions"]:
        _out(f"Przez ostatnie {args.minutes} min nic nie robiłem.")
        return EXIT_OK
    _out(f"Przez ostatnie {args.minutes} min: {summary['actions']} operacji "
         f"w {len(summary['tasks'])} zadaniach.")
    for tool, count in summary["top_tools"]:
        _out(_dim(f"  {tool} x{count}"))
    if summary["failures"]:
        _out(_dim(f"  nieudanych operacji: {summary['failures']} (naprawiane samodzielnie)"))
    if summary["waiting_for_approval"]:
        _out(f"  czekam na zgodę: {summary['waiting_for_approval']}")
    return EXIT_OK


async def cmd_tools(garis: Garis, args: argparse.Namespace) -> int:
    for category, specs in sorted(garis.registry.by_category().items()):
        _out(_bold(category))
        for spec in specs:
            effects = ", ".join(sorted(e.value for e in spec.effects))
            _out(f"  {spec.name:<24} {spec.summary}")
            if args.verbose and effects:
                _out(_dim(f"    skutki: {effects}"))
    return EXIT_OK


async def cmd_config(garis: Garis, args: argparse.Namespace) -> int:
    if args.key is None:
        _out(_json(garis.config.to_dict()))
        return EXIT_OK
    if args.value is None:
        _out(_json(garis.config.get(args.key)))
        return EXIT_OK
    garis.config.set(args.key, args.value)
    garis.config.save(garis.paths)
    _out(f"{args.key} = {garis.config.get(args.key)}")
    return EXIT_OK


async def cmd_serve(garis: Garis, args: argparse.Namespace) -> int:
    """Run in the background, the way the tray app will: recover, serve, wait.

    Recovery comes first: anything interrupted by a reboot resumes before the API
    starts accepting new work, so a client that connects immediately sees the true
    state rather than an empty one.
    """
    from .api import ApiServer

    recovered = await garis.tasks.recover()
    if recovered:
        _out(_dim(f"Wznowiłem {len(recovered)} zadań po restarcie."))

    # Providers checked and watched before the first request arrives: a client
    # that connects immediately must see measured state, not assumptions.
    await garis.start()

    api: ApiServer | None = None
    if not args.no_api:
        api = ApiServer(garis, host=args.host, port=args.port)
        await api.start()
        _out(f"API: {api.url}")
        if args.print_token:
            _out(_dim(f"Token: {api.token}"))
        else:
            _out(_dim("Token: garis serve --print-token (albo garis vault list)"))

    _out(f"GARIS działa. Katalog: {garis.paths.home}. Zakończ: Ctrl+C.")
    subscription = garis.bus.subscribe(Topic.TASK_FINISHED, Topic.TASK_FAILED,
                                       Topic.TASK_BLOCKED, Topic.NOTICE)
    try:
        async for event in subscription:
            payload = event.payload
            if event.topic == Topic.NOTICE:
                if not payload.get("silent"):
                    _out(payload.get("message", ""))
            elif event.topic == Topic.TASK_BLOCKED:
                _out(f"[{payload.get('task_id')}] {payload.get('question', 'czekam na zgodę')}")
            else:
                _out(f"[{payload.get('task_id')}] {payload.get('report', '')}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        subscription.close()
        await garis.stop()
        if api is not None:
            await api.stop()
    return EXIT_OK


# --------------------------------------------------------------------- parsing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garis",
        description="GARIS — powiedz, jaki chcesz rezultat. Resztę zrobię sam.",
    )
    parser.add_argument("--home", help="Katalog stanu (domyślnie %%LOCALAPPDATA%%\\GARIS)")
    parser.add_argument("--version", action="version", version=f"GARIS {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    do = sub.add_parser("do", help="Zleć cel do wykonania")
    do.add_argument("goal", nargs="+")
    do.add_argument("-c", "--criteria", action="append", help="Kryterium ukończenia")
    do.add_argument("--on", help="Urządzenie docelowe (np. nazwa serwera)")
    do.add_argument("-v", "--verbose", action="store_true", help="Pokaż postęp i szczegóły")
    do.add_argument("--dry-run", action="store_true",
                    help="Pokaż plan i skutki, nic nie wykonuj")
    do.add_argument("--timeout", type=float, default=None)
    do.set_defaults(handler=cmd_do)

    tasks = sub.add_parser("tasks", help="Lista zadań")
    tasks.add_argument("--state", choices=[s.value for s in TaskState])
    tasks.add_argument("--active", action="store_true")
    tasks.add_argument("--limit", type=int, default=30)
    tasks.set_defaults(handler=cmd_tasks)

    task = sub.add_parser("task", help="Szczegóły zadania")
    task.add_argument("id")
    task.add_argument("--audit", action="store_true", help="Dołącz dziennik audytu")
    task.set_defaults(handler=cmd_task)

    stop = sub.add_parser("stop", help="Zatrzymaj zadanie")
    stop.add_argument("id")
    stop.set_defaults(handler=cmd_stop)

    sub.add_parser("approvals", help="Co czeka na Twoją zgodę").set_defaults(
        handler=cmd_approvals
    )

    approve = sub.add_parser("approve", help="Zatwierdź operację")
    approve.add_argument("id")
    approve.set_defaults(handler=cmd_approve)

    reject = sub.add_parser("reject", help="Odrzuć operację")
    reject.add_argument("id")
    reject.set_defaults(handler=cmd_reject)

    memory = sub.add_parser("memory", help="Co GARIS pamięta")
    memory.add_argument("action", choices=["list", "search", "remember", "forget", "stats"],
                        nargs="?", default="list")
    memory.add_argument("text", nargs="*")
    memory.add_argument("--id")
    memory.add_argument("--kind", choices=[k.value for k in MemoryKind])
    memory.add_argument("--limit", type=int, default=50)
    memory.set_defaults(handler=cmd_memory)

    vault = sub.add_parser("vault", help="Sejf haseł i kluczy")
    vault.add_argument("action", choices=["list", "set", "delete"], nargs="?", default="list")
    vault.add_argument("name", nargs="?", default="")
    vault.add_argument("value", nargs="?", default="")
    vault.add_argument("--note", default="")
    vault.set_defaults(handler=cmd_vault)

    sub.add_parser("doctor", help="Diagnostyka: co potrafię na tym komputerze").set_defaults(
        handler=cmd_doctor
    )

    activity = sub.add_parser("activity", help="Co robiłem ostatnio")
    activity.add_argument("--minutes", type=int, default=60)
    activity.set_defaults(handler=cmd_activity)

    tools = sub.add_parser("tools", help="Lista dostępnych narzędzi")
    tools.add_argument("-v", "--verbose", action="store_true")
    tools.set_defaults(handler=cmd_tools)

    config = sub.add_parser("config", help="Podejrzyj lub zmień ustawienie")
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
    config.set_defaults(handler=cmd_config)

    serve = sub.add_parser("serve", help="Działaj w tle i wystaw lokalne API")
    serve.add_argument("--host", default=None, help="Domyślnie 127.0.0.1")
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--no-api", action="store_true", help="Bez serwera API")
    serve.add_argument("--print-token", action="store_true",
                       help="Wypisz token API (potrzebny interfejsowi)")
    serve.set_defaults(handler=cmd_serve)

    token = sub.add_parser("token", help="Pokaż token lokalnego API")
    token.set_defaults(handler=cmd_token)

    return parser


async def cmd_token(garis: Garis, args: argparse.Namespace) -> int:
    from .api import ensure_token

    _out(ensure_token(garis))
    return EXIT_OK


async def _run(args: argparse.Namespace) -> int:
    garis = build(args.home, include_fake=True)
    try:
        return await args.handler(garis, args)
    finally:
        garis.close()


def main(argv: list[str] | None = None) -> int:
    # Before the first character leaves this process, including argparse's own
    # error messages: on Windows an unconfigured stdout is cp1250 and the first
    # Polish sentence either raises or arrives unreadable.
    use_utf8()
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        _out("\nPrzerwane.")
        return EXIT_OK
    except GarisError as exc:
        # Blocking errors get a plain sentence; the rest is detail for --verbose.
        _out(exc.explain())
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
