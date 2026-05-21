"""Multi-turn scenes — ADR 0005 Decision 3.

A scene is a 3-turn micro-sequence on the workshop affordance:

    turn 1  proposal   (initiator A → WIP)
    turn 2  response   (peer B reads A's turn, contributes)
    turn 3  closure    (peer C — or A again — reads both prior turns)

The scene is the unit of artifact. The Trader scores the resulting
3-fragment WIP as one entity. Peer engagement becomes the path of
least resistance because turn 2 and 3 see the prior turn's text in
their prompt as an explicit input, not via autobiographical replay
or coercive instruction.

Event contract (logged in episodic):

  - ``scene.open`` carries ``{scene_id, turn1_author, turn2_author,
    turn3_author, wip_name}``. The author list is the authoritative
    source on replay (the scheduler is mutable; logged authors are not).
  - 3 ``contribute`` events carry ``scene_id`` and ``turn_index ∈ {1,2,3}``
    in their payload (alongside the existing ``fragment`` / ``thought``
    / ``artifact`` keys). The WorkshopProjection sees these as ordinary
    contributes; scene grouping is an observation property over the log,
    not a projection invariant.
  - On parse failure or LLM exception mid-scene, ``scene.abort`` is
    written with ``{scene_id, last_turn, reason}``. Partial fragments
    that landed before the abort stay in the WIP — the harvester treats
    a partial scene as a shorter accreted artifact under its existing
    acceptance rules.

The micro-scheduler does not coerce verb choice: if a participant's
parsed action is not a contribute (or targets a different WIP), the
scene aborts. We do not paper over disobedience with a forced rewrite;
the gate-7 semantic-dependence check would catch that fakery anyway.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from microverse.agents.base import Action, ActionKind, Agent, SceneTurn, WorldContext

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SceneResult:
    """What happened during a single scene. ``aborted`` is True when
    the scene did not run to turn 3 cleanly; ``completed_turns`` is
    the number of contributes that landed (0/1/2/3)."""

    scene_id: str
    wip_name: str
    completed_turns: int
    aborted: bool
    reason: str = ""


def pick_authors(initiator: str, peers: list[str], *, rng: Any) -> tuple[str, str, str]:
    """Pick a 3-author rotation biased toward NON-INITIATOR peers.

    ADR 0005:354 closed-circuit mitigation: if a scene cannot find 3
    distinct authors, fall back to A→B→A (turn-3 author == turn-1
    author). The redaction carve-out in build_world_for_turn handles
    the same-author case correctly.
    """
    distinct_peers = [p for p in peers if p != initiator]
    if not distinct_peers:
        # Degenerate: a soloing initiator. Caller should gate against this.
        return (initiator, initiator, initiator)
    turn2 = rng.choice(distinct_peers)
    remaining = [p for p in distinct_peers if p != turn2]
    # ADR 0005:354 fallback: only one distinct peer → turn 3 closes
    # back to the initiator (A→B→A). The scene_context carve-out for
    # turn 3's redaction handles this case.
    turn3 = rng.choice(remaining) if remaining else initiator
    return (initiator, turn2, turn3)


def build_scene_context(turns: list[SceneTurn]) -> tuple[SceneTurn, ...]:
    """Order-preserving tuple of completed prior turns for the next
    persona render."""
    return tuple(turns)


class SceneRunner:
    """Runs a single 3-turn scene end-to-end.

    The runner is intentionally stateless across scenes — each call
    to :meth:`run` opens its own scene_id, runs the three turns, and
    returns. The caller (run.py) provides the lookups (``agent_by_name``,
    a ``commit_action`` callback so contribute events go through the
    same path as single-tick actions, and ``world_factory`` so each
    turn gets a fresh WorldContext with the appropriate ``scene_context``).
    """

    def __init__(
        self,
        *,
        episodic: Any,
        commit_action: Any,
        world_factory: Any,
        rng: Any,
        metrics: Any | None = None,
    ) -> None:
        self._episodic = episodic
        self._commit = commit_action
        self._world_for = world_factory
        self._rng = rng
        self._metrics = metrics

    def _bump(self, name: str, *, agent: str | None = None) -> None:
        if self._metrics is None:
            return
        if agent is None:
            self._metrics.bump(name)
        else:
            self._metrics.bump(name, agent=agent)

    def run(
        self,
        initiator: Agent,
        wip_name: str,
        peers: list[Agent],
    ) -> SceneResult:
        """Drive one 3-turn scene. Returns a SceneResult either way."""
        scene_id = uuid.uuid4().hex[:16]
        peer_names = [p.name for p in peers]
        author_names = pick_authors(initiator.name, peer_names, rng=self._rng)
        name_to_agent: dict[str, Agent] = {p.name: p for p in peers}
        name_to_agent[initiator.name] = initiator

        # 1. Emit scene.open BEFORE any think() so replay sees the
        # author list. payload.scene_id allows downstream consumers
        # (gate-7 producer, kill-drill verifier) to group turns.
        self._episodic.append(
            actor="scene",
            action="scene.open",
            target=wip_name,
            payload={
                "scene_id": scene_id,
                "turn1_author": author_names[0],
                "turn2_author": author_names[1],
                "turn3_author": author_names[2],
                "wip_name": wip_name,
            },
        )
        self._bump("scene_open")

        completed_turns: list[SceneTurn] = []

        for turn_index, author_name in enumerate(author_names, start=1):
            agent = name_to_agent.get(author_name)
            if agent is None:
                self._abort(scene_id, last_turn=turn_index - 1, reason="agent_missing")
                return SceneResult(
                    scene_id=scene_id,
                    wip_name=wip_name,
                    completed_turns=len(completed_turns),
                    aborted=True,
                    reason="agent_missing",
                )
            scene_context = build_scene_context(completed_turns)
            try:
                world: WorldContext = self._world_for(
                    agent=agent,
                    scene_context=scene_context,
                )
            except Exception as exc:
                _logger.exception("scene world_factory failed")
                self._abort(scene_id, last_turn=turn_index - 1, reason=f"world_factory: {exc}")
                return SceneResult(
                    scene_id=scene_id,
                    wip_name=wip_name,
                    completed_turns=len(completed_turns),
                    aborted=True,
                    reason="world_factory_error",
                )
            try:
                action: Action = agent.think(world)
            except Exception as exc:
                _logger.exception("scene turn %d think failed", turn_index)
                self._bump("scene_abort_think_error", agent=author_name)
                self._abort(scene_id, last_turn=turn_index - 1, reason=f"think: {exc}")
                return SceneResult(
                    scene_id=scene_id,
                    wip_name=wip_name,
                    completed_turns=len(completed_turns),
                    aborted=True,
                    reason="think_error",
                )

            # Validate that the LLM honored the scene affordance.
            wrong_action = action.action != ActionKind.CONTRIBUTE
            wrong_wip = action.contribute_to != wip_name
            no_fragment = not (action.artifact or "").strip()
            if wrong_action or wrong_wip or no_fragment:
                self._bump("scene_abort_off_topic", agent=author_name)
                self._abort(
                    scene_id,
                    last_turn=turn_index - 1,
                    reason=(
                        f"off_topic: action={action.action} "
                        f"wip={action.contribute_to!r} "
                        f"frag_empty={no_fragment}"
                    ),
                )
                return SceneResult(
                    scene_id=scene_id,
                    wip_name=wip_name,
                    completed_turns=len(completed_turns),
                    aborted=True,
                    reason="off_topic",
                )

            # Stamp scene_id / turn_index on the action so the commit
            # path embeds them into the episodic payload.
            stamped = action.model_copy(update={"scene_id": scene_id, "turn_index": turn_index})
            try:
                self._commit(agent, stamped)
            except Exception as exc:
                _logger.exception("scene commit_action failed")
                self._abort(scene_id, last_turn=turn_index - 1, reason=f"commit: {exc}")
                return SceneResult(
                    scene_id=scene_id,
                    wip_name=wip_name,
                    completed_turns=len(completed_turns),
                    aborted=True,
                    reason="commit_error",
                )

            completed_turns.append(
                SceneTurn(author=author_name, text=(stamped.artifact or "").strip())
            )
            self._bump("scene_turn_committed", agent=author_name)

        self._bump("scene_completed")
        return SceneResult(
            scene_id=scene_id,
            wip_name=wip_name,
            completed_turns=3,
            aborted=False,
        )

    def _abort(self, scene_id: str, *, last_turn: int, reason: str) -> None:
        try:
            self._episodic.append(
                actor="scene",
                action="scene.abort",
                target=None,
                payload={
                    "scene_id": scene_id,
                    "last_turn": last_turn,
                    "reason": reason,
                },
                ts=time.time(),
            )
        except Exception:
            _logger.exception("scene.abort emit failed")
        self._bump("scene_aborted")
