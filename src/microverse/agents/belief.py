"""BeliefSummarizer: periodic, out-of-world summarization of an agent's
recent activity into one short first-person belief line.

ADR 0007 Phase 1 (Stage C), Pillar 1. Like ``Elder.compress_lore`` and
``Trader.rank``, this calls the model OUTSIDE ``agent.think()`` — the
single-model invariant for the action loop is untouched. The same
thinking discipline applies: ``think=False``, defensive ``strip_thinking``
on the content, and a failure metric so operators can tell an infra
failure (chat raised/empty) from a kept-prior outcome.

The output is a model *summary*, never raw fragment prose, and it is the
sanctioned Path-3 self-record carve-out: the agent is allowed to know
what it currently believes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from microverse.agents.base import has_meta_leak
from microverse.config import BELIEF_MAX_CHARS, BELIEF_MAX_TOKENS, LLM_TIMEOUT_S, SAMPLING_FACTUAL
from microverse.llm.ollama_client import chat
from microverse.llm.thinking import strip_thinking
from microverse.prompts import render

if TYPE_CHECKING:
    from microverse.memory.episodic import Event
    from microverse.ops.metrics import Metrics

_logger = logging.getLogger(__name__)
_TEMPLATE = "belief.j2"


class BeliefSummarizer:
    """Summarize recent activity into a bounded first-person belief line."""

    sampling = SAMPLING_FACTUAL

    def summarize(
        self,
        *,
        agent_name: str,
        role: str,
        events: list[Event],
        prior: str,
        metrics: Metrics,
    ) -> str | None:
        """Return a new belief line, or ``None`` if the call failed or
        produced nothing (the caller then keeps the prior belief).

        Bumps ``belief_chat_failure`` on a raised/empty call so the two
        outcomes are distinguishable in the metrics.
        """
        prompt = render(_TEMPLATE, name=agent_name, role=role, events=events, prior=prior)
        try:
            result = chat(
                messages=[{"role": "user", "content": prompt}],
                think=False,
                options={**self.sampling, "num_predict": BELIEF_MAX_TOKENS},
                timeout_s=LLM_TIMEOUT_S,
            )
        except Exception:
            _logger.exception("BeliefSummarizer chat() failed for %s", agent_name)
            metrics.bump("belief_chat_failure")
            return None

        content = (result.get("content") if isinstance(result, dict) else "") or ""
        # Defense in depth: strip any thinking that leaked despite think=False.
        cleaned = strip_thinking(content).strip()
        if not cleaned:
            metrics.bump("belief_chat_failure")
            return None
        if len(cleaned) > BELIEF_MAX_CHARS:
            cleaned = cleaned[:BELIEF_MAX_CHARS].rstrip()
        # The belief is persisted and re-injected into every later persona
        # prompt until the next refresh, so an immersion-breaking
        # hallucination ("I am an AI model") would become durable prompt
        # state. Apply the same meta-leak guard that gates normal agent
        # output; reject (keep the prior belief) on any leak signal.
        if has_meta_leak(cleaned):
            metrics.bump("belief_meta_leak_block", agent=agent_name)
            return None
        return cleaned
