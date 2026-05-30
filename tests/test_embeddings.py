"""Embeddings unit smoke (offline) + integration smoke (live Ollama).

Offline tests mock ``ollama_client.embed`` (the retry-wrapped boundary
``embeddings.py`` now calls) so the suite stays fast and runs on a CI
without the embedding model pulled. The integration test is marked and
only runs against a live Ollama with ``nomic-embed-text``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from microverse.llm.embeddings import clear_cache, cosine, embed


def setup_function() -> None:
    clear_cache()


def test_embed_returns_list_of_floats() -> None:
    fake_resp: dict[str, Any] = {"embeddings": [[0.1, 0.2, 0.3]]}
    with patch("microverse.llm.ollama_client.embed", return_value=fake_resp):
        vec = embed("hello")
    assert vec == [0.1, 0.2, 0.3]


def test_embed_returns_empty_on_failure() -> None:
    """A missing model / network error returns [], NOT raise — gate 7
    degrades to unknown rather than crashing the spike script."""
    with patch("microverse.llm.ollama_client.embed", side_effect=RuntimeError("model not found")):
        vec = embed("hello")
    assert vec == []


def test_embed_empty_input_returns_empty() -> None:
    assert embed("") == []
    assert embed("   ") == []


def test_cosine_self_equals_one() -> None:
    v = [0.1, 0.2, 0.3, 0.4]
    assert cosine(v, v) == pytest.approx(1.0, abs=1e-6)


def test_cosine_orthogonal_is_zero() -> None:
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_mismatched_length_returns_zero() -> None:
    assert cosine([1.0, 0.0], [1.0]) == 0.0


def test_cosine_empty_returns_zero() -> None:
    assert cosine([], []) == 0.0
    assert cosine([1.0], []) == 0.0


def test_embed_cache_avoids_repeat_calls() -> None:
    """Identical inputs hit the lru_cache; ollama_client.embed should be
    called exactly once per unique text within the cache window."""
    clear_cache()
    fake_resp: dict[str, Any] = {"embeddings": [[0.5]]}
    with patch("microverse.llm.ollama_client.embed", return_value=fake_resp) as mock_embed:
        embed("same")
        embed("same")
        embed("same")
    assert mock_embed.call_count == 1


@pytest.mark.integration
def test_live_nomic_embed_text_roundtrip() -> None:
    """Live Ollama smoke: requires ``ollama pull nomic-embed-text``.

    Skipped when the model is unavailable so the live suite stays
    runnable on machines without the embed model.
    """
    vec_a = embed("a small calm afternoon by the loom")
    if not vec_a:
        pytest.skip("nomic-embed-text not pulled or ollama unavailable")
    vec_b = embed("a small calm afternoon by the loom")
    assert vec_a == vec_b  # determinism
    assert cosine(vec_a, vec_b) == pytest.approx(1.0, abs=1e-6)
    vec_c = embed("a thunderous storm across the open plain at midnight")
    sim = cosine(vec_a, vec_c)
    # Different sentences should be related but not identical — exact
    # value depends on the model. Loose bound: not collapsed to 0 or 1.
    assert 0.0 < abs(sim) < 0.999
