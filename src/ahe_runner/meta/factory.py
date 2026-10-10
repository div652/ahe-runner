"""Meta backend selection."""

from __future__ import annotations

from .base import MetaBackend, MetaBackendContext


def create_meta_backend(context: MetaBackendContext) -> MetaBackend:
    backend = context.config.document.meta.backend
    if backend == "scripted":
        from .scripted import ScriptedMetaBackend

        return ScriptedMetaBackend(context)
    if backend == "nexau":
        from .nexau_backend import NexAUMetaBackend

        return NexAUMetaBackend(context)
    raise ValueError(f"unsupported meta backend: {backend}")
