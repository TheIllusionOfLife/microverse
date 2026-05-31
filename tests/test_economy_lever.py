"""apply_economy_lever: hard-substitute an unaffordable verb (ADR 0008 spike).

When the LLM picks a verb the agent cannot pay for, the lever substitutes
the cheapest affordable *productive* verb (rest only as a last resort, so
the lever drives specialization rather than collapsing onto rest). It must
never produce ``contribute`` (cannot fabricate a WIP + fragment), must
leave a parse-fallback rest untouched, and must NOT fire during a scene
turn (that would abort the scene).
"""

from __future__ import annotations

from typing import Any

from microverse.agents.base import Action, ActionKind, WorldContext, apply_economy_lever
from microverse.ops.metrics import Metrics
from microverse.world.economy import EnergyLedger

_THOUGHT = "I turn to what I can sustain today."


class _FixedRng:
    """Deterministic stand-in for random.Random used by the lever."""

    def choice(self, seq: Any) -> Any:
        return seq[0]


def _ledger(cost_table: dict[str, dict[str, float]], *, names=("Aki",)) -> EnergyLedger:
    return EnergyLedger.fresh(
        list(names), max_energy=100.0, regen_per_tick=12.0, cost_table=cost_table
    )


_ARTISAN = {
    "craft": 6.0,
    "study": 14.0,
    "speak": 16.0,
    "travel": 18.0,
    "rest": 0.0,
    "contribute": 22.0,
}


def _lever(action, world, ledger, metrics, role="artisan", name="Aki"):
    return apply_economy_lever(
        action,
        world,
        ledger=ledger,
        role=role,
        agent_name=name,
        rng=_FixedRng(),
        metrics=metrics,
        replacement_thought=_THOUGHT,
    )


def test_affordable_verb_passes_through(metrics: Metrics):
    ledger = _ledger({"artisan": _ARTISAN})  # full energy
    action = Action(thought="make a bowl", action=ActionKind.CRAFT, artifact="a bowl")
    out = _lever(action, WorldContext(), ledger, metrics)
    assert out is action or out.action == ActionKind.CRAFT
    assert metrics.get("economy_verb_substituted", agent="Aki") == 0


def test_unaffordable_verb_substituted_to_cheapest_productive(metrics: Metrics):
    ledger = _ledger({"artisan": _ARTISAN})
    # At 15 the artisan affords craft(6) and study(14) but not speak(16) /
    # travel(18) / contribute(22). craft is a payload verb the lever cannot
    # fabricate, so the substitution target is the cheapest PAYLOAD-FREE verb:
    # study. (A hollow craft here would bypass the empty-craft guard — review.)
    ledger._pool["Aki"] = 15.0
    action = Action(
        thought="add to the scroll",
        action=ActionKind.CONTRIBUTE,
        contribute_to="village_scroll",
        artifact="x" * 200,
    )
    out = _lever(action, WorldContext(), ledger, metrics)
    assert out.action == ActionKind.STUDY  # cheapest affordable payload-free verb
    assert out.contribute_to is None
    assert out.artifact is None
    assert metrics.get("economy_verb_substituted", agent="Aki") == 1


def test_substitution_never_contribute(metrics: Metrics):
    # Even if contribute were notionally "cheapest", it is excluded.
    cheap_contribute = {
        **_ARTISAN,
        "contribute": 0.0,
        "craft": 50.0,
        "study": 50.0,
        "speak": 50.0,
        "travel": 50.0,
    }
    ledger = _ledger({"artisan": cheap_contribute})
    while ledger.current("Aki") > 5.0:
        ledger.deduct("Aki", "artisan", "rest")  # 0 cost; loop guard below
        break
    # Force low energy directly.
    ledger._pool["Aki"] = 5.0
    action = Action(thought="x", action=ActionKind.TRAVEL)
    out = _lever(action, WorldContext(), ledger, metrics)
    assert out.action != ActionKind.CONTRIBUTE


def test_fallback_rest_untouched(metrics: Metrics):
    ledger = _ledger({"artisan": _ARTISAN})
    ledger._pool["Aki"] = 0.0
    fallback = Action(thought="", action=ActionKind.REST)  # parse-fallback shape
    out = _lever(fallback, WorldContext(), ledger, metrics)
    assert out.action == ActionKind.REST
    assert out.thought == ""
    assert metrics.get("economy_verb_substituted", agent="Aki") == 0


def test_intentional_rest_passes_through(metrics: Metrics):
    ledger = _ledger({"artisan": _ARTISAN})
    ledger._pool["Aki"] = 0.0
    rest = Action(thought="I pause to gather myself.", action=ActionKind.REST)
    out = _lever(rest, WorldContext(), ledger, metrics)
    assert out.action == ActionKind.REST  # rest is always affordable
    assert metrics.get("economy_verb_substituted", agent="Aki") == 0


def test_no_substitution_during_scene_turn(metrics: Metrics):
    ledger = _ledger({"artisan": _ARTISAN})
    ledger._pool["Aki"] = 0.0
    world = WorldContext(scene_wip_name="village_scroll")
    action = Action(
        thought="add a line",
        action=ActionKind.CONTRIBUTE,
        contribute_to="village_scroll",
        artifact="x" * 200,
    )
    out = _lever(action, world, ledger, metrics)
    assert out.action == ActionKind.CONTRIBUTE  # scene turn never substituted
    assert metrics.get("economy_verb_substituted", agent="Aki") == 0


def test_speak_substitution_sets_target_when_peers(metrics: Metrics):
    # Cost table where speak is the cheapest affordable productive verb.
    speak_cheap = {
        "speak": 4.0,
        "craft": 40.0,
        "study": 40.0,
        "travel": 40.0,
        "rest": 0.0,
        "contribute": 40.0,
    }
    ledger = _ledger({"scholar": speak_cheap}, names=("Cy",))
    ledger._pool["Cy"] = 5.0
    world = WorldContext(peers_today=("Aki",))
    action = Action(thought="study the soil", action=ActionKind.STUDY)
    out = apply_economy_lever(
        action,
        world,
        ledger=ledger,
        role="scholar",
        agent_name="Cy",
        rng=_FixedRng(),
        metrics=metrics,
        replacement_thought=_THOUGHT,
    )
    assert out.action == ActionKind.SPEAK
    assert out.target == "Aki"
