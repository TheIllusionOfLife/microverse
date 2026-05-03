"""Integration test for think=False on real Ollama gemma4:e4b.

Verifies the contract from PROMPT.md: callers of ollama_client.chat must
never see thinking tokens. We test both branches:

  - think=True : confirm message.thinking populates (or skip the branch
                 if Ollama doesn't classify gemma4:e4b as a thinking
                 model — the README documents this case).
  - think=False: confirm message.thinking is empty AND no <think> leak
                 in content.

Run with: ``uv run pytest tests/test_ollama_think_off.py -q -m integration``
Requires: ``ollama serve`` running locally with ``gemma4:e4b`` pulled.
"""

import pytest

from microverse.llm.ollama_client import chat

pytestmark = pytest.mark.integration


PROMPT = "Reply with the single word OK and nothing else."


def test_think_false_yields_no_thinking():
    result = chat([{"role": "user", "content": PROMPT}], think=False)

    assert result["thinking"] == "", (
        f"think=False must produce empty thinking; got {result['thinking']!r}"
    )
    assert "<think>" not in result["content"], (
        f"think=False must not leak <think> tag in content; got {result['content']!r}"
    )
    assert "OK" in result["content"].upper()


def test_think_true_branch_or_skip():
    """If gemma4:e4b is recognized as thinking-capable by Ollama, this
    populates message.thinking. If not, the call still succeeds with
    empty thinking — that's documented behavior, not a failure.
    """
    result = chat([{"role": "user", "content": PROMPT}], think=True)

    if not result["thinking"]:
        pytest.skip(
            "gemma4:e4b returned empty thinking even with think=True; "
            "Ollama does not classify this build as a thinking model. "
            "Documented in README; strip_thinking remains the contract."
        )
    assert isinstance(result["thinking"], str)
    assert len(result["thinking"]) > 0
