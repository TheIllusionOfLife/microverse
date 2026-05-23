"""Scene micro-loop — ADR 0005 Decision 3.

The runner emits one ``scene.open`` event, then drives 3 think() calls
with a fresh WorldContext per turn (carrying scene_context that
contains the prior turns). On success: 3 ``contribute`` events land
with matching scene_id and turn_index 1/2/3. On any failure
(think raises, parsed action off-topic, commit raises) a ``scene.abort``
is written and partial fragments stay.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import ClassVar

from microverse.agents.base import Action, ActionKind, Agent, SceneTurn, WorldContext
from microverse.memory.episodic import EpisodicMemory
from microverse.ops.metrics import Metrics
from microverse.world.scene import SceneRunner, pick_authors
from microverse.world.workshop import CONFIGURED_WIPS


class _FakeAgent(Agent):
    role = "artisan"
    persona_template = ""
    sampling: ClassVar[dict[str, float | int]] = {}

    def __init__(self, name: str, *, plan: list[Action]) -> None:
        super().__init__(name=name)
        self._plan = list(plan)
        self.last_world: WorldContext | None = None

    def think(self, world: WorldContext) -> Action:
        self.last_world = world
        if not self._plan:
            raise RuntimeError("fake agent exhausted its plan")
        return self._plan.pop(0)


def _contrib_action(wip: str, text: str) -> Action:
    return Action(
        thought="",
        action=ActionKind.CONTRIBUTE,
        target=None,
        artifact=text,
        contribute_to=wip,
    )


def _world_factory_factory(seed_world: WorldContext):
    def _factory(
        *,
        agent: Agent,
        scene_context: tuple[SceneTurn, ...],
        scene_wip_name: str = "",
    ) -> WorldContext:
        # Fresh WorldContext per turn with scene_context populated.
        from dataclasses import replace

        return replace(
            seed_world,
            scene_context=scene_context,
            scene_wip_name=scene_wip_name,
        )

    return _factory


def _commit_action_factory(episodic: EpisodicMemory):
    def _commit(agent: Agent, action: Action) -> None:
        episodic.append(
            actor=agent.name,
            action="contribute",
            target=action.contribute_to,
            payload={
                "thought": action.thought,
                "artifact": action.artifact,
                "fragment": action.artifact,
                "contribute_to": action.contribute_to,
                "scene_id": action.scene_id,
                "turn_index": action.turn_index,
            },
        )

    return _commit


def test_full_scene_path(tmp_path: Path) -> None:
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:
        wip = CONFIGURED_WIPS[0]
        aki = _FakeAgent(
            "Aki",
            plan=[
                _contrib_action(wip, "Aki turn 1: a proposal for the loom design."),
                _contrib_action(wip, "Aki turn 3 close: combining both views."),
            ],
        )
        bo = _FakeAgent(
            "Bo",
            plan=[
                _contrib_action(wip, "Bo turn 2: a response from the marsh tradition."),
            ],
        )
        # Force deterministic pick: peers=[Bo], turn3 falls back to initiator (Aki).
        runner = SceneRunner(
            episodic=em,
            commit_action=_commit_action_factory(em),
            world_factory=_world_factory_factory(WorldContext()),
            rng=random.Random(0),
            metrics=metrics,
        )

        result = runner.run(aki, wip, peers=[bo])

        assert not result.aborted
        assert result.completed_turns == 3
        # Check episodic log: one scene.open + 3 contributes, in order.
        all_events = em.last(10)
        kinds = [(e.action, e.actor) for e in reversed(all_events)]
        assert kinds[0] == ("scene.open", "scene")
        assert kinds[1:] == [
            ("contribute", "Aki"),
            ("contribute", "Bo"),
            ("contribute", "Aki"),
        ]
        # scene_id stamped on every contribute.
        sids = [e.payload.get("scene_id") for e in reversed(all_events) if e.action == "contribute"]
        assert all(s == result.scene_id for s in sids)
        # turn_index 1/2/3 in order.
        tids = [
            e.payload.get("turn_index") for e in reversed(all_events) if e.action == "contribute"
        ]
        assert tids == [1, 2, 3]
    finally:
        em.close()
        metrics.close()


def test_abort_on_think_error(tmp_path: Path) -> None:
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:
        wip = CONFIGURED_WIPS[0]

        class _Boomer(_FakeAgent):
            def think(self, world: WorldContext) -> Action:
                raise RuntimeError("simulated")

        aki = _FakeAgent("Aki", plan=[_contrib_action(wip, "Aki turn 1.")])
        bo = _Boomer("Bo", plan=[])
        runner = SceneRunner(
            episodic=em,
            commit_action=_commit_action_factory(em),
            world_factory=_world_factory_factory(WorldContext()),
            rng=random.Random(0),
            metrics=metrics,
        )

        result = runner.run(aki, wip, peers=[bo])

        assert result.aborted
        assert result.completed_turns == 1  # turn 1 landed
        # scene.abort emitted.
        actions = [e.action for e in em.last(10)]
        assert "scene.abort" in actions
    finally:
        em.close()
        metrics.close()


def test_abort_on_off_topic_action(tmp_path: Path) -> None:
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:
        wip = CONFIGURED_WIPS[0]
        # Turn 2 returns a SPEAK instead of CONTRIBUTE → off_topic abort.
        aki = _FakeAgent("Aki", plan=[_contrib_action(wip, "Aki turn 1.")])
        bo = _FakeAgent(
            "Bo",
            plan=[
                Action(
                    thought="",
                    action=ActionKind.SPEAK,
                    target="Aki",
                    artifact=None,
                    contribute_to=None,
                ),
            ],
        )
        runner = SceneRunner(
            episodic=em,
            commit_action=_commit_action_factory(em),
            world_factory=_world_factory_factory(WorldContext()),
            rng=random.Random(0),
            metrics=metrics,
        )

        result = runner.run(aki, wip, peers=[bo])
        assert result.aborted
        assert result.reason == "off_topic"
        actions = [e.action for e in em.last(10)]
        assert "scene.abort" in actions
    finally:
        em.close()
        metrics.close()


def test_turn3_same_author_sees_own_turn1_in_scene_context(tmp_path: Path) -> None:
    """When only one peer is available, turn 3 closes back to the
    initiator. The initiator's turn 3 WorldContext MUST contain their
    own turn-1 fragment in scene_context — the Path-3 carve-out for
    scenes is the load-bearing piece."""
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:
        wip = CONFIGURED_WIPS[0]
        aki = _FakeAgent(
            "Aki",
            plan=[
                _contrib_action(wip, "Aki TURN ONE fragment marker."),
                _contrib_action(wip, "Aki TURN THREE close."),
            ],
        )
        bo = _FakeAgent("Bo", plan=[_contrib_action(wip, "Bo turn 2 fragment.")])

        runner = SceneRunner(
            episodic=em,
            commit_action=_commit_action_factory(em),
            world_factory=_world_factory_factory(WorldContext()),
            rng=random.Random(0),
            metrics=metrics,
        )
        result = runner.run(aki, wip, peers=[bo])
        assert not result.aborted

        # The agent's last seen world is the turn-3 world. Its
        # scene_context should be the two prior turns, with Aki's own
        # turn-1 text present verbatim.
        ctx = aki.last_world
        assert ctx is not None
        assert len(ctx.scene_context) == 2
        authors = [t.author for t in ctx.scene_context]
        assert authors == ["Aki", "Bo"]
        assert "TURN ONE fragment marker" in ctx.scene_context[0].text
    finally:
        em.close()
        metrics.close()


def test_replay_determinism_via_scene_open_payload(tmp_path: Path) -> None:
    """The scene.open payload carries the author rotation; downstream
    code must read it from the log rather than re-computing from the
    scheduler. We verify by reading the events back as a consumer
    would and confirming author identities are stable."""
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:
        wip = CONFIGURED_WIPS[0]
        aki = _FakeAgent(
            "Aki",
            plan=[
                _contrib_action(wip, "Aki one."),
                _contrib_action(wip, "Aki three."),
            ],
        )
        bo = _FakeAgent("Bo", plan=[_contrib_action(wip, "Bo two.")])
        runner = SceneRunner(
            episodic=em,
            commit_action=_commit_action_factory(em),
            world_factory=_world_factory_factory(WorldContext()),
            rng=random.Random(0),
            metrics=metrics,
        )
        runner.run(aki, wip, peers=[bo])

        events = list(reversed(em.last(10)))
        open_evt = next(e for e in events if e.action == "scene.open")
        payload = open_evt.payload
        assert payload["turn1_author"] == "Aki"
        assert payload["turn2_author"] == "Bo"
        assert payload["turn3_author"] == "Aki"  # fallback to initiator
        assert payload["wip_name"] == wip
        # The 3 contributes share scene_id with the open event.
        contributes = [e for e in events if e.action == "contribute"]
        assert all(c.payload["scene_id"] == payload["scene_id"] for c in contributes)
    finally:
        em.close()
        metrics.close()


def test_scene_open_logged_before_first_think(tmp_path: Path) -> None:
    """ADR 0006 ordering invariant: ``scene.open`` MUST be durably
    written to episodic BEFORE the first ``think()`` runs. Replay sees
    the author rotation as authoritative; if think() raced ahead and
    crashed before the open lands, the replay author list would be
    derived from the (mutable) scheduler instead, breaking determinism.
    """
    em = EpisodicMemory(tmp_path / "episodic.sqlite")
    metrics = Metrics(tmp_path / "metrics.sqlite")
    try:
        wip = CONFIGURED_WIPS[0]

        class _InspectAgent(_FakeAgent):
            def __init__(self, name: str, episodic_ref: EpisodicMemory) -> None:
                super().__init__(
                    name,
                    plan=[_contrib_action(wip, f"{name} turn fragment for ordering test.")],
                )
                self._episodic = episodic_ref
                self.saw_open_before_first_think: bool | None = None

            def think(self, world: WorldContext) -> Action:
                # Capture state on the FIRST think() only — later turns
                # would trivially see scene.open and mask a real
                # ordering bug where the open is written between turn 1
                # and turn 2.
                if self.saw_open_before_first_think is None:
                    events = self._episodic.last(20)
                    actions = [e.action for e in events]
                    self.saw_open_before_first_think = "scene.open" in actions
                return super().think(world)

        aki = _InspectAgent("Aki", em)
        bo = _FakeAgent("Bo", plan=[_contrib_action(wip, "Bo turn 2.")])
        runner = SceneRunner(
            episodic=em,
            commit_action=_commit_action_factory(em),
            world_factory=_world_factory_factory(WorldContext()),
            rng=random.Random(0),
            metrics=metrics,
        )
        # Solo run so Aki is the only think()-er we need to inspect on
        # turn 1; the carve-out path runs turn 3 as Aki too.
        runner.run(aki, wip, peers=[bo])

        assert aki.saw_open_before_first_think is True, (
            "scene.open must be durable in episodic BEFORE first think() runs "
            "(ADR 0006 ordering invariant)."
        )
    finally:
        em.close()
        metrics.close()


def test_pick_authors_falls_back_when_only_one_peer() -> None:
    """A→B→A when only one distinct peer exists."""
    out = pick_authors("Aki", ["Bo"], rng=random.Random(0))
    assert out == ("Aki", "Bo", "Aki")


def test_pick_authors_three_distinct_when_available() -> None:
    out = pick_authors("Aki", ["Bo", "Cy"], rng=random.Random(0))
    assert set(out) == {"Aki", "Bo", "Cy"}
    assert out[0] == "Aki"


def test_pick_authors_solo_initiator_returns_three_copies() -> None:
    """Degenerate fallback for the no-peer edge case."""
    out = pick_authors("Aki", [], rng=random.Random(0))
    assert out == ("Aki", "Aki", "Aki")
