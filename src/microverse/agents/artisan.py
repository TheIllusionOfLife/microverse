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

Layer F.2: a post-LLM coercion for craft actions whose ``artifact``
field is null or whitespace. Empty-artifact crafts are coerced to
``study`` with a neutral replacement thought. Runs BEFORE the rest
rate-limiter — empty-craft is an active tick, not rest-avoidance.

Path-3 note: the agent's own thoughts and actions are persisted in
the episodic log for audit, watchdog, and harvest, but they NO
LONGER feed back into the next prompt. The "narrative laundering"
mitigations below remain useful as defence-in-depth (a future
re-introduction of any self-history channel must not be able to
re-acquire the silent-woodworker attractor through stored thoughts).
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

# Replacement thought for Layer F.2 empty-craft coercion. Crucially
# neutral: it does NOT mention silence/fatigue/etc. Path-3 already
# strips self-history from prompts, so this is now defence-in-depth
# — the audit log records the coercion event, but no re-routing of
# the original thought can sustain the trap.
_EMPTY_CRAFT_REPLACEMENT_THOUGHT = (
    "The work needs a concrete form, so I study materials before making it."
)

# Layer-G slice 3 (R2.b): replacement thought for the engagement-gate
# coercion. Like F.2 it is deliberately *neutral and external-facing*:
# the introspective rationalisation the LLM produced for the disobeyed
# action would otherwise enter audit-only state but the action's own
# logged-but-not-fed-back semantics make this defence-in-depth: a
# future change that re-enables thought feedback should not allow the
# disobeyed monologue to seed the next tick.
_ENGAGEMENT_REPLACEMENT_THOUGHT = "I turn from my work to greet a neighbor."
# Phase D: substitution probability when the LLM ignores novelty_hint
# and re-emits the dominant verb. 0.30 ≈ 1-in-3, balanced between
# "agent has agency" and "lever actually moves the share."
_DIVERSIFY_PROB = 0.30
_DIVERSITY_REPLACEMENT_THOUGHT = "I try something other than my usual rhythm today."


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
        action = parse_action(
            result["content"],
            metrics=self._metrics,
            agent=self.name,
            workshop=self._workshop,
        )
        # F.2 must run BEFORE the rest rate-limiter: empty-craft is an
        # active tick that should reset the rest streak, not pass
        # through it as rest.
        action = self._maybe_coerce_empty_craft(action)
        action = self._maybe_rate_limit(action, world)
        # Phase D: post-action verb-diversity substitution. When the LLM
        # ignored the novelty_hint and re-emitted the dominant verb, flip
        # a coin to substitute it. Bumps diversity_lever_substituted so
        # the dashboard / WRITEUP can show the share of lever-flipped
        # vs LLM-chosen verbs. Skips when no hint is active (the
        # run-loop only sets novelty_hint above the dominance threshold).
        action = self._maybe_diversify(action, world)
        # Engagement gate runs LAST so it overrides any earlier coercion
        # (e.g. the rest rate-limiter picking a different peer).
        return self._maybe_enforce_engagement(action, world)

    def _maybe_diversify(self, action: Action, world: WorldContext) -> Action:
        """Phase D Step 2 (structural counter-pressure for ADR 0002).

        When ``world.novelty_hint`` is non-empty AND the parsed action's
        verb matches the dominant verb the hint is trying to break out
        of, substitute the action verb with the hint's suggested verb
        at ``_DIVERSIFY_PROB`` (default 0.30). Uses a neutral thought so
        a rationalisation cannot survive into future ticks.

        Carve-outs (mirror engagement-gate and rate-limiter precedent):
          - Fallback REST (parse failure / meta-leak-block) is left
            alone — diversifying it would mask the underlying signal.
          - When the hint suggests SPEAK we set target to None; the
            engagement gate runs after and may overwrite.
          - When the hint suggests CONTRIBUTE we DO NOT substitute,
            because contributes require a configured WIP target and
            non-empty fragment text — substituting blindly would
            instantly hard-fold in the validator. The hint exists to
            push the LLM toward CONTRIBUTE at compose time; if the LLM
            doesn't bite, that is acceptable.
        """
        from microverse.agents.base import ActionKind as _AK

        if not world.novelty_hint:
            return action
        is_fallback_rest = action.action == _AK.REST and not action.thought
        if is_fallback_rest:
            return action
        # Pull the suggested verb out of the hint. The run-loop wrote
        # "You have leaned heavily on X lately; consider Y." — Y is
        # the last word before the period.
        hint = world.novelty_hint.rstrip(".").strip()
        if "consider " not in hint:
            return action
        suggested = hint.split("consider ")[-1].strip().strip(".")
        try:
            target_verb = _AK(suggested)
        except ValueError:
            return action
        # Only fire when the LLM did pick the dominant verb the hint
        # was trying to break. The hint text encodes that as "leaned
        # heavily on X" — extract X.
        if "leaned heavily on " not in hint:
            return action
        dominant = hint.split("leaned heavily on ")[-1].split(" lately")[0].strip().strip(".")
        if action.action.value != dominant:
            return action
        if target_verb == _AK.CONTRIBUTE:
            return action
        if self._rng.random() >= _DIVERSIFY_PROB:
            return action
        self._metrics.bump("diversity_lever_substituted", agent=self.name)
        new_target: str | None = None
        if target_verb == _AK.SPEAK and world.peers_today:
            new_target = self._rng.choice(world.peers_today)
        return action.model_copy(
            update={
                "thought": _DIVERSITY_REPLACEMENT_THOUGHT,
                "action": target_verb,
                "target": new_target,
                "artifact": None,
                "contribute_to": None,
            }
        )

    def _maybe_enforce_engagement(self, action: Action, world: WorldContext) -> Action:
        """Layer-G slice 3 (R2.b): if the runtime set ``required_target``
        on this tick's ``WorldContext`` and the LLM did not produce a
        ``speak`` to that exact peer, coerce. Drops the original thought
        so a disobeyed-rationalisation cannot enter audit-or-future
        feedback as a justification for ignoring the gate.

        Safety carve-out: a parse-fallback or meta-leak-blocked REST
        (``parse_action`` returns ``Action(thought='', action=REST,
        target=None, artifact=None)``) must propagate untouched.
        Coercing it into a SPEAK would silently disguise a JSON failure
        or immersion break as social behavior, hiding the
        ``json_fallback_rest`` / ``meta_leak_block`` signal the watchdog
        depends on. Mirrors ``_maybe_rate_limit``'s intentional-rest
        heuristic (REST + non-empty thought) for consistency.
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

    def _maybe_coerce_empty_craft(self, action: Action) -> Action:
        """Layer F.2: if the LLM picks ``craft`` without populating
        ``artifact`` (None, empty, or whitespace), coerce to ``study``
        with a neutral replacement thought.

        The original thought is intentionally DROPPED. Under Path-3
        self-history no longer feeds back into prompts, so this is
        now belt-and-suspenders: the audit log records the coercion
        but cannot re-introduce the silent-woodworker narrative.
        Bumps ``artisan_empty_craft_coerced`` (per-agent metric) so
        runaway firing is observable.

        Note: ``Action`` has ``str_strip_whitespace=True``, so a
        whitespace-only artifact arrives here normalised to ``""`` —
        the truthy check covers both None and stripped-empty.
        """
        if action.action != ActionKind.CRAFT:
            return action
        if action.artifact:
            return action
        self._metrics.bump("artisan_empty_craft_coerced", agent=self.name)
        # ``model_copy(update=...)`` over direct ``Action(...)`` so a
        # future field added to ``Action`` is preserved by default;
        # forgetting to mention it here would silently drop it.
        return action.model_copy(
            update={
                "thought": _EMPTY_CRAFT_REPLACEMENT_THOUGHT,
                "action": ActionKind.STUDY,
                "target": None,
                "artifact": None,
            }
        )

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
