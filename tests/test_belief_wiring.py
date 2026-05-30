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
from microverse.run import _build_self_view, _maybe_update_beliefs, _RelationshipLedgerCache


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


def test_relationship_ledger_cache_throttles_and_refreshes(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Cy", action="speak", target="Aki", payload={"thought": "hi"})
        cache = _RelationshipLedgerCache(ep, refresh_events=5)
        first = cache.get("Aki", ("Aki", "Cy"))
        assert first[0].addressed_you == 1
        # Add fewer than the threshold of new events: cached value is reused
        # (stale by design — relationships drift slowly).
        ep.append(actor="Cy", action="speak", target="Aki", payload={"thought": "hi again"})
        assert cache.get("Aki", ("Aki", "Cy"))[0].addressed_you == 1
        # Cross the refresh threshold: the ledger recomputes.
        for _ in range(5):
            ep.append(actor="Cy", action="speak", target="Aki", payload={"thought": "x"})
        assert cache.get("Aki", ("Aki", "Cy"))[0].addressed_you == 7


def test_relationship_ledger_cache_derives_late_arrival_on_demand(tmp_path: Path) -> None:
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Cy", action="speak", target="Aki", payload={"thought": "hi"})
        cache = _RelationshipLedgerCache(ep, refresh_events=1000)
        cache.get("Aki", ("Aki", "Cy"))  # primes the cache for Aki/Cy only
        # A Stranger that arrives mid-run is not in the cache yet; it is
        # derived on demand rather than returning empty.
        ep.append(actor="Eli", action="speak", target="Aki", payload={"thought": "hello"})
        facts = cache.get("Aki", ("Aki", "Cy", "Eli"))
        assert any(f.peer == "Eli" for f in facts)
