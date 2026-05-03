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
import re
from typing import TYPE_CHECKING

from microverse.agents.base import Action, ActionKind, Agent, WorldContext
from microverse.config import LLM_MAX_TOKENS, LLM_TIMEOUT_S, SAMPLING_FACTUAL
from microverse.llm.ollama_client import chat
from microverse.prompts import render

if TYPE_CHECKING:
    from microverse.memory.episodic import Event
    from microverse.ops.metrics import Metrics

_logger = logging.getLogger(__name__)
_PERSONA_TEMPLATE = "compression.j2"
_CONTINUITY_HINT = (
    "\n\nIMPORTANT: preserve the village's existing names, places, "
    "events, and themes. Do NOT introduce new settings or eras."
)
MIN_JACCARD = 0.5

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def lore_jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity, case-folded, punctuation-stripped.

    Vacuous case (both empty) returns 1.0 — there is no drift signal,
    so we don't trigger the guard on a fresh world.
    """
    tokens_a = {t.lower() for t in _TOKEN_RE.findall(a)}
    tokens_b = {t.lower() for t in _TOKEN_RE.findall(b)}
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


class Elder(Agent):
    role = "elder"
    persona_template = _PERSONA_TEMPLATE
    sampling = SAMPLING_FACTUAL

    def __init__(self, name: str, *, soul_tokens: int = 50) -> None:
        super().__init__(name, soul_tokens=soul_tokens)

    def think(self, world: WorldContext) -> Action:
        # Elder doesn't tick like Artisan; compress_lore is the real work.
        return Action(action=ActionKind.REST)

    def _call(self, prompt: str) -> str | None:
        try:
            result = chat(
                messages=[{"role": "user", "content": prompt}],
                think=False,
                options={**self.sampling, "num_predict": LLM_MAX_TOKENS},
                timeout_s=LLM_TIMEOUT_S,
            )
        except Exception:
            _logger.exception("Elder chat() failed")
            return None
        content = (result.get("content") if isinstance(result, dict) else "") or ""
        return content.strip() or None

    def compress_lore(
        self,
        prior_lore: str,
        events: list[Event],
        *,
        metrics: Metrics,
    ) -> str:
        """Rewrite the canonical lore. Returns the prior on guard fail."""
        prompt = render(self.persona_template, prior_lore=prior_lore, events=events)

        # Empty prior = fresh world; there's nothing to drift FROM, so
        # accept whatever the model produces (or fall through if the
        # call failed entirely).
        empty_prior = not prior_lore.strip()

        # Round 1: normal prompt.
        candidate = self._call(prompt)
        if candidate and (empty_prior or lore_jaccard(prior_lore, candidate) >= MIN_JACCARD):
            return candidate

        # Round 2: continuity hint appended.
        retry = self._call(prompt + _CONTINUITY_HINT)
        if retry and (empty_prior or lore_jaccard(prior_lore, retry) >= MIN_JACCARD):
            return retry

        # Drift survived two attempts (or chat failed twice). Keep the
        # prior so the world's mythic continuity is never overwritten
        # by a hallucination, and bump the metric for visibility.
        metrics.bump("lore_drift_block")
        return prior_lore
