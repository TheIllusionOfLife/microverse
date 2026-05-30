"""Belief regen wiring — ADR 0007 Phase 1 (Stage C).

``run._maybe_update_beliefs`` runs the summarizer on the regen cadence and
persists the result in the IdentityStore; ``run._build_self_view`` then
surfaces the stored belief into ``WorldContext.self_view``. The LLM is
mocked so this stays offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from microverse import config
from microverse.agents.artisan import Artisan
from microverse.agents.belief import BeliefSummarizer
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.identity import IdentityStore
from microverse.ops.metrics import Metrics
from microverse.run import _build_self_view, _maybe_update_beliefs


def _chat(content: str) -> Any:
    return {"content": content, "thinking": ""}


def test_beliefs_regenerate_on_cadence_and_reach_self_view(tmp_path: Path) -> None:
    metrics = Metrics(":memory:")
    aki = Artisan(name="Aki", metrics=metrics)
    with (
        EpisodicMemory(tmp_path / "ep.sqlite") as ep,
        IdentityStore(tmp_path / "id.sqlite") as store,
    ):
        ep.append(actor="Aki", action="craft", target=None, payload={"artifact": "a bowl"})
        with patch(
            "microverse.agents.belief.chat",
            return_value=_chat("I believe slow work outlasts fast work."),
        ):
            _maybe_update_beliefs(
                executed=config.BELIEF_UPDATE_INTERVAL,  # a due tick
                agents=[aki],
                episodic=ep,
                identity_store=store,
                summarizer=BeliefSummarizer(),
                metrics=metrics,
            )
        assert store.get("Aki") == "I believe slow work outlasts fast work."
        sv = _build_self_view(ep, aki, known_peers=("Aki",), beliefs=store.get("Aki"))
    assert sv.beliefs == "I believe slow work outlasts fast work."
    metrics.close()


def test_no_regen_off_cadence(tmp_path: Path) -> None:
    metrics = Metrics(":memory:")
    aki = Artisan(name="Aki", metrics=metrics)
    with (
        EpisodicMemory(tmp_path / "ep.sqlite") as ep,
        IdentityStore(tmp_path / "id.sqlite") as store,
    ):
        with patch("microverse.agents.belief.chat", side_effect=AssertionError("must not call")):
            _maybe_update_beliefs(
                executed=config.BELIEF_UPDATE_INTERVAL + 1,  # not a due tick
                agents=[aki],
                episodic=ep,
                identity_store=store,
                summarizer=BeliefSummarizer(),
                metrics=metrics,
            )
        assert store.get("Aki") == ""
    metrics.close()


def test_regen_failure_keeps_prior(tmp_path: Path) -> None:
    metrics = Metrics(":memory:")
    aki = Artisan(name="Aki", metrics=metrics)
    with (
        EpisodicMemory(tmp_path / "ep.sqlite") as ep,
        IdentityStore(tmp_path / "id.sqlite") as store,
    ):
        store.put("Aki", "an earlier belief")
        with patch("microverse.agents.belief.chat", side_effect=RuntimeError("down")):
            _maybe_update_beliefs(
                executed=config.BELIEF_UPDATE_INTERVAL,
                agents=[aki],
                episodic=ep,
                identity_store=store,
                summarizer=BeliefSummarizer(),
                metrics=metrics,
            )
        assert store.get("Aki") == "an earlier belief"  # prior preserved
    metrics.close()
