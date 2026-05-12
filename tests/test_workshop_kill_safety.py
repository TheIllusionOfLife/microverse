"""Slice 2.4 — Workshop projection kill-safety.

ADR 0003 contract: the workshop is a *derived* read-model. The
episodic SQLite event log is the durability boundary; its WAL/SIGKILL
properties are tested at the subprocess level in
``tests/test_kill_safety.py``. What we must pin here is the bridge:
the projection that an operator rebuilds after restart must match
the projection a long-lived process saw at the moment of crash, with
no autonomous state that could diverge.

The full subprocess-SIGKILL drill is unnecessary here because the
episodic file is the only durability surface — if it survived (and
it does, per the existing drill), then ``WorkshopProjection(ep)`` on
a freshly-opened ``EpisodicMemory`` is provably equivalent to the
in-memory projection that was running at crash time. We pin
*equivalence across a reopen*, which is the cheapest test of that
invariant.
"""

from __future__ import annotations

from pathlib import Path

from microverse.memory.episodic import EpisodicMemory
from microverse.world.workshop import CONFIGURED_WIPS, WorkshopProjection


def test_projection_rebuilds_identically_after_sqlite_reopen(tmp_path: Path) -> None:
    """Build a projection on a hot connection; close the connection;
    open a new one against the same SQLite file; assert the rebuilt
    projection has the same fragments / phase / contributor set per
    WIP. This is the recovery path that runs on every process
    restart.
    """
    db = tmp_path / "ep.sqlite"
    target = CONFIGURED_WIPS[0]
    expected_fragments: list[tuple[str, str]] = []

    with EpisodicMemory(db) as ep:
        proj_hot = WorkshopProjection(ep)
        for i, (actor, text) in enumerate([
            ("Aki", "rough warp"),
            ("Bo", "blue stitching"),
            ("Aki", "blossom motif"),
            ("Cy", "field-note observation"),
        ]):
            ep.append(
                actor=actor,
                action="contribute",
                target=target,
                payload={"thought": f"{actor} thinks", "fragment": text},
                ts=float(i + 1),
            )
            proj_hot.on_contribute_event(ep.last(1)[0])
            expected_fragments.append((actor, text))

        hot_wip = proj_hot.get(target)
        assert hot_wip is not None
        hot_state = (
            tuple((f.contributor, f.text) for f in hot_wip.fragments),
            hot_wip.phase,
            hot_wip.contributors(),
            hot_wip.last_activity_ts,
        )

    # Process restart: episodic connection closed; reopen fresh.
    with EpisodicMemory(db) as ep2:
        proj_cold = WorkshopProjection(ep2)
        cold_wip = proj_cold.get(target)
        assert cold_wip is not None
        cold_state = (
            tuple((f.contributor, f.text) for f in cold_wip.fragments),
            cold_wip.phase,
            cold_wip.contributors(),
            cold_wip.last_activity_ts,
        )

    assert hot_state == cold_state, (
        f"projection diverged across reopen.\n"
        f"hot:  {hot_state!r}\ncold: {cold_state!r}"
    )
    # And the fragments match what the test seeded — no autonomous state.
    assert [(c, t) for (c, t) in hot_state[0]] == expected_fragments


def test_partial_writes_do_not_corrupt_projection(tmp_path: Path) -> None:
    """A contribute event with an empty fragment payload (the
    closest legal analogue of "tick committed but agent emitted
    nothing useful") is silently dropped at projection-rebuild time,
    so a subsequent restart converges to the same state regardless
    of how many such no-op events sit in the log.
    """
    db = tmp_path / "ep.sqlite"
    target = CONFIGURED_WIPS[0]

    with EpisodicMemory(db) as ep:
        # Mix real + empty-fragment contributes; the empty ones are
        # legally appendable (no schema constraint forbids them) but
        # MUST NOT affect the projection state.
        for i in range(6):
            text = "" if i % 2 == 0 else f"frag-{i}"
            ep.append(
                actor="Aki",
                action="contribute",
                target=target,
                payload={"thought": "x", "fragment": text},
                ts=float(i + 1),
            )

    with EpisodicMemory(db) as ep1:
        proj1 = WorkshopProjection(ep1)
    with EpisodicMemory(db) as ep2:
        proj2 = WorkshopProjection(ep2)

    w1 = proj1.get(target)
    w2 = proj2.get(target)
    assert w1 is not None
    assert w2 is not None
    assert [(f.contributor, f.text) for f in w1.fragments] == [
        (f.contributor, f.text) for f in w2.fragments
    ]
    assert w1.phase == w2.phase
    # Three of six events were empty-fragment; the projection holds
    # exactly the three non-empty ones.
    assert len(w1.fragments) == 3
