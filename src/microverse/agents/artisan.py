"""Artisan agent: makes things (essays, code, designs, descriptions).

Uses creative sampling so output has variety. Each tick renders the
persona prompt against the current ``WorldContext``, calls Ollama, and
runs the response through ``parse_action`` so a malformed payload
becomes a safe ``rest`` action rather than a crash.

Layer E.2: a post-LLM rate-limit on consecutive intentional rests.
After ``ARTISAN_REST_STREAK_LIMIT`` rests in a row, the next intentional
rest is coerced to speak (if peers exist) or study. Parse-fallback
rests are excluded — they must propagate so the watchdog can see the
real JSON-failure signal.
"""

from __future__ import annotations

import random

from microverse.agents.base import Action, ActionKind, Agent, WorldContext, parse_action
from microverse.config import (
    ARTISAN_REST_STREAK_LIMIT,
    LLM_MAX_TOKENS,
    LLM_TIMEOUT_S,
    SAMPLING_CREATIVE,
)
from microverse.llm.ollama_client import chat
from microverse.ops.metrics import Metrics
from microverse.prompts import render

_PERSONA_TEMPLATE = "persona_artisan.j2"


class Artisan(Agent):
    role = "artisan"
    persona_template = _PERSONA_TEMPLATE
    sampling = SAMPLING_CREATIVE

    def __init__(
        self,
        name: str,
        *,
        soul_tokens: int = 100,
        metrics: Metrics | None = None,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(name, soul_tokens=soul_tokens)
        self._metrics = metrics or Metrics(":memory:")
        self._consecutive_rest = 0
        # Used only by _coerce_non_rest to pick a peer. Accepting an
        # injected Random keeps soak runs reproducible against the
        # outer scheduler's seed.
        self._rng = rng or random.Random()

    def render_prompt(self, world: WorldContext) -> str:
        return render(self.persona_template, name=self.name, world=world)

    def think(self, world: WorldContext) -> Action:
        prompt = self.render_prompt(world)
        result = chat(
            messages=[{"role": "user", "content": prompt}],
            think=False,
            format="json",
            options={**self.sampling, "num_predict": LLM_MAX_TOKENS},
            timeout_s=LLM_TIMEOUT_S,  # explicit so a hung model can't freeze the tick loop
        )
        action = parse_action(result["content"], metrics=self._metrics, agent=self.name)
        return self._maybe_rate_limit(action, world)

    def _maybe_rate_limit(self, action: Action, world: WorldContext) -> Action:
        """Coerce the action when the LLM picks rest too many times in
        a row. ``parse_action`` returns a fallback rest with empty
        thought; we treat any rest with a non-empty thought as
        intentional. Parse-fallback (and meta-leak-block) rests are
        excluded so the ``json_fallback_rest`` / ``consecutive_fail`` /
        ``meta_leak_block`` signals reach the watchdog unobscured.

        Intentional-rest detection note: ``Action`` has
        ``str_strip_whitespace=True``, so a thought of ``" "`` is
        normalised to ``""`` before this check — meaning whitespace-only
        thoughts are correctly classified as fallback-shaped. A
        legitimate LLM rest with a deliberately-empty thought would slip
        the limiter; in practice the persona prompt asks for a thought
        on every action, so we accept this trade-off rather than route
        provenance through ``parse_action``'s return type. Pinned in
        ``test_artisan_rate_limit_skips_empty_thought_rest``.
        """
        is_intentional_rest = action.action == ActionKind.REST and bool(action.thought)
        if is_intentional_rest and self._consecutive_rest >= ARTISAN_REST_STREAK_LIMIT:
            self._metrics.bump("artisan_rest_rate_limited", agent=self.name)
            # Reset to 0 (not held at the limit): the coercion itself
            # counts as a break in the rest streak from the framework's
            # perspective, so the agent earns another full window
            # before the next coercion. Holding at the limit would
            # coerce every intentional rest indefinitely, making
            # ``rest`` effectively unavailable for the rest of the run.
            self._consecutive_rest = 0
            return self._coerce_non_rest(action, world)
        if is_intentional_rest:
            self._consecutive_rest += 1
        else:
            self._consecutive_rest = 0
        return action

    def _coerce_non_rest(self, rested: Action, world: WorldContext) -> Action:
        """Pick a productive replacement: speak to a randomly-chosen
        peer if any are present, else study. Random peer selection
        keeps the simulation varied — picking ``peers_today[0]`` every
        time would address the same villager on every rate-limit fire.
        Preserve the original thought so the narrative log still
        records why the agent was hesitating.
        """
        if world.peers_today:
            return Action(
                thought=rested.thought,
                action=ActionKind.SPEAK,
                target=self._rng.choice(world.peers_today),
                artifact=None,
            )
        return Action(
            thought=rested.thought,
            action=ActionKind.STUDY,
            target=None,
            artifact=None,
        )
