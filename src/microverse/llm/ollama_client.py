"""Thin wrapper around the official Ollama Python client.

Single-model contract: every call goes to ``microverse.config.MODEL``.

Thinking discipline (belt + braces):
  - Pass ``think=False`` (top-level Ollama API field per docs.ollama.com)
    so the runtime is asked not to produce thinking tokens.
  - When ``think=False``, force the returned ``thinking`` field to ``""``
    so the wrapper itself cannot leak the trace via the dict it returns.
  - Run ``strip_thinking`` on content unconditionally as defense in depth.
  - Bump ``thinking_leak`` whenever any leak signal is detected
    (markers in content, or non-empty ``thinking`` despite ``think=False``).
    The counter increments under a lock so multi-threaded callers stay
    coherent (a future watchdog may run alongside agent ticks).

The wrapper returns a plain dict so callers can serialize it without
caring about the ollama package's response object types.
"""

from __future__ import annotations

import functools
import threading
from typing import Any

import ollama

from microverse.config import LLM_TIMEOUT_S, MODEL
from microverse.llm.thinking import has_thinking_markers, strip_thinking

# Module-level counter: bumped whenever a leak signal is detected on a
# single call. Persisted to data/metrics.sqlite by ops.metrics in Phase 1.
thinking_leak: int = 0
_thinking_leak_lock = threading.Lock()


def _bump_leak() -> None:
    global thinking_leak
    with _thinking_leak_lock:
        thinking_leak += 1


@functools.lru_cache(maxsize=4)
def _get_client(timeout_s: float) -> ollama.Client:
    """Cache one ``ollama.Client`` per distinct timeout.

    The client wraps ``httpx.Client``, which holds a connection pool —
    rebuilding it on every call is wasteful. ``lru_cache`` is thread-safe
    in CPython, and ``maxsize=4`` is enough for the default plus a few
    custom-timeout call sites without unbounded growth.
    """
    return ollama.Client(timeout=timeout_s)


def chat(
    messages: list[dict[str, str]],
    *,
    think: bool = False,
    format: str | None = None,
    options: dict[str, Any] | None = None,
    timeout_s: float = LLM_TIMEOUT_S,
) -> dict[str, Any]:
    """Call Ollama chat for ``MODEL`` with thinking discipline.

    Returns ``{"content": str, "thinking": str, "raw": dict}``.
    """
    client = _get_client(timeout_s)

    response = client.chat(
        model=MODEL,
        messages=messages,
        think=think,
        format=format,
        options=options,
    )

    raw_content: str = response.message.content or ""
    raw_thinking: str = getattr(response.message, "thinking", None) or ""

    leak = False
    if has_thinking_markers(raw_content):
        leak = True
    if (not think) and raw_thinking:
        # Caller asked for no thinking but the runtime produced some.
        # That's the strongest leak signal we have — count it and clear
        # the field so callers can never read it.
        leak = True
        raw_thinking = ""

    cleaned = strip_thinking(raw_content)

    if leak:
        _bump_leak()

    return {
        "content": cleaned,
        "thinking": raw_thinking,
        "raw": response.model_dump(),
    }
