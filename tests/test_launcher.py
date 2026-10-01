import asyncio
import importlib
import sys

import pytest


class FakeProcess:
    next_pid = 100

    def __init__(self):
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode = None
        self.waiting = asyncio.Event()
        self.terminated = False
        self.killed = False

    async def wait(self):
        await self.waiting.wait()
        return self.returncode

    def exit(self, code):
        self.returncode = code
        self.waiting.set()

    def terminate(self):
        self.terminated = True
        self.exit(0)

    def kill(self):
        self.killed = True
        self.exit(-9)


def test_importing_launcher_does_not_start_services(monkeypatch):
    called = False

    def unexpected_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(asyncio, "run", unexpected_run)
    sys.modules.pop("plurapack.__main__", None)
    importlib.import_module("plurapack.__main__")

    assert called is False


@pytest.mark.asyncio
async def test_starts_both_components_and_coordinates_failure(monkeypatch, capsys):
    launcher = importlib.import_module("plurapack.__main__")
    processes = [FakeProcess(), FakeProcess()]
    commands = []

    async def create_process(*command):
        commands.append(command)
        return processes[len(commands) - 1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    task = asyncio.create_task(launcher.supervise((
        ("bot", ("python", "bot")),
        ("dashboard", ("python", "web")),
    )))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    processes[0].exit(7)

    assert await task == 7
    assert commands == [("python", "bot"), ("python", "web")]
    assert processes[1].terminated is True
    output = capsys.readouterr().out
    assert "Started bot" in output
    assert "Started dashboard" in output
    assert "Critical component bot exited unexpectedly with status 7" in output


@pytest.mark.asyncio
async def test_cancellation_stops_every_component(monkeypatch):
    launcher = importlib.import_module("plurapack.__main__")
    processes = [FakeProcess(), FakeProcess()]

    async def create_process(*command):
        return processes.pop(0)

    original_processes = processes.copy()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    task = asyncio.create_task(launcher.supervise((
        ("bot", ("bot",)), ("dashboard", ("web",)),
    )))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(process.terminated for process in original_processes)


def test_components_reuse_existing_entry_points():
    launcher = importlib.import_module("plurapack.__main__")

    assert launcher.COMPONENTS == (
        ("bot", (sys.executable, "-m", "plurapack.bot")),
        ("dashboard", (sys.executable, "-m", "plurapack.web")),
    )
