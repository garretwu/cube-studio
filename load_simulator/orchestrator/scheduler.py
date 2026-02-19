"""Load scheduling utilities for the orchestrator."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional


@dataclass
class ScheduledTask:
    """Metadata for a task managed by :class:`LoadScheduler`."""

    name: str
    coroutine_factory: Callable[[], Coroutine[Any, Any, Any]]
    start_delay_seconds: float = 0.0
    repeat_interval_seconds: Optional[float] = None  # None → run once


class LoadScheduler:
    """Simple async scheduler that fires coroutines at configured intervals.

    Usage::

        scheduler = LoadScheduler()
        scheduler.add(ScheduledTask(
            name="inference_burst",
            coroutine_factory=lambda: my_agent.run(duration_seconds=10),
            start_delay_seconds=0.0,
            repeat_interval_seconds=15.0,
        ))
        await scheduler.run(duration_seconds=60)
    """

    def __init__(self) -> None:
        self._tasks: list[ScheduledTask] = []
        self._results: list[tuple[str, Any]] = []

    def add(self, task: ScheduledTask) -> None:
        """Register a :class:`ScheduledTask`."""
        self._tasks.append(task)

    @property
    def results(self) -> list[tuple[str, Any]]:
        """Return (task_name, result) pairs for all completed coroutines."""
        return list(self._results)

    async def run(self, duration_seconds: float) -> None:
        """Run all registered tasks for *duration_seconds*.

        Tasks are started according to their ``start_delay_seconds`` and
        re-fired every ``repeat_interval_seconds`` (if set) until the
        scheduler's deadline is reached.
        """
        deadline = time.monotonic() + duration_seconds
        asyncio_tasks: list[asyncio.Task] = []

        for sched_task in self._tasks:
            t = asyncio.create_task(
                self._run_task(sched_task, deadline)
            )
            asyncio_tasks.append(t)

        await asyncio.gather(*asyncio_tasks, return_exceptions=True)

    async def _run_task(self, sched_task: ScheduledTask, deadline: float) -> None:
        """Fire a single scheduled task, repeating if configured."""
        # Initial delay
        remaining = deadline - time.monotonic()
        if sched_task.start_delay_seconds > 0 and remaining > 0:
            await asyncio.sleep(min(sched_task.start_delay_seconds, remaining))

        while time.monotonic() < deadline:
            try:
                result = await sched_task.coroutine_factory()
                self._results.append((sched_task.name, result))
            except Exception as exc:  # noqa: BLE001
                self._results.append((sched_task.name, exc))

            if sched_task.repeat_interval_seconds is None:
                break  # Run once

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(sched_task.repeat_interval_seconds, remaining))


async def run_with_ramp_up(
    coroutine_factory: Callable[[], Coroutine[Any, Any, Any]],
    target_concurrency: int,
    ramp_seconds: float,
    duration_seconds: float,
) -> list[Any]:
    """Gradually increase concurrency from 1 to *target_concurrency* over *ramp_seconds*.

    Args:
        coroutine_factory:  Callable that returns a new coroutine each call.
        target_concurrency: Final concurrency level.
        ramp_seconds:       Time to reach full concurrency.
        duration_seconds:   Total duration of the run.

    Returns:
        List of results from all coroutines.
    """
    results: list[Any] = []
    deadline = time.monotonic() + duration_seconds
    tasks: list[asyncio.Task] = []
    current_concurrency = 1

    step_interval = ramp_seconds / max(1, target_concurrency - 1)
    next_ramp_time = time.monotonic() + step_interval

    while time.monotonic() < deadline:
        # Ramp up
        now = time.monotonic()
        if now >= next_ramp_time and current_concurrency < target_concurrency:
            current_concurrency += 1
            next_ramp_time += step_interval

        # Ensure we have `current_concurrency` active tasks
        active = [t for t in tasks if not t.done()]
        while len(active) < current_concurrency and time.monotonic() < deadline:
            task = asyncio.create_task(coroutine_factory())
            tasks.append(task)
            active.append(task)

        await asyncio.sleep(0.1)

    # Collect results
    done, _ = await asyncio.wait(tasks, timeout=30.0)
    for task in done:
        try:
            results.append(task.result())
        except Exception as exc:
            results.append(exc)

    return results
