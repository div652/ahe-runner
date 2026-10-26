"""In-process callbacks that let a role's tool run work in the runner.

A meta-agent tool receives only its arguments and the agent context. When a
tool must start other work, such as a role that dispatches sub-roles, the
runner registers a callback for the duration of the run and puts the
returned token in the agent context; the tool calls back through the token.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

_CALLBACKS: dict[str, Callable[..., Any]] = {}
_LOCK = threading.Lock()


@contextmanager
def registered(callback: Callable[..., Any]) -> Iterator[str]:
    token = uuid.uuid4().hex
    with _LOCK:
        _CALLBACKS[token] = callback
    try:
        yield token
    finally:
        with _LOCK:
            _CALLBACKS.pop(token, None)


def call(token: str, *args: Any, **kwargs: Any) -> Any:
    with _LOCK:
        callback = _CALLBACKS.get(token)
    if callback is None:
        raise RuntimeError("the runner callback for this tool is no longer active")
    return callback(*args, **kwargs)
