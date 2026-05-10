"""Scholar agent: observation-leaning second resident.

Layer-G slice 4 (R2.c): the project's stated mission is a "society"
of agents producing harvested artifacts, but for the entire history
of the run only one agent (Aki the Artisan) was ever resident.
Codex's review of the Layer-G plan flagged that adding a second plain
Artisan risks *mutual* reverence — two craftspeople reading each
other's silence-justifying logs and converging on the same monoculture.
A structurally different role breaks that symmetry: Scholar prefers
observation, conversation, and short written notes; it has no rest
rate-limiter and no empty-craft coercion (those were Artisan-specific
failure-mode patches), and uses ``SAMPLING_FACTUAL`` for steadier
observational output.

Engagement gate (Layer-G slice 3) applies here just as it does to
Artisan — long-silent stretches still call for a peer-targeted speak.
The 10-line coercion is duplicated rather than abstracted; per Codex,
premature unification of coercion helpers is out of scope (semantics
differ for each helper).
"""

from __future__ import annotations

from microverse.agents.base import Action, ActionKind, Agent, WorldContext, parse_action
from microverse.config import LLM_MAX_TOKENS, LLM_TIMEOUT_S, SAMPLING_FACTUAL
from microverse.llm.ollama_client import chat
from microverse.ops.metrics import Metrics
from microverse.prompts import render

_PERSONA_TEMPLATE = "persona_scholar.j2"

_ENGAGEMENT_REPLACEMENT_THOUGHT = "I set my notes aside to greet a neighbor."


class Scholar(Agent):
    role = "scholar"
    persona_template = _PERSONA_TEMPLATE
    sampling = SAMPLING_FACTUAL

    def __init__(
        self,
        name: str,
        *,
        soul_tokens: int = 70,
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
            timeout_s=LLM_TIMEOUT_S,
        )
        action = parse_action(result["content"], metrics=self._metrics, agent=self.name)
        return self._maybe_enforce_engagement(action, world)

    def _maybe_enforce_engagement(self, action: Action, world: WorldContext) -> Action:
        """Shared with Artisan (semantics identical). Coerce a non-
        compliant action into ``speak`` to ``required_target`` when the
        runtime's engagement gate is firing. Drops the original thought
        for the same reason F.2 does — a disobeyed rationalisation
        should not survive into audit context where a future change
        could re-enable feedback.

        Safety carve-out: a parse-fallback or meta-leak-blocked REST
        (empty thought, no target, no artifact) must propagate so the
        watchdog still sees ``json_fallback_rest`` /
        ``meta_leak_block``. Mirrors Artisan's guard.
        """
        if not world.required_target:
            return action
        is_fallback_rest = action.action == ActionKind.REST and not action.thought
        if is_fallback_rest:
            return action
        if action.action == ActionKind.SPEAK and action.target == world.required_target:
            return action
        self._metrics.bump("engagement_gate_coerced", agent=self.name)
        return action.model_copy(
            update={
                "thought": _ENGAGEMENT_REPLACEMENT_THOUGHT,
                "action": ActionKind.SPEAK,
                "target": world.required_target,
                "artifact": None,
            }
        )
