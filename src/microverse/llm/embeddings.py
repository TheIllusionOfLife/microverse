"""Local Ollama embeddings — measurement-only.

NOT called from ``agent.think()``. Used exclusively by the gates
producer in ``scripts/spike_workshop_measure.py`` to compute
turn-to-turn cosine similarity for ADR 0005 Gate 8 (scene semantic
dependence). The single-model invariant for the agent action loop
remains intact: agents see exactly one model, ``config.MODEL``.

The embedding model (``config.EMBEDDING_MODEL``, default
``nomic-embed-text``) must be pulled separately:

    ollama pull nomic-embed-text

If the model is missing, :func:`embed` returns an empty list and
records ``embedding_unavailable`` to stderr — gate 8 then degrades to
unknown rather than asserting on a nonexistent oracle.

A small ``lru_cache`` deduplicates re-embedding identical fragments
across multiple gate runs (e.g. when the spike script is re-invoked).
The cache function takes ``(text_hash, text)`` and Python's
``lru_cache`` keys on both, but the SHA-256 hash dominates the key's
identity in practice — equal inputs produce equal hashes, so a repeat
call hits the cache exactly when the text is unchanged.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import math
import sys
from typing import Any

import ollama

from microverse.config import EMBEDDING_MODEL

_logger = logging.getLogger(__name__)

# Soft cap on cache size. Each entry is one fragment's embedding
# (~768 floats ≈ 6 KB). 2048 entries ≈ 12 MB RSS, plenty for a 7-day
# soak's WIPs and turns.
_CACHE_MAX = 2048


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=_CACHE_MAX)
def _embed_by_hash(_text_hash: str, text: str) -> tuple[float, ...]:
    """Cached inner. ``lru_cache`` keys on the (hash, text) tuple — the
    hash is collision-resistant and dominates identity; ``text`` rides
    alongside as the value to actually pass to Ollama on a cache miss.
    """
    try:
        resp: Any = ollama.embed(model=EMBEDDING_MODEL, input=text)
    except Exception as e:
        # Most likely cause: embedding model not pulled. Log once per
        # process via the logger; emit a stderr breadcrumb so the
        # operator sees it even with default logging silenced.
        _logger.warning("ollama.embed failed: %s", e)
        # Operator visibility breadcrumb on stderr, even with default
        # logging silenced. Uses sys.stderr.write to avoid the ruff T201
        # print() ban (this module is library code, not a CLI).
        sys.stderr.write(f"embedding_unavailable: {e}\n")
        return ()
    # Response shape: {"embeddings": [[float, ...]]} (Ollama >=0.4)
    # or {"embedding": [float, ...]} (older).
    embeddings = getattr(resp, "embeddings", None) or (
        resp.get("embeddings") if isinstance(resp, dict) else None
    )
    if embeddings:
        vec = embeddings[0]
    else:
        vec = getattr(resp, "embedding", None) or (
            resp.get("embedding") if isinstance(resp, dict) else None
        )
    if not vec:
        return ()
    return tuple(float(x) for x in vec)


def embed(text: str) -> list[float]:
    """Return a list-of-floats embedding for ``text``.

    Empty list if the embedding model is unavailable. Cached by
    SHA-256 hash so re-embedding identical inputs is free.
    """
    if not text or not text.strip():
        return []
    return list(_embed_by_hash(_hash(text), text))


def cosine(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 on any zero / mismatched
    vector — the caller should drop those from the gate-7 statistic so a
    degenerate pair does not pull the median.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def clear_cache() -> None:
    """Drop the embedding cache. Test hook + operator-side memory bound."""
    _embed_by_hash.cache_clear()
