"""Spawn isolated evaluated-trial work without weakening runner policy."""

from __future__ import annotations

import ctypes
import importlib
import multiprocessing
import os
import pickle
import signal
import sys
import traceback
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from .errors import (
    CandidateValidationError,
    ConfigurationError,
    InfrastructureError,
    IntegrityError,
)
from .meta.model_client import (
    _adopt_shared_request_slots,
    _shared_request_slots,
)
from .runtime import EvaluatedAgentRuntime, RuntimeProcessSpec

_CONTEXT = multiprocessing.get_context("spawn")
_PR_SET_PDEATHSIG = 1
_JOIN_TIMEOUT_SECONDS = 10
_MAX_TRACEBACK_CHARS = 20_000


@dataclass(frozen=True)
class _EvaluatedChildRequest:
    target: str
    payload: Any
    runtime_spec: RuntimeProcessSpec


def run_evaluated_trial_in_child(
    target: str,
    payload: Any,
    *,
    runtime: EvaluatedAgentRuntime,
    import_paths: Sequence[Path] = (),
    inherited: tuple[Any, ...] = (),
    name: str | None = None,
    timeout_seconds: float | None = None,
) -> Any:
    """Run one adapter target with a rebuilt runtime in a fresh process.

    ``target`` must be an importable ``"module:function"`` accepting
    ``(payload, child_runtime, *inherited)``. The parent runs the evaluated-model
    preflight once, and every child shares the parent's endpoint request limit.
    Payloads, inherited objects, return values, and raised exceptions must cross
    a multiprocessing boundary.
    """

    spec = runtime.process_spec()
    request = _EvaluatedChildRequest(
        target=target,
        payload=payload,
        runtime_spec=spec,
    )
    paths = (spec.project_root, *import_paths)
    return run_in_child(
        f"{__name__}:_run_evaluated_target",
        request,
        import_paths=paths,
        inherited=(_shared_request_slots(spec.transport), *inherited),
        name=name,
        timeout_seconds=timeout_seconds,
    )


def run_in_child(
    target: str,
    payload: Any,
    *,
    import_paths: Sequence[Path] = (),
    inherited: tuple[Any, ...] = (),
    name: str | None = None,
    timeout_seconds: float | None = None,
) -> Any:
    """Return ``target(payload, *inherited)`` from a fresh spawned process."""

    _validate_target(target)
    paths = _validated_import_paths(import_paths)
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ConfigurationError("child timeout_seconds must be greater than zero")

    parent_conn, child_conn = _CONTEXT.Pipe()
    process = _CONTEXT.Process(
        target=_child_main,
        args=(
            child_conn,
            target,
            tuple(str(path) for path in paths),
            inherited,
        ),
        name=name,
    )
    process_name = process.name
    try:
        process.start()
    except BaseException:
        parent_conn.close()
        child_conn.close()
        raise
    child_conn.close()

    received = False
    try:
        try:
            parent_conn.send(payload)
        except (BrokenPipeError, EOFError, OSError) as exc:
            process.join(_JOIN_TIMEOUT_SECONDS)
            raise InfrastructureError(
                f"child process {process_name} exited with code "
                f"{process.exitcode} before receiving its payload; if an operator "
                "script drives the runner, guard it with "
                "`if __name__ == '__main__':`"
            ) from exc
        except Exception as exc:
            raise ConfigurationError(
                f"child payload for {target!r} is not serializable: {exc}"
            ) from exc
        if timeout_seconds is not None and not parent_conn.poll(timeout_seconds):
            raise InfrastructureError(
                f"child process {process_name} exceeded {timeout_seconds:g} seconds"
            )
        try:
            status, value = parent_conn.recv()
        except (EOFError, OSError) as exc:
            process.join(_JOIN_TIMEOUT_SECONDS)
            raise InfrastructureError(
                f"child process {process_name} exited with code "
                f"{process.exitcode} before returning a result"
            ) from exc
        received = True
    finally:
        parent_conn.close()
        _finish_process(process, terminate=not received)

    if status == "ok":
        return value
    if status == "error" and isinstance(value, BaseException):
        raise value
    raise InfrastructureError(
        f"child process {process_name} returned an invalid response"
    )


def portable_exception(exc: BaseException) -> BaseException:
    """Return a picklable exception carrying the child's traceback text."""

    detail = "".join(traceback.format_exception(exc))
    if len(detail) > _MAX_TRACEBACK_CHARS:
        detail = "...\n" + detail[-_MAX_TRACEBACK_CHARS:]
    copy = _copy_exception(exc)
    copy.add_note("Traceback in the child process:\n" + detail)
    return copy


def _copy_exception(exc: BaseException) -> BaseException:
    if isinstance(exc, CandidateValidationError):
        return CandidateValidationError(exc.report)
    try:
        copy = pickle.loads(pickle.dumps(exc))
        if type(copy) is not type(exc):
            raise TypeError("exception changed type in pickling")
        return copy
    except Exception:
        if isinstance(exc, (ConfigurationError, InfrastructureError, IntegrityError)):
            return type(exc)(str(exc))
        return RuntimeError(f"{type(exc).__name__}: {exc}")


def _run_evaluated_target(
    request: _EvaluatedChildRequest,
    request_slots: Any,
    *inherited: Any,
) -> Any:
    _adopt_shared_request_slots(request.runtime_spec.transport, request_slots)
    function = _load_target(request.target)
    runtime = EvaluatedAgentRuntime.from_process_spec(request.runtime_spec)
    try:
        return function(request.payload, runtime, *inherited)
    finally:
        runtime.close()


def _child_main(
    conn: Connection,
    target: str,
    import_paths: tuple[str, ...],
    inherited: tuple[Any, ...],
) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _exit_with_parent()
    sys.path[:0] = import_paths
    try:
        try:
            payload = conn.recv()
            value = _load_target(target)(payload, *inherited)
            message: tuple[str, Any] = ("ok", value)
        except BaseException as exc:
            message = ("error", portable_exception(exc))
        try:
            conn.send(message)
        except Exception as exc:
            with suppress(Exception):
                conn.send(
                    (
                        "error",
                        RuntimeError(
                            "child result could not be sent: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    )
                )
    finally:
        conn.close()
        for stream in (sys.stdout, sys.stderr):
            with suppress(Exception):
                stream.flush()
        os._exit(0)


def _load_target(target: str) -> Callable[..., Any]:
    module_name, function_name = _validate_target(target)
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise ConfigurationError(f"child target {target!r} is not callable")
    return function


def _validate_target(target: str) -> tuple[str, str]:
    module_name, separator, function_name = target.partition(":")
    if (
        separator != ":"
        or not function_name.isidentifier()
        or not module_name
        or any(not part.isidentifier() for part in module_name.split("."))
    ):
        raise ConfigurationError("child target must use 'module.path:function_name'")
    return module_name, function_name


def _validated_import_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for value in paths:
        path = Path(value).resolve()
        if not path.is_dir():
            raise ConfigurationError(f"child import path is not a directory: {path}")
        if path not in resolved:
            resolved.append(path)
    return tuple(resolved)


def _finish_process(
    process: multiprocessing.Process,
    *,
    terminate: bool,
) -> None:
    if terminate and process.is_alive():
        process.terminate()
    process.join(_JOIN_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join()
    process.close()


def _exit_with_parent() -> None:
    """Ask Linux to stop this process if its parent process exits."""

    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, int(signal.SIGTERM))
    except (OSError, AttributeError):
        return
    if os.getppid() == 1:
        os._exit(1)
