"""Phase 4 — per-receiver workshop_view in build_context.

ADR 0003 Decision 1 (load-bearing per Codex): the WorldContext's
``workshop_view`` is rendered *per-receiver*. The receiver's own
prior fragment texts and own contributor name are redacted to
anonymous markers; non-receiver contributors are named verbatim and
their fragments rendered verbatim. This preserves Path-3's
structural no-self-history guarantee for the new channel.

Three slices:
  * 4.1 — _build_workshop_view correctness on synthetic inputs.
  * 4.2 — structural leak sweep parallel to PR #24's 897-sample sweep
    (tests/test_no_autobiography.py).
  * 4.3 — peer-fragment surface preservation (don't over-redact).
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest

from microverse.agents.base import WorldContext
from microverse.memory import _build_workshop_view, build_context
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory
from microverse.ops.metrics import Metrics
from microverse.world.workshop import (
    CONFIGURED_WIPS,
    WIPView,
    WorkshopProjection,
)


def _seed(ep: EpisodicMemory, *, actor: str, wip: str, fragment: str, ts: float) -> None:
    ep.append(
        actor=actor,
        action="contribute",
        target=wip,
        payload={"thought": f"{actor} weaving", "fragment": fragment},
        ts=ts,
    )


# ---------------------------------------------------------------------------
# Schema contract
# ---------------------------------------------------------------------------


def test_wip_view_is_frozen_dataclass() -> None:
    v = WIPView(name="x", phase="forming", contributors="A, B", excerpt="hello")
    assert dataclasses.is_dataclass(v)
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.name = "y"  # type: ignore[misc]


def test_world_context_default_workshop_view_is_empty_tuple() -> None:
    world = WorldContext()
    assert world.workshop_view == ()
    assert isinstance(world.workshop_view, tuple)


# ---------------------------------------------------------------------------
# Slice 4.1 — _build_workshop_view correctness
# ---------------------------------------------------------------------------


def test_build_workshop_view_returns_one_view_per_configured_wip(tmp_path: Path) -> None:
    """Even when a WIP has no fragments, it appears as a view so the
    prompt sees the configured set of objects. Empty WIPs render
    with an empty excerpt and empty contributors string.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        proj = WorkshopProjection(ep)
        views = _build_workshop_view(proj, agent_name="Aki", metrics=None)
    assert len(views) == len(CONFIGURED_WIPS)
    names = [v.name for v in views]
    assert set(names) == set(CONFIGURED_WIPS)
    for v in views:
        assert v.phase == "forming"
        assert v.excerpt == ""
        assert v.contributors == ""


def test_build_workshop_view_renders_other_contributors_verbatim(tmp_path: Path) -> None:
    """When Bo contributes to a WIP and Aki is reading it, Bo's
    name appears verbatim in the contributors string and Bo's
    fragment text appears verbatim in the excerpt — community
    knowledge is preserved.
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed(ep, actor="Bo", wip=target, fragment="blue stitching across the warp", ts=1.0)
        proj = WorkshopProjection(ep)
        views = _build_workshop_view(proj, agent_name="Aki", metrics=None)
    view = next(v for v in views if v.name == target)
    assert "Bo" in view.contributors
    assert "blue stitching across the warp" in view.excerpt


def test_build_workshop_view_redacts_receivers_own_fragments(tmp_path: Path) -> None:
    """When Aki contributes a fragment and Aki later reads the WIP,
    Aki's fragment text does NOT appear verbatim. The contributor
    list does not contain Aki's name verbatim either.
    """
    target = CONFIGURED_WIPS[0]
    signature_text = "signature-aki-fragment-distinctive"
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed(ep, actor="Aki", wip=target, fragment=signature_text, ts=1.0)
        proj = WorkshopProjection(ep)
        metrics = Metrics(":memory:")
        views = _build_workshop_view(proj, agent_name="Aki", metrics=metrics)
    view = next(v for v in views if v.name == target)
    assert signature_text not in view.excerpt
    # The placeholder marker fires (anonymous-contributor render).
    assert "earlier contributor" in view.excerpt.lower()
    # Aki's own name is masked.
    assert "Aki" not in view.contributors
    # Redaction metric bumps so operators can watch.
    assert metrics.get("workshop_view_self_redactions", agent="Aki") >= 1


def test_build_workshop_view_preserves_self_redaction_across_mixed_contribs(tmp_path: Path) -> None:
    """A WIP with mixed contributors: Aki's fragments are redacted
    for Aki; Bo's fragments remain. Order is preserved.
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed(ep, actor="Aki", wip=target, fragment="aki-frag-distinct-alpha", ts=1.0)
        _seed(ep, actor="Bo", wip=target, fragment="bo-frag-distinct-beta", ts=2.0)
        _seed(ep, actor="Aki", wip=target, fragment="aki-frag-distinct-gamma", ts=3.0)
        proj = WorkshopProjection(ep)
        views = _build_workshop_view(proj, agent_name="Aki", metrics=None)
    view = next(v for v in views if v.name == target)
    assert "aki-frag-distinct-alpha" not in view.excerpt
    assert "aki-frag-distinct-gamma" not in view.excerpt
    assert "bo-frag-distinct-beta" in view.excerpt
    # Bo is named verbatim.
    assert "Bo" in view.contributors


