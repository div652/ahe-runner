"""Public model-request seams for benchmark adapters."""

from .meta.model_client import (
    context_overflow_message,
    is_context_overflow_exception,
    model_request_slot,
)

__all__ = [
    "context_overflow_message",
    "is_context_overflow_exception",
    "model_request_slot",
]
