"""Exact prompt token count for NexAU's pre-send context check (AHE-023).

NexAU's default counter estimates one token per four characters, which is
10-20% high for prose and far too low for dense tables. This counter uses
the o200k_base tokenizer of the GPT-5 family served through Copilot, with
NexAU's own per-message and per-tool overheads. Special-token text such as
``<|endoftext|>`` in a document is counted as ordinary text instead of
raising.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import tiktoken
from nexau.archs.main_sub.utils.token_counter import TokenCounter
from nexau.core.messages import Message

ENCODING_NAME = "o200k_base"
_CHUNK_CHARS = 8192


class _ExactTokenCounter(TokenCounter):
    def __init__(self) -> None:
        self._encoding = tiktoken.get_encoding(ENCODING_NAME)
        super().__init__(strategy="fallback")
        self.strategy = "tiktoken"
        self.set_counter(self._count)

    def _count(
        self,
        messages: Sequence[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        return self._count_tokens_with_text_encoder(messages, tools, self._encode)

    def _encode(self, text: str) -> int:
        if not text:
            return 0
        try:
            return len(self._encoding.encode_ordinary(text))
        except ValueError as exc:
            if not self._is_regex_tokenization_error(exc):
                raise
        return sum(
            len(self._encoding.encode_ordinary(text[start : start + _CHUNK_CHARS]))
            for start in range(0, len(text), _CHUNK_CHARS)
        )


_COUNTER = _ExactTokenCounter()


def count_prompt_tokens(
    messages: Sequence[Message],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """NexAU ``token_counter`` hook: o200k tokens of messages and tools."""
    return _COUNTER.count_tokens(messages, tools=tools)
