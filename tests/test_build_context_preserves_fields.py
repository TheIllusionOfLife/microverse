"""Regression: ``build_context`` must preserve EVERY ``world_base``
field it does not itself own.

Stage A of ADR 0007 Phase 1. The pre-refactor ``build_context``
reconstructed ``WorldContext`` field-by-field and silently DROPPED the
``novelty_*`` and ``scene_*`` fields set on ``world_base`` by
``run.py:_build_per_tick_world_base``. On the single-tick path this
meant the structured novelty verbs never reached the agent, so
``apply_diversity_lever`` (``agents/base.py``) always no-op'd — a latent
bug that disabled the Phase-D diversity lever.

The fix routes the return through ``dataclasses.replace`` so only the
two fields ``build_context`` owns (``lore_excerpt``, ``workshop_view``)
are overwritten and everything else rides through verbatim. This is
also the carrier path for the Stage-B ``self_view`` field.
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.base import SceneTurn, WorldContext
from microverse.memory import build_context
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory


def test_build_context_preserves_novelty_fields(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        out = build_context(
            world_base=WorldContext(
                novelty_hint="You have leaned hard on 'craft' lately; try another verb.",
                novelty_dominant_verb="craft",
                novelty_suggested_verb="speak",
            ),
            episodic=ep,
            semantic=se,
            topic="",
        )
    assert out.novelty_hint.startswith("You have leaned")
    assert out.novelty_dominant_verb == "craft"
    assert out.novelty_suggested_verb == "speak"


def test_build_context_preserves_scene_fields(tmp_path: Path) -> None:
    scene_ctx = (SceneTurn(author="Cy", text="The loom hums in the early light."),)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        out = build_context(
            world_base=WorldContext(
                scene_context=scene_ctx,
                scene_wip_name="workshop.loom",
            ),
            episodic=ep,
            semantic=se,
            topic="",
        )
    assert out.scene_context == scene_ctx
    assert out.scene_wip_name == "workshop.loom"
