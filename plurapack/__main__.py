"""Production launcher for the Plurapack bot and dashboard.

The two components keep their standalone entry points.  This module supervises
those entry points as child processes so each library remains responsible for
its own startup and shutdown lifecycle.
"""
from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Sequence


Component = tuple[str, tuple[str, ...]]
COMPONENTS: tuple[Component, ...] = (
    ("bot", (sys.executable, "-m", "plurapack.bot")),
    ("dashboard", (sys.executable, "-m", "plurapack.web")),
)
SHUTDOWN_TIMEOUT = 10.0


def _status(message: str) -> None:
    print(f"[Plurapack launcher] {message}", flush=True)


async def _stop_components(
    processes: dict[str, asyncio.subprocess.Process], timeout: float
) -> None:
    running = {name: process for name, process in processes.items()
               if process.returncode is None}
    for name, process in running.items():
        _status(f"Stopping {name}...")
        process.terminate()

    if not running:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*(process.wait() for process in running.values())),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        remaining = {name: process for name, process in running.items()
                     if process.returncode is None}
        for name, process in remaining.items():
            _status(f"{name} did not stop in time; killing it.")
            process.kill()
        await asyncio.gather(*(process.wait() for process in remaining.values()))


async def supervise(
    components: Sequence[Component] = COMPONENTS,
    shutdown_timeout: float = SHUTDOWN_TIMEOUT,
) -> int:
    """Run all critical components until a signal or component exit occurs."""
    loop = asyncio.get_running_loop()
    shutdown_requested = asyncio.Event()
    installed_signals: list[signal.Signals] = []

    def request_shutdown(received: signal.Signals) -> None:
        _status(f"Shutdown requested ({received.name}).")
        shutdown_requested.set()

    for received in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(received, request_shutdown, received)
            installed_signals.append(received)
        except NotImplementedError:  # pragma: no cover - Windows event loops
            pass

    processes: dict[str, asyncio.subprocess.Process] = {}
    waiters: dict[asyncio.Task[int], str] = {}
    shutdown_waiter: asyncio.Task[bool] | None = None
    exit_code = 0
    try:
        _status("Starting Plurapack bot and dashboard.")
        for name, command in components:
            try:
                process = await asyncio.create_subprocess_exec(*command)
            except Exception:
                _status(f"Failed to start critical component {name}; shutting down.")
                raise
            processes[name] = process
            waiters[asyncio.create_task(process.wait())] = name
            _status(f"Started {name} (PID {process.pid}).")

        shutdown_waiter = asyncio.create_task(shutdown_requested.wait())
        done, _ = await asyncio.wait(
            [*waiters, shutdown_waiter], return_when=asyncio.FIRST_COMPLETED
        )
        if shutdown_waiter not in done:
            failed_waiter = next(task for task in done if task in waiters)
            name = waiters[failed_waiter]
            component_code = failed_waiter.result()
            _status(
                f"Critical component {name} exited unexpectedly "
                f"with status {component_code}; shutting down."
            )
            exit_code = component_code if component_code > 0 else 1
    finally:
        await _stop_components(processes, shutdown_timeout)
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        if shutdown_waiter is not None and not shutdown_waiter.done():
            shutdown_waiter.cancel()
        cleanup_waiters = [*waiters]
        if shutdown_waiter is not None:
            cleanup_waiters.append(shutdown_waiter)
        await asyncio.gather(*cleanup_waiters, return_exceptions=True)
        for received in installed_signals:
            loop.remove_signal_handler(received)
        _status("Plurapack stopped.")
    return exit_code


def main() -> None:
    raise SystemExit(asyncio.run(supervise()))


if __name__ == "__main__":
    main()
