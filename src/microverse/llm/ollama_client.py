"""Thin wrapper around the official Ollama Python client.

Single-model contract: every call goes to ``gemma4:e4b``.

Thinking discipline:
  - Pass ``think=False`` (top-level Ollama API field per docs.ollama.com).
  - As defense-in-depth, run ``strip_thinking`` on the content. If anything
    was stripped, bump the module-level ``thinking_leak`` counter so the
    runtime can spot model/runtime regressions.

The wrapper returns a plain dict so callers can serialize it without
caring about the ollama package's response object types.
"""

from __future__ import annotations

from typing import Any

import ollama

from microverse.llm.thinking import strip_thinking

MODEL = "gemma4:e4b"

# Module-level counter: bumped each time strip_thinking actually trims
# content. Persisted to data/metrics.sqlite by ops.metrics in Phase 1.
thinking_leak: int = 0


def chat(
    messages: list[dict[str, str]],
    *,
    think: bool = False,
    format: str | None = None,
    options: dict[str, Any] | None = None,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    """Call Ollama chat for ``gemma4:e4b`` with thinking discipline.

    Returns ``{"content": str, "thinking": str, "raw": dict}``.
    """
    global thinking_leak
    client = ollama.Client(timeout=timeout_s)

    response = client.chat(
        model=MODEL,
        messages=messages,
        think=think,
        format=format,
        options=options,
    )

    raw_content: str = response.message.content or ""
    raw_thinking: str = getattr(response.message, "thinking", None) or ""

    cleaned = strip_thinking(raw_content)
    if cleaned != raw_content.strip():
        thinking_leak += 1

    return {
        "content": cleaned,
        "thinking": raw_thinking,
        "raw": response.model_dump(),
    }
