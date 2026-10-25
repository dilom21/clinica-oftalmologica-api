"""Inicia la API y el worker CU31 como procesos hermanos de desarrollo.

Uso desde la raiz del repositorio::

    python scripts\\dev.py

El launcher no importa la aplicacion ni lee secretos. Ambos hijos usan el
interprete y el entorno activos; ``app.core.config`` conserva la carga normal
de ``.env`` dentro de cada proceso.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHUTDOWN_TIMEOUT_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 0.2


@dataclass(slots=True)
class ManagedProcess:
    name: str
    command: list[str]
    process: object


def build_commands(python_executable: str | None = None) -> list[tuple[str, list[str]]]:
    """Construye ambos comandos con el mismo Python que ejecuta el launcher."""
    python = python_executable or sys.executable
    return [
        (
            "FastAPI",
            [python, "-u", "-m", "uvicorn", "app.main:app", "--reload"],
        ),
        (
            "Worker Resend",
            [
                python,
                "-u",
                "-m",
                "app.modules.gestion_pagos.notificacion_runner",
            ],
        ),
    ]


def _process_options(platform_name: str) -> dict:
    if platform_name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def start_processes(
    *,
    popen: Callable[..., object] = subprocess.Popen,
    platform_name: str = os.name,
    python_executable: str | None = None,
) -> list[ManagedProcess]:
    """Inicia una API y un worker; revierte el inicio si el segundo falla."""
    started: list[ManagedProcess] = []
    try:
        for name, command in build_commands(python_executable):
            process = popen(
                command,
                cwd=PROJECT_ROOT,
                **_process_options(platform_name),
            )
            item = ManagedProcess(name=name, command=command, process=process)
            started.append(item)
            print(f"[dev] {name} iniciado (PID {process.pid}).", flush=True)
        return started
    except Exception:
        if started:
            shutdown_processes(started, platform_name=platform_name)
        raise


def _send_graceful(process, platform_name: str) -> None:
    if process.poll() is not None:
        return
    if platform_name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
        return
    os.killpg(os.getpgid(process.pid), signal.SIGTERM)


def _kill_tree(
    process,
    platform_name: str,
    run: Callable[..., object],
) -> None:
    if process.poll() is not None:
        return
    if platform_name == "nt":
        run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            return


def shutdown_processes(
    processes: Sequence[ManagedProcess],
    *,
    platform_name: str = os.name,
    timeout: float = SHUTDOWN_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    run: Callable[..., object] = subprocess.run,
) -> None:
    """Detiene primero de forma amable y luego elimina solo los arboles hijos."""
    for item in processes:
        try:
            _send_graceful(item.process, platform_name)
        except (OSError, ProcessLookupError):
            pass

    deadline = monotonic() + timeout
    while any(item.process.poll() is None for item in processes):
        if monotonic() >= deadline:
            break
        sleep(0.1)

    for item in processes:
        if item.process.poll() is None:
            print(
                f"[dev] {item.name} no termino a tiempo; cerrando su arbol.",
                file=sys.stderr,
                flush=True,
            )
            _kill_tree(item.process, platform_name, run)

    for item in processes:
        try:
            item.process.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            pass


def monitor_processes(
    processes: Sequence[ManagedProcess],
    *,
    platform_name: str = os.name,
    sleep: Callable[[float], None] = time.sleep,
    shutdown: Callable[..., None] = shutdown_processes,
) -> int:
    """Mantiene ambos hijos vivos y aplica politica fail-fast."""
    try:
        while True:
            for item in processes:
                returncode = item.process.poll()
                if returncode is None:
                    continue
                print(
                    f"[dev] ERROR: {item.name} termino inesperadamente "
                    f"(codigo {returncode}).",
                    file=sys.stderr,
                    flush=True,
                )
                shutdown(processes, platform_name=platform_name)
                return returncode if returncode != 0 else 1
            sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\n[dev] Ctrl+C recibido; deteniendo API y worker...", flush=True)
        shutdown(processes, platform_name=platform_name)
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inicia FastAPI con hot reload y un worker CU31.",
    )
    parser.parse_args(argv)

    if not (PROJECT_ROOT / "app" / "main.py").is_file():
        print("[dev] ERROR: no se encontro app/main.py.", file=sys.stderr)
        return 1

    print(f"[dev] Python activo: {sys.executable}", flush=True)
    print("[dev] FastAPI: http://127.0.0.1:8000", flush=True)
    print("[dev] Los registros de API y worker apareceran a continuacion.", flush=True)
    try:
        processes = start_processes()
    except Exception as exc:
        print(f"[dev] ERROR al iniciar procesos: {exc}", file=sys.stderr, flush=True)
        return 1
    return monitor_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
