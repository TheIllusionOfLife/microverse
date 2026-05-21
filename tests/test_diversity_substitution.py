"""Phase D Step 2 — post-action verb-diversity substitution.

When the LLM ignores the novelty_hint and re-emits the dominant verb,
the agent's ``_maybe_diversify`` flips a coin and substitutes the
suggested verb. Counter ``diversity_lever_substituted`` is bumped.

Carve-outs (same precedent as engagement-gate):
  - Fallback REST (empty thought) is NOT substituted.
  - CONTRIBUTE target is never the substitution choice (the LLM
    must compose a fragment; we can't fabricate one).
"""

from __future__ import annotations

import random
from pathlib import Path

from microverse.agents.artisan import Artisan
from microverse.agents.base import Action, ActionKind, WorldContext
from microverse.ops.metrics import Metrics


def _craft_action() -> Action:
    return Action(
        thought="thinking about cedar",
        action=ActionKind.CRAFT,
        target=None,
        artifact="a small cedar bowl",
    )


def _fallback_rest_action() -> Action:
    return Action(thought="", action=ActionKind.REST, target=None, artifact=None)


def test_no_hint_means_no_substitution(tmp_path: Path) -> None:
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:
        a = Artisan("Aki", metrics=metrics, rng=random.Random(0))
        world = WorldContext(novelty_hint="")
        action = _craft_action()
        out = a._maybe_diversify(action, world)
        assert out == action
    finally:
        metrics.close()


def test_substitution_fires_when_hint_targets_dominant_verb(tmp_path: Path) -> None:
    """An RNG seed of 0 produces a draw < 0.30 on the first call → fire."""
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:
        # Force the substitution to fire by feeding an RNG whose first
        # uniform sample is below _DIVERSIFY_PROB. Random(0) first call
        # is ~0.844 — that's ABOVE 0.30, so we need a different seed
        # for a deterministic "fire" path. Use a stub Random.
        class _ForceFire:
            def random(self) -> float:
                return 0.01

            def choice(self, seq):
                return seq[0]

        a = Artisan("Aki", metrics=metrics, rng=_ForceFire())  # type: ignore[arg-type]
        world = WorldContext(
            novelty_hint="You have leaned heavily on craft lately; consider speak.",
            peers_today=("Bo",),
        )
        action = _craft_action()
        out = a._maybe_diversify(action, world)
        assert out.action == ActionKind.SPEAK
        assert out.target == "Bo"
        assert out.artifact is None
    finally:
        metrics.close()


def test_substitution_skips_when_rng_draw_above_threshold(tmp_path: Path) -> None:
    """When the coin flip lands above the substitution probability,
    the original action passes through unchanged."""
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:

        class _ForceSkip:
            def random(self) -> float:
                return 0.99

            def choice(self, seq):
                return seq[0]

        a = Artisan("Aki", metrics=metrics, rng=_ForceSkip())  # type: ignore[arg-type]
        world = WorldContext(
            novelty_hint="You have leaned heavily on craft lately; consider speak.",
            peers_today=("Bo",),
        )
        action = _craft_action()
        out = a._maybe_diversify(action, world)
        assert out == action
    finally:
        metrics.close()


def test_substitution_preserves_fallback_rest(tmp_path: Path) -> None:
    """Fallback REST (empty thought) must NOT be substituted —
    masking it would hide the JSON-failure signal."""
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:

        class _ForceFire:
            def random(self) -> float:
                return 0.0

            def choice(self, seq):
                return seq[0]

        a = Artisan("Aki", metrics=metrics, rng=_ForceFire())  # type: ignore[arg-type]
        world = WorldContext(
            novelty_hint="You have leaned heavily on rest lately; consider speak.",
            peers_today=("Bo",),
        )
        action = _fallback_rest_action()
        out = a._maybe_diversify(action, world)
        assert out == action  # untouched
    finally:
        metrics.close()


def test_substitution_skips_contribute_target(tmp_path: Path) -> None:
    """When the hint suggests CONTRIBUTE, the lever does not fire —
    the LLM must compose a fragment with a valid WIP target itself.
    Substituting blindly would hard-fold in the validator."""
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:

        class _ForceFire:
            def random(self) -> float:
                return 0.0

            def choice(self, seq):
                return seq[0]

        a = Artisan("Aki", metrics=metrics, rng=_ForceFire())  # type: ignore[arg-type]
        world = WorldContext(
            novelty_hint="You have leaned heavily on craft lately; consider contribute.",
        )
        action = _craft_action()
        out = a._maybe_diversify(action, world)
        assert out == action
    finally:
        metrics.close()


def test_substitution_skips_when_llm_already_diversified(tmp_path: Path) -> None:
    """When the LLM picked a verb OTHER than the dominant one, the
    lever does not fire even if the hint is set."""
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:

        class _ForceFire:
            def random(self) -> float:
                return 0.0

            def choice(self, seq):
                return seq[0]

        a = Artisan("Aki", metrics=metrics, rng=_ForceFire())  # type: ignore[arg-type]
        world = WorldContext(
            novelty_hint="You have leaned heavily on craft lately; consider speak.",
            peers_today=("Bo",),
        )
        # LLM already picked study — leave it.
        action = Action(
            thought="reflecting",
            action=ActionKind.STUDY,
            target=None,
            artifact=None,
        )
        out = a._maybe_diversify(action, world)
        assert out == action
    finally:
        metrics.close()
