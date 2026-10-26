"""Public model-request seams for benchmark adapters.

Adapter-owned model clients, such as judges, should use
``RecordedExactModelClient`` so that their calls follow the campaign's
endpoint policy: request slots, token admission, retries, and provider-block
handling. A client that must stay outside it can use ``build_openai_client``
for credentials and ``model_request_slot`` for the shared call queue.
"""

from .meta.endpoint import (
    build_openai_client,
    is_provider_blocked_failure,
    is_transient_exception,
    provider_rejection_message,
    route_fingerprint,
    transient_retry_delay,
    wire_api,
)
from .meta.model_client import (
    RecordedExactModelClient,
    context_overflow_message,
    is_context_overflow_exception,
    model_request_slot,
)

__all__ = [
    "RecordedExactModelClient",
    "build_openai_client",
    "context_overflow_message",
    "is_context_overflow_exception",
    "is_provider_blocked_failure",
    "is_transient_exception",
    "model_request_slot",
    "provider_rejection_message",
    "route_fingerprint",
    "transient_retry_delay",
    "wire_api",
]
