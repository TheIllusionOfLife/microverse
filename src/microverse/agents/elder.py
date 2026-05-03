"""Elder: weekly mythic-lore regeneration with a Jaccard drift guard.

The Elder reads the prior lore + a slice of recent events and asks the
LLM to rewrite a single canonical lore document. Without a guard, a
small model will sometimes substitute a totally new world (the
"galaxy far far away" failure mode). The drift guard checks lexical
Jaccard similarity between old and new tokens; if too low, retry once
with a continuity hint, then keep the prior on continued drift.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from microverse._text import jaccard, tokenize
from microverse.agents.base import Action, ActionKind, Agent, WorldContext
from microverse.config import LLM_MAX_TOKENS, LLM_TIMEOUT_S, SAMPLING_FACTUAL
from microverse.llm.ollama_client import chat
from microverse.prompts import render

if TYPE_CHECKING:
    from microverse.memory.episodic import Event
    from microverse.ops.metrics import Metrics

_logger = logging.getLogger(__name__)
_PERSONA_TEMPLATE = "compression.j2"
MIN_JACCARD = 0.35


def lore_jaccard(a: str, b: str) -> float:
    """Token-set Jaccard over signal tokens (case-folded, stop-words and
    sub-3-char tokens dropped).
    """
    return jaccard(
        tokenize(a, min_len=3, drop_stopwords=True),
        tokenize(b, min_len=3, drop_stopwords=True),
    )


class Elder(Agent):
    role = "elder"
    persona_template = _PERSONA_TEMPLATE
    sampling = SAMPLING_FACTUAL

    def __init__(self, name: str, *, soul_tokens: int = 50) -> None:
        super().__init__(name, soul_tokens=soul_tokens)

    def think(self, world: WorldContext) -> Action:
        # Elder doesn't tick like Artisan; compress_lore is the real work.
        return Action(action=ActionKind.REST)

    def _call(self, prompt: str, *, metrics: Metrics) -> str | None:
        """Single LLM call. Bumps lore_chat_failure on raise/empty so
        operators can distinguish infrastructure failures from
        semantic drift in the metrics."""
        try:
            result = chat(
                messages=[{"role": "user", "content": prompt}],
                think=False,
                options={**self.sampling, "num_predict": LLM_MAX_TOKENS},
                timeout_s=LLM_TIMEOUT_S,
            )
        except Exception:
            _logger.exception("Elder chat() failed")
            metrics.bump("lore_chat_failure")
            return None
        content = (result.get("content") if isinstance(result, dict) else "") or ""
        cleaned = content.strip()
        if not cleaned:
            metrics.bump("lore_chat_failure")
            return None
        return cleaned

    def compress_lore(
        self,
        prior_lore: str,
        events: list[Event],
        *,
        metrics: Metrics,
    ) -> str:
        """Rewrite the canonical lore. Returns the prior on guard fail.

        Granular metrics (the watchdog reads these to distinguish
        legitimate equilibrium from a stuck Elder):
          - ``lore_compress_accepted``     — round-1 success
          - ``lore_compress_retry_accepted`` — round-2 success
          - ``lore_drift_block``           — kept prior after drift
          - ``lore_chat_failure``          — chat() raised or returned empty
        """
        prompt = render(self.persona_template, prior_lore=prior_lore, events=events)

        # Empty prior = fresh world; there's nothing to drift FROM, so
        # accept whatever the model produces (or fall through if the
        # call failed entirely).
        empty_prior = not prior_lore.strip()

        # Round 1: normal prompt.
        candidate = self._call(prompt, metrics=metrics)
        if candidate and (empty_prior or lore_jaccard(prior_lore, candidate) >= MIN_JACCARD):
            metrics.bump("lore_compress_accepted")
            return candidate

        # Round 2: continuity-hint variant. The hint goes on the prompt
        # before the model sees the output instruction; render with a
        # context flag the persona template handles.
        retry_prompt = render(
            self.persona_template,
            prior_lore=prior_lore,
            events=events,
            continuity_hint=True,
        )
        retry = self._call(retry_prompt, metrics=metrics)
        if retry and (empty_prior or lore_jaccard(prior_lore, retry) >= MIN_JACCARD):
            metrics.bump("lore_compress_retry_accepted")
            return retry

        # Both attempts failed. Distinguish the two failure modes so
        # the watchdog can react differently to "model is hung" vs
        # "model is hallucinating away from canon".
        if candidate is None and retry is None:
            metrics.bump("lore_double_chat_failure")
        else:
            metrics.bump("lore_drift_block")
        return prior_lore
