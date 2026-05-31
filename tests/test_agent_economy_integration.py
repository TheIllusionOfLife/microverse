"""Agent.think() x EnergyLedger wiring (ADR 0008 spike).

Exercises the economy lever inside Artisan/Scholar think() with a mocked
chat: it substitutes an unaffordable verb, is a no-op when no ledger is
attached, is skipped during scene turns, records the parsed-verb telemetry,
and runs AFTER diversity but BEFORE the engagement gate (which must win).
"""

from __future__ import annotations

from unittest.mock import patch

from microverse.agents.artisan import Artisan
from microverse.agents.base import ActionKind, WorldContext
from microverse.config import VERB_COST_BY_ROLE
from microverse.ops.metrics import Metrics
from microverse.world.economy import EnergyLedger


def _chat(action: str, *, thought: str = "x", target=None, artifact=None) -> dict:
    import json

    payload = {"thought": thought, "action": action, "target": target, "artifact": artifact}
    return {"content": json.dumps(payload), "thinking": "", "raw": {}}


def _drained_ledger(name: str, level: float) -> EnergyLedger:
    led = EnergyLedger.fresh(
        [name], max_energy=100.0, regen_per_tick=12.0, cost_table=VERB_COST_BY_ROLE
    )
    led._pool[name] = level
    return led


def test_think_substitutes_when_energy_low(metrics: Metrics):
    # At 10 energy an Artisan affords craft(6) but not study(14).
    led = _drained_ledger("Aki", 10.0)
    a = Artisan(name="Aki", metrics=metrics)
    a.attach_energy(led)
    with patch("microverse.agents.artisan.chat", return_value=_chat("study")):
        out = a.think(WorldContext())
    assert out.action == ActionKind.CRAFT  # cheapest affordable productive verb
    assert metrics.get("economy_verb_substituted", agent="Aki") == 1


def test_no_economy_when_ledger_unattached(metrics: Metrics):
    a = Artisan(name="Aki", metrics=metrics)  # no attach_energy
    with patch("microverse.agents.artisan.chat", return_value=_chat("study")):
        out = a.think(WorldContext())
    assert out.action == ActionKind.STUDY  # unchanged: economy is a no-op
    assert metrics.get("economy_verb_substituted", agent="Aki") == 0


def test_economy_skipped_during_scene_turn(metrics: Metrics):
    led = _drained_ledger("Aki", 0.0)  # affords nothing but rest
    a = Artisan(name="Aki", metrics=metrics)
    a.attach_energy(led)
    world = WorldContext(scene_wip_name="village_scroll")
    with patch("microverse.agents.artisan.chat", return_value=_chat("study")):
        out = a.think(world)
    assert out.action == ActionKind.STUDY  # scene turns are never substituted
    assert metrics.get("economy_verb_substituted", agent="Aki") == 0


def test_verb_trace_records_parsed_verb(metrics: Metrics):
    led = _drained_ledger("Aki", 100.0)  # full: no substitution
    a = Artisan(name="Aki", metrics=metrics)
    a.attach_energy(led)
    with patch(
        "microverse.agents.artisan.chat",
        return_value=_chat("craft", artifact="a long enough fragment to be a real craft artifact"),
    ):
        a.think(WorldContext())
    assert a._verb_trace == {"parsed_verb": "craft"}


def test_engagement_gate_wins_over_economy(metrics: Metrics):
    # Drained energy would substitute study->craft, but the engagement gate
    # runs LAST and must coerce a speak to the required target regardless.
    led = _drained_ledger("Aki", 10.0)
    a = Artisan(name="Aki", metrics=metrics)
    a.attach_energy(led)
    world = WorldContext(required_target="Bo", peers_today=("Bo",))
    with patch("microverse.agents.artisan.chat", return_value=_chat("study")):
        out = a.think(world)
    assert out.action == ActionKind.SPEAK
    assert out.target == "Bo"
    # Economy still ran first (substituted study->craft) before engagement won.
    assert metrics.get("economy_verb_substituted", agent="Aki") == 1
