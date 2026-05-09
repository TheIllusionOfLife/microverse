"""Slice 3 (R2.b): engagement gate forces a peer-targeted speak after
K silent ticks.

The post-Layer-F 24h soak (data/soak-24h-5, hour 3) showed Aki
dropping all `speak` actions and locking into a craft+study loop with
silent-craftsperson thoughts. Without an exogenous force pulling her
back into peer interaction, the LLM's introspective attractor is the
basin of attraction. The engagement gate is the missing balancing
loop: if an agent has not produced a `speak` with a non-null `target`
in the last K=20 of its own actions, the next tick injects a hint
into `WorldContext` (rendered into the persona prompt) AND a
`required_target` that the post-think coercion enforces.

Two mechanisms together:
  * pre-think: hint surfaces in the persona so the LLM can comply
    voluntarily;
  * post-think: if the LLM disobeys, Artisan coerces the action to
    `speak` with the required target, dropping the original thought
    so the rationalisation does not feed back through the (already
    cut) memory channel.
"""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import patch

from microverse.agents.artisan import Artisan
from microverse.agents.base import ActionKind, WorldContext
from microverse.memory.episodic import EpisodicMemory
from microverse.ops.metrics import Metrics
from microverse.run import _maybe_engagement_target


def _seed_silent_history(ep: EpisodicMemory, actor: str, n: int) -> None:
    for i in range(n):
        ep.append(
            actor=actor,
            action="craft",
            target=None,
            payload={"thought": f"work {i}", "artifact": f"thing-{i}"},
        )


def test_engagement_target_after_K_silent_ticks(tmp_path: Path) -> None:
    """20 of Aki's own actions, none of them targeted speak: the
    helper must return one of the supplied peers."""
    rng = random.Random(0)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_silent_history(ep, "Aki", 20)
        target = _maybe_engagement_target(
            ep, agent_name="Aki", peers=("Bo", "Cy"), rng=rng, interval=20
        )
    assert target in ("Bo", "Cy"), f"expected a peer, got {target!r}"


def test_engagement_no_target_when_recent_targeted_speak(tmp_path: Path) -> None:
    """A single recent targeted speak resets the gate even amid a
    long otherwise-silent stretch."""
    rng = random.Random(0)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Aki", action="speak", target="Bo", payload={})
        _seed_silent_history(ep, "Aki", 5)
        target = _maybe_engagement_target(
            ep, agent_name="Aki", peers=("Bo",), rng=rng, interval=20
        )
    assert target is None, f"recent targeted speak must reset gate, got {target!r}"


def test_engagement_no_target_when_no_peers(tmp_path: Path) -> None:
    """Solo mode: no peers → no engagement firing."""
    rng = random.Random(0)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_silent_history(ep, "Aki", 50)
        target = _maybe_engagement_target(ep, agent_name="Aki", peers=(), rng=rng, interval=20)
    assert target is None


def test_engagement_warmup_under_K_actions(tmp_path: Path) -> None:
    """An agent with fewer than K total actions is in warmup — gate
    does not fire even though it has zero targeted speaks."""
    rng = random.Random(0)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_silent_history(ep, "Aki", 5)
        target = _maybe_engagement_target(
            ep, agent_name="Aki", peers=("Bo",), rng=rng, interval=20
        )
    assert target is None, f"warmup must not fire gate, got {target!r}"


def test_engagement_other_agents_actions_do_not_count(tmp_path: Path) -> None:
    """Only the target agent's own actions count toward the K window.
    Other agents' speaks must not satisfy the gate for self."""
    rng = random.Random(0)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        for _ in range(30):
            ep.append(actor="Bo", action="speak", target="Cy", payload={})
        _seed_silent_history(ep, "Aki", 20)
        target = _maybe_engagement_target(
            ep, agent_name="Aki", peers=("Bo",), rng=rng, interval=20
        )
    assert target == "Bo", f"other agents' speaks must not count, got {target!r}"


def test_engagement_untargeted_speak_does_not_reset(tmp_path: Path) -> None:
    """A `speak` with target=None ('spoke aloud') is NOT a peer
    interaction and must not reset the gate."""
    rng = random.Random(0)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        ep.append(actor="Aki", action="speak", target=None, payload={})
        _seed_silent_history(ep, "Aki", 19)
        target = _maybe_engagement_target(
            ep, agent_name="Aki", peers=("Bo",), rng=rng, interval=20
        )
    assert target == "Bo", f"untargeted speak must not reset, got {target!r}"


def _craft_chat(thought: str, artifact: str | None) -> dict:
    artifact_json = "null" if artifact is None else f'"{artifact}"'
    return {
        "content": (
            f'{{"thought": "{thought}", "action": "craft", '
            f'"target": null, "artifact": {artifact_json}}}'
        ),
        "thinking": "",
        "raw": {},
    }


def _speak_chat(thought: str, target: str | None) -> dict:
    target_json = "null" if target is None else f'"{target}"'
    return {
        "content": (
            f'{{"thought": "{thought}", "action": "speak", '
            f'"target": {target_json}, "artifact": null}}'
        ),
        "thinking": "",
        "raw": {},
    }


def test_artisan_coerces_to_required_target_when_disobeyed(metrics: Metrics) -> None:
    """The LLM picked craft despite the engagement hint; Artisan must
    coerce to speak-to-required_target and bump the metric."""
    canned = _craft_chat(
        thought="The wood demands my full attention.",
        artifact="a small wooden box",
    )
    world = WorldContext(
        peers_today=("Bo",),
        engagement_hint="You must address Bo this tick.",
        required_target="Bo",
    )
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(world)
    assert result.action == ActionKind.SPEAK, f"expected SPEAK, got {result.action!r}"
    assert result.target == "Bo", f"expected target=Bo, got {result.target!r}"
    assert metrics.get("engagement_gate_coerced", agent="Aki") == 1
    # Original rationalisation must NOT survive into the coerced action.
    assert "wood demands" not in result.thought.lower()


def test_artisan_passes_through_when_required_target_addressed(metrics: Metrics) -> None:
    """LLM voluntarily complied with the hint — no coercion fires."""
    canned = _speak_chat(thought="A warm greeting.", target="Bo")
    world = WorldContext(
        peers_today=("Bo",),
        engagement_hint="You must address Bo this tick.",
        required_target="Bo",
    )
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(world)
    assert result.action == ActionKind.SPEAK
    assert result.target == "Bo"
    assert metrics.get("engagement_gate_coerced", agent="Aki") == 0


def test_artisan_no_engagement_field_no_coercion(metrics: Metrics) -> None:
    """Without `required_target` set, behavior is unchanged from
    pre-Layer-G: a craft action passes through normally."""
    canned = _craft_chat(thought="Make a bowl.", artifact="a bowl")
    world = WorldContext(peers_today=())
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(world)
    assert result.action == ActionKind.CRAFT
    assert metrics.get("engagement_gate_coerced", agent="Aki") == 0


def test_artisan_speaks_to_wrong_target_gets_coerced(metrics: Metrics) -> None:
    """LLM spoke but to the wrong peer; the gate enforces the
    specifically-required target."""
    canned = _speak_chat(thought="Hi Cy!", target="Cy")
    world = WorldContext(
        peers_today=("Bo", "Cy"),
        engagement_hint="You must address Bo this tick.",
        required_target="Bo",
    )
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(world)
    assert result.action == ActionKind.SPEAK
    assert result.target == "Bo", f"must coerce target to required_target, got {result.target!r}"
    assert metrics.get("engagement_gate_coerced", agent="Aki") == 1
