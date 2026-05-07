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

from microverse.agents.base import Action, ActionKind, Agent, WorldContext, parse_action
from microverse.config import LLM_MAX_TOKENS, LLM_TIMEOUT_S, SAMPLING_CREATIVE
from microverse.llm.ollama_client import chat
from microverse.ops.metrics import Metrics
from microverse.prompts import render

_PERSONA_TEMPLATE = "persona_artisan.j2"

# After this many consecutive intentional rests, the next intentional
# rest is coerced. Three is conservative — a real artisan can rest a
# few times in a row legitimately, but four-in-a-row is the empirical
# trap signature from soak-24h-3.
ARTISAN_REST_STREAK_LIMIT: int = 3


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
    ) -> None:
        super().__init__(name, soul_tokens=soul_tokens)
        self._metrics = metrics or Metrics(":memory:")
        self._consecutive_rest = 0

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
        intentional. Parse-fallback rests are excluded so the
        ``json_fallback_rest`` / ``consecutive_fail`` signals reach the
        watchdog unobscured.
        """
        is_intentional_rest = action.action == ActionKind.REST and bool(action.thought)
        if is_intentional_rest and self._consecutive_rest >= ARTISAN_REST_STREAK_LIMIT:
            self._metrics.bump("artisan_rest_rate_limited", agent=self.name)
            self._consecutive_rest = 0
            return self._coerce_non_rest(action, world)
        if is_intentional_rest:
            self._consecutive_rest += 1
        else:
            self._consecutive_rest = 0
        return action

    @staticmethod
    def _coerce_non_rest(rested: Action, world: WorldContext) -> Action:
        """Pick a productive replacement: speak to a peer if any are
        present, else study. Preserve the original thought so the
        narrative log still records why the agent was hesitating.
        """
        if world.peers_today:
            return Action(
                thought=rested.thought,
                action=ActionKind.SPEAK,
                target=world.peers_today[0],
                artifact=None,
            )
        return Action(
            thought=rested.thought,
            action=ActionKind.STUDY,
            target=None,
            artifact=None,
        )