def test_build_workshop_view_name_filter_is_case_insensitive_whole_word(tmp_path: Path) -> None:
    """Aki vs Akihiko: the receiver-name filter matches only on whole
    words. A peer named ``Akihiko`` whose fragments mention ``Akihiko``
    should NOT be redacted when ``Aki`` is the receiver (the lore
    redaction in build_context follows the same rule).
    """
    target = CONFIGURED_WIPS[0]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed(ep, actor="Akihiko", wip=target, fragment="frag-by-akihiko", ts=1.0)
        proj = WorkshopProjection(ep)
        views = _build_workshop_view(proj, agent_name="Aki", metrics=None)
    view = next(v for v in views if v.name == target)
    assert "Akihiko" in view.contributors
    assert "frag-by-akihiko" in view.excerpt


# ---------------------------------------------------------------------------
# Slice 4.2 — structural leak sweep parallel to PR #24
# ---------------------------------------------------------------------------


def _world_state_dump(world: WorldContext) -> str:
    """Concatenate every text-bearing field of ``world`` — same
    pattern as ``tests/test_no_autobiography.py::_world_state_dump``,
    extended to include the new ``workshop_view`` excerpts and
    contributors fields. If any of the receiver's own fragments
    surface in any field, this dump catches it.
    """
    parts: list[str] = [
        world.season,
        world.weather,
        *world.peers_today,
    ]
    for s in world.peer_inbox:
        parts.append(s.speaker)
        parts.append(s.utterance)
    parts.extend(world.world_events)
    parts.extend(world.lore_excerpt)
    parts.append(world.engagement_hint)
    if world.required_target is not None:
        parts.append(world.required_target)
    for v in world.workshop_view:
        parts.append(v.name)
        parts.append(v.phase)
        parts.append(v.contributors)
        parts.append(v.excerpt)
    return "\n".join(parts)


def test_structural_leak_sweep_no_self_fragments(tmp_path: Path) -> None:
    """Seed 50 contribute events for Aki across the configured WIPs;
    assert zero substring matches of any of Aki's fragment texts
    when build_context is assembled for Aki.
    """
    base = time.time() - 60.0
    with (
        EpisodicMemory(tmp_path / "ep.sqlite") as ep,
        SemanticMemory(tmp_path / "se.sqlite") as se,
    ):
        for i in range(50):
            wip = CONFIGURED_WIPS[i % len(CONFIGURED_WIPS)]
            _seed(
                ep,
                actor="Aki",
                wip=wip,
                fragment=f"signature-frag-zeta-{i}-aki-unique-marker",
                ts=base + i * 0.1,
            )
        proj = WorkshopProjection(ep)
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
            receiver_name="Aki",
            workshop=proj,
        )
    dump = _world_state_dump(out)
    for i in range(50):
        marker = f"signature-frag-zeta-{i}-aki-unique-marker"
        assert marker not in dump, (
            f"self workshop fragment {marker!r} leaked into context, dump head:\n{dump[:600]!r}"
        )
    # And the agent's own name does not appear in any workshop_view
    # field (contributors / excerpt).
    for v in out.workshop_view:
        assert "Aki" not in v.contributors


def test_structural_leak_sweep_peer_fragments_pass_through(tmp_path: Path) -> None:
    """Slice 4.3: don't over-redact. When Bo contributes 50 times,
    Aki sees Bo's fragments verbatim (community knowledge) but not
    Aki's own (50 separately-seeded self fragments stay redacted).
    """
    base = time.time() - 60.0
    with (
        EpisodicMemory(tmp_path / "ep.sqlite") as ep,
        SemanticMemory(tmp_path / "se.sqlite") as se,
    ):
        for i in range(50):
            wip = CONFIGURED_WIPS[i % len(CONFIGURED_WIPS)]
            _seed(ep, actor="Aki", wip=wip, fragment=f"aki-self-{i}", ts=base + i * 0.1)
            _seed(ep, actor="Bo", wip=wip, fragment=f"bo-peer-{i}", ts=base + i * 0.1 + 0.01)
        proj = WorkshopProjection(ep)
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
            receiver_name="Aki",
            workshop=proj,
        )
    dump = _world_state_dump(out)
    # No self fragment leaks.
    for i in range(50):
        assert f"aki-self-{i}" not in dump
    # At least some peer fragments are visible (don't over-redact).
    bo_visible = sum(1 for i in range(50) if f"bo-peer-{i}" in dump)
    assert bo_visible > 0, "peer fragments should pass through community-knowledge"


def test_workshop_view_off_when_no_workshop_passed(tmp_path: Path) -> None:
    """``workshop`` is keyword-only; when not passed, build_context
    returns workshop_view=(). Existing v0.1.1 callers (no Workshop)
    keep working.
    """
    with (
        EpisodicMemory(tmp_path / "ep.sqlite") as ep,
        SemanticMemory(tmp_path / "se.sqlite") as se,
    ):
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
            receiver_name="Aki",
        )
    assert out.workshop_view == ()
