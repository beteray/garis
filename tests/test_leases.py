"""Resource leases: many tasks at once, but never on the same file at once."""

from __future__ import annotations

import asyncio
import os

import pytest

from garis.errors import LeaseTimeout
from garis.runtime import LeaseManager, LeaseMode, Runtime, file_key


async def test_two_writers_on_one_file_are_serialised() -> None:
    leases = LeaseManager()
    order: list[str] = []

    async def writer(name: str, hold: float) -> None:
        async with await leases.acquire(["file:a"], holder=name):
            order.append(f"{name}-start")
            await asyncio.sleep(hold)
            order.append(f"{name}-end")

    await asyncio.gather(writer("t1", 0.05), writer("t2", 0.01))

    # Whoever went first must have finished before the other started.
    assert order[0].endswith("-start") and order[1].endswith("-end")
    assert order[1][:2] == order[0][:2]


async def test_readers_run_concurrently() -> None:
    leases = LeaseManager()
    active = 0
    peak = 0

    async def reader() -> None:
        nonlocal active, peak
        async with await leases.acquire(["file:a"], holder="r", mode=LeaseMode.SHARED):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(reader() for _ in range(4)))
    assert peak == 4, "odczyty nie powinny się blokować"


async def test_writer_waits_for_readers() -> None:
    leases = LeaseManager()
    events: list[str] = []

    async def reader() -> None:
        async with await leases.acquire(["file:a"], holder="r", mode=LeaseMode.SHARED):
            events.append("read-start")
            await asyncio.sleep(0.05)
            events.append("read-end")

    async def writer() -> None:
        await asyncio.sleep(0.01)
        async with await leases.acquire(["file:a"], holder="w",
                                       mode=LeaseMode.EXCLUSIVE):
            events.append("write")

    await asyncio.gather(reader(), writer())
    assert events == ["read-start", "read-end", "write"]


async def test_opposite_acquisition_order_does_not_deadlock() -> None:
    """Keys are sorted before acquisition, so {A,B} and {B,A} cannot hold-and-wait."""
    leases = LeaseManager()
    done: list[str] = []

    async def one() -> None:
        async with await leases.acquire(["file:a", "file:b"], holder="one", timeout=2):
            await asyncio.sleep(0.02)
            done.append("one")

    async def two() -> None:
        async with await leases.acquire(["file:b", "file:a"], holder="two", timeout=2):
            await asyncio.sleep(0.02)
            done.append("two")

    await asyncio.wait_for(asyncio.gather(one(), two()), timeout=3)
    assert sorted(done) == ["one", "two"]


async def test_lease_timeout_is_reported() -> None:
    leases = LeaseManager()
    held = await leases.acquire(["device:microphone"], holder="voice")
    with pytest.raises(LeaseTimeout):
        await leases.acquire(["device:microphone"], holder="other", timeout=0.05)
    held.release()
    # Once released the resource is immediately available again.
    second = await leases.acquire(["device:microphone"], holder="other", timeout=0.05)
    second.release()
    assert not leases.is_busy("device:microphone")


async def test_same_holder_can_reenter() -> None:
    """A task's nested action must not deadlock against the task's own lease."""
    leases = LeaseManager()
    outer = await leases.acquire(["file:a"], holder="task-1")
    inner = await leases.acquire(["file:a"], holder="task-1", timeout=0.5)
    inner.release()
    outer.release()


def test_windows_paths_are_case_insensitive_keys() -> None:
    if os.name == "nt":  # pragma: no cover - only meaningful on Windows
        assert file_key(r"C:\Temp\A.txt") == file_key(r"c:\temp\a.txt")
    else:
        assert file_key("/tmp/a.txt") == file_key("/tmp/../tmp/a.txt")


async def test_runtime_serialises_conflicting_tools(runtime: Runtime) -> None:
    """End-to-end: two writes to the same declared resource do not overlap."""
    overlaps = 0
    active = 0

    @runtime.registry.tool(
        "slow_note",
        "Pisze wolno.",
        params={},
        effects=[__import__("garis.runtime", fromlist=["Effect"]).Effect.WRITE],
        resources=lambda p: ["file:shared.txt"],
    )
    async def slow_note(ctx):  # type: ignore[no-untyped-def]
        nonlocal overlaps, active
        active += 1
        if active > 1:
            overlaps += 1
        await asyncio.sleep(0.03)
        active -= 1
        return "ok"

    results = await asyncio.gather(
        runtime.perform_tool("slow_note", task_id="a"),
        runtime.perform_tool("slow_note", task_id="b"),
    )
    assert all(r.ok for r in results)
    assert overlaps == 0, "dwa zadania weszły jednocześnie na ten sam plik"
