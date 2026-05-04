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
import time
from typing import Any

import httpx
import ollama

from microverse.config import LLM_TIMEOUT_S, MODEL
from microverse.llm.thinking import has_thinking_markers, strip_thinking

# Module-level counter: bumped whenever a leak signal is detected on a
# single call. Persisted to data/metrics.sqlite by ops.metrics in Phase 1.
thinking_leak: int = 0
_thinking_leak_lock = threading.Lock()

# Module-level counter: bumped each time chat() retries after a
# transient connection error. Operators read this from metrics.sqlite
# to spot a flaky Ollama instance before it triggers consecutive_fail
# pauses on every agent.
llm_retry: int = 0
_llm_retry_lock = threading.Lock()

# Connection-class errors we retry. Pydantic / value errors / 4xx
# are NOT retried: those signal a caller bug, not infra noise.
#
# IMPORTANT: ollama-python wraps httpx.ConnectError as the built-in
# ConnectionError before it reaches us (see ollama/_client.py
# `_request_raw`). httpx.WriteError, httpx.PoolTimeout, and
# httpx.ReadError pass through unwrapped. ollama.ResponseError
# wraps HTTP error responses; we retry only when status_code >= 500
# (handled by _is_retryable_response_error below, not in this tuple).
_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)
_RETRY_BACKOFFS: tuple[float, ...] = (0.5, 1.5)  # 2 retries total


def _is_retryable_response_error(exc: BaseException) -> bool:
    """Retry ollama.ResponseError only for transient 5xx server errors.

    4xx is a caller bug (bad model name, malformed payload); retrying
    just wastes time. 5xx is infra noise (Ollama daemon restarting,
    upstream proxy hiccup) and the same call is likely to succeed.
    """
    return isinstance(exc, ollama.ResponseError) and getattr(exc, "status_code", 0) >= 500


def _bump_leak() -> None:
    """Atomically increment the module-level ``thinking_leak`` counter."""
    global thinking_leak
    with _thinking_leak_lock:
        thinking_leak += 1


def _bump_retry() -> None:
    """Atomically increment the module-level ``llm_retry`` counter."""
    global llm_retry
    with _llm_retry_lock:
        llm_retry += 1


@functools.lru_cache(maxsize=4)
def _get_client(timeout_s: float) -> ollama.Client:
    """Cache one ``ollama.Client`` per distinct timeout.

    The client wraps ``httpx.Client``, which holds a connection pool —
    rebuilding it on every call is wasteful. ``lru_cache`` is thread-safe
    in CPython, and ``maxsize=4`` is enough for the default plus a few
    custom-timeout call sites without unbounded growth.
    """
    return ollama.Client(timeout=timeout_s)


def _chat_with_retry(client: ollama.Client, **kwargs: Any) -> Any:
    """Call client.chat with retries on transient connection errors.

    Pydantic / value / HTTP 4xx errors are not in _RETRY_EXCEPTIONS so
    they propagate unchanged. Idempotency is fine: nothing in the
    world commits until _commit_action() runs after agent.think()
    returns.
    """
    for attempt, backoff in enumerate((0.0, *_RETRY_BACKOFFS)):
        if backoff > 0:
            time.sleep(backoff)
        try:
            return client.chat(**kwargs)
        except _RETRY_EXCEPTIONS:
            if attempt >= len(_RETRY_BACKOFFS):
                raise
            _bump_retry()
        except ollama.ResponseError as e:
            if not _is_retryable_response_error(e) or attempt >= len(_RETRY_BACKOFFS):
                raise
            _bump_retry()
    raise RuntimeError("unreachable")  # pragma: no cover


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

    response = _chat_with_retry(
        client,
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
