"""Run one function in a fresh spawned process (AHE-025).

Trials that run as threads of one interpreter share its global interpreter
lock, so CPU-bound host-side harness tools of one trial stall all the others.
LAB runs every task in its own process; this helper gives a trial the same
isolation while its caller (and grading) stays in the parent.
"""

from __future__ import annotations

import ctypes
import importlib
import multiprocessing
import os
import pickle
import signal
import sys
import traceback
from collections.abc import Sequence
from contextlib import suppress
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from .errors import InfrastructureError

_CONTEXT = multiprocessing.get_context("spawn")
_PR_SET_PDEATHSIG = 1
_JOIN_TIMEOUT_SECONDS = 60
_MAX_TRACEBACK_CHARS = 20_000


def run_in_child(
    target: str,
    payload: Any,
    *,
    import_paths: Sequence[Path] = (),
    inherited: tuple[Any, ...] = (),
    name: str | None = None,
) -> Any:
    """Return ``target(payload, *inherited)`` computed in a fresh process.

    ``target`` is ``"module:function"``. The child puts ``import_paths`` first
    on ``sys.path`` before it receives ``payload`` over a pipe, so the payload
    may hold types from those paths. ``inherited`` holds objects that can only
    be passed while the process is spawned, such as multiprocessing
    semaphores. An exception raised by the target is raised here, as itself
    when it survives pickling and otherwise as a ``RuntimeError`` naming its
    type; either way it carries the child's traceback as a note. A child that
    exits without answering raises ``InfrastructureError``.
    """
    parent_conn, child_conn = _CONTEXT.Pipe()
    process = _CONTEXT.Process(
        target=_child_main,
        args=(
            child_conn,
            target,
            tuple(str(path) for path in import_paths),
            inherited,
        ),
        name=name,
        daemon=True,
    )
    process.start()
    child_conn.close()
    try:
        try:
            parent_conn.send(payload)
            status, value = parent_conn.recv()
        except (EOFError, OSError) as exc:
            process.join(_JOIN_TIMEOUT_SECONDS)
            raise InfrastructureError(
                f"child process {process.name} exited with code "
                f"{process.exitcode} before returning a result"
            ) from exc
    finally:
        parent_conn.close()
        process.join(_JOIN_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            process.join()
    if status == "ok":
        return value
    raise value


def portable_exception(exc: BaseException) -> BaseException:
    """Return a picklable stand-in for ``exc`` that keeps its traceback text."""
    detail = "".join(traceback.format_exception(exc))
    if len(detail) > _MAX_TRACEBACK_CHARS:
        detail = "...\n" + detail[-_MAX_TRACEBACK_CHARS:]
    try:
        copy = pickle.loads(pickle.dumps(exc))
        if type(copy) is not type(exc):
            raise TypeError("exception changed type in pickling")
    except Exception:
        copy = RuntimeError(f"{type(exc).__name__}: {exc}")
    copy.add_note("Traceback in the child process:\n" + detail)
    return copy


def _child_main(
    conn: Connection,
    target: str,
    import_paths: tuple[str, ...],
    inherited: tuple[Any, ...],
) -> None:
    # The parent decides how to stop; a terminal Ctrl-C reaches every process.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _exit_with_parent()
    sys.path[:0] = import_paths
    try:
        try:
            payload = conn.recv()
            module_name, _, function_name = target.partition(":")
            function = getattr(importlib.import_module(module_name), function_name)
            message: tuple[str, Any] = ("ok", function(payload, *inherited))
        except BaseException as exc:
            message = ("error", portable_exception(exc))
        try:
            conn.send(message)
        except Exception as exc:
            conn.send(
                (
                    "error",
                    RuntimeError(
                        f"child result could not be sent: {type(exc).__name__}: {exc}"
                    ),
                )
            )
    finally:
        conn.close()
        for stream in (sys.stdout, sys.stderr):
            with suppress(Exception):
                stream.flush()
        # Skip interpreter shutdown, which can wait on non-daemon threads
        # left by libraries; every artifact is already written.
        os._exit(0)


def _exit_with_parent() -> None:
    """Ask Linux to stop this process if the thread that started it dies."""
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, int(signal.SIGTERM))
    except (OSError, AttributeError):
        return
    if os.getppid() == 1:
        os._exit(1)
