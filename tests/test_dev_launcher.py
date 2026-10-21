"""Pruebas aisladas del launcher local; nunca inician API, worker ni red."""

import signal

import pytest

from scripts import dev


class FakeProcess:
    next_pid = 9000

    def __init__(self, returncode=None):
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode = returncode
        self.signals = []
        self.waits = []

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        self.returncode = 0

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.returncode


def test_commands_use_active_python_and_only_one_worker():
    commands = dev.build_commands("C:/conda/envs/clinica-backend/python.exe")

    assert len(commands) == 2
    assert all(command[0] == "C:/conda/envs/clinica-backend/python.exe"
               for _, command in commands)
    api = commands[0][1]
    worker = commands[1][1]
    assert api[-2:] == ["app.main:app", "--reload"]
    assert sum("notificacion_runner" in part
               for _, command in commands for part in command) == 1
    assert "notificacion_runner" not in " ".join(api)
    assert "notificacion_runner" in " ".join(worker)


def test_windows_starts_two_process_groups_from_project_root():
    calls = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    processes = dev.start_processes(
        popen=popen,
        platform_name="nt",
        python_executable="python",
    )

    assert [item.name for item in processes] == ["FastAPI", "Worker Resend"]
    assert len(calls) == 2
    assert all(kwargs["cwd"] == dev.PROJECT_ROOT for _, kwargs in calls)
    assert all(
        kwargs["creationflags"] == dev.subprocess.CREATE_NEW_PROCESS_GROUP
        for _, kwargs in calls
    )


def test_hot_reload_belongs_only_to_api_process():
    commands = dict(dev.build_commands("python"))

    assert "--reload" in commands["FastAPI"]
    assert "--reload" not in commands["Worker Resend"]


def test_unexpected_api_exit_stops_worker_and_propagates_error():
    api = dev.ManagedProcess("FastAPI", [], FakeProcess(returncode=7))
    worker = dev.ManagedProcess("Worker Resend", [], FakeProcess())
    shutdown_calls = []

    result = dev.monitor_processes(
        [api, worker],
        platform_name="nt",
        sleep=lambda _seconds: None,
        shutdown=lambda processes, **kwargs: shutdown_calls.append(
            (list(processes), kwargs)
        ),
    )

    assert result == 7
    assert len(shutdown_calls) == 1
    assert shutdown_calls[0][0] == [api, worker]


def test_ctrl_c_stops_both_processes_cleanly():
    processes = [
        dev.ManagedProcess("FastAPI", [], FakeProcess()),
        dev.ManagedProcess("Worker Resend", [], FakeProcess()),
    ]
    shutdown_calls = []

    def interrupt(_seconds):
        raise KeyboardInterrupt

    result = dev.monitor_processes(
        processes,
        platform_name="nt",
        sleep=interrupt,
        shutdown=lambda items, **kwargs: shutdown_calls.append(
            (list(items), kwargs)
        ),
    )

    assert result == 0
    assert shutdown_calls[0][0] == processes


def test_windows_graceful_shutdown_signals_both_groups():
    processes = [
        dev.ManagedProcess("FastAPI", [], FakeProcess()),
        dev.ManagedProcess("Worker Resend", [], FakeProcess()),
    ]

    dev.shutdown_processes(processes, platform_name="nt", timeout=0)

    assert all(
        item.process.signals == [signal.CTRL_BREAK_EVENT]
        for item in processes
    )
    assert all(item.process.waits == [2] for item in processes)


def test_start_failure_stops_process_already_started(monkeypatch):
    api = FakeProcess()
    calls = 0
    shutdown_calls = []

    def popen(_command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("worker no disponible")
        return api

    monkeypatch.setattr(
        dev,
        "shutdown_processes",
        lambda processes, **kwargs: shutdown_calls.append((list(processes), kwargs)),
    )

    with pytest.raises(OSError, match="worker no disponible"):
        dev.start_processes(popen=popen, platform_name="nt")

    assert len(shutdown_calls) == 1
    assert shutdown_calls[0][0][0].name == "FastAPI"
