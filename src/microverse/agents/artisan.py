"""Artisan agent: makes things (essays, code, designs, descriptions).

Uses creative sampling so output has variety. Each tick renders the
persona prompt against the current ``WorldContext``, calls Ollama, and
runs the response through ``parse_action`` so a malformed payload
becomes a safe ``rest`` action rather than a crash.
"""

from __future__ import annotations

from microverse.agents.base import Action, Agent, WorldContext, parse_action
from microverse.config import LLM_MAX_TOKENS, LLM_TIMEOUT_S, SAMPLING_CREATIVE
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
    ) -> None:
        super().__init__(name, soul_tokens=soul_tokens)
        self._metrics = metrics or Metrics(":memory:")

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
        return parse_action(result["content"], metrics=self._metrics, agent=self.name)
