"""Layer-G structural fix: cut autobiographical thought feedback.

Six prior layers (PRs #17-22) each bounded one expression of an
introspective trap (rest streaks, empty crafts, ...) while leaving
the generative edge intact. The edge is `_format_episodic` rendering
the agent's own ``thought`` verbatim into ``recent_episodic``, which
flows into the next prompt and lets the LLM continue its own
narrative. These tests pin the new contract:

  * `recent_episodic` carries factual surface only (action + target +
    artifact-excerpt for craft + ``[world]`` / ``[harvest]`` tags for
    exogenous events).
  * The ``thought`` field is still emitted by the LLM, persisted into
    episodic for audit, and consumed by current-tick logic — but it
    is NEVER surfaced into the *next* prompt's context.
  * The rest-run suppression of Layers C/D/E.1 generalises to ANY
    consecutive same-actor same-action run (`_compress_action_runs`).
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.base import WorldContext
from microverse.memory import build_context, est_tokens
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory


def test_format_episodic_drops_thought_for_craft(tmp_path: Path) -> None:
    """A craft event with a thought renders the artifact excerpt — not
    the thought — into recent_episodic."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="Aki",
            action="craft",
            target=None,
            payload={
                "thought": "every fiber demands silence",
                "artifact": "a small wooden box with a clear resin lid",
            },
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    joined = "\n".join(out.recent_episodic)
    assert "fiber demands silence" not in joined, (
        f"thought must NOT appear in recent_episodic, got {out.recent_episodic!r}"
    )
    assert "wooden box" in joined, (
        f"artifact excerpt must appear in recent_episodic, got {out.recent_episodic!r}"
    )
    # Specifically, the line is "Aki crafted: <excerpt>".
    assert any(line.startswith("Aki crafted:") for line in out.recent_episodic), (
        f"expected an 'Aki crafted:' line, got {out.recent_episodic!r}"
    )


def test_format_episodic_caps_artifact_excerpt(tmp_path: Path) -> None:
    """A long artifact text gets bounded so a single event cannot
    blow the episodic budget on its own."""
    long_artifact = "x" * 800
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="Aki",
            action="craft",
            target=None,
            payload={"thought": "long work", "artifact": long_artifact},
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    [line] = [line for line in out.recent_episodic if line.startswith("Aki crafted:")]
    # Cap at 120 chars + ellipsis is enforced; line length stays well
    # under the original artifact length.
    assert len(line) < 200, f"artifact line must be bounded, got len={len(line)}: {line!r}"
    assert line.endswith("…"), f"truncation must be marked with ellipsis, got {line!r}"


def test_format_episodic_speak_with_target(tmp_path: Path) -> None:
    """A speak event with a target renders as 'Aki spoke to Bo' —
    no thought, no quoted utterance."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="Aki",
            action="speak",
            target="Bo",
            payload={"thought": "I will greet them warmly"},
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    joined = "\n".join(out.recent_episodic)
    assert "greet them warmly" not in joined
    assert "Aki spoke to Bo" in out.recent_episodic, (
        f"expected 'Aki spoke to Bo' line, got {out.recent_episodic!r}"
    )


def test_format_episodic_speak_without_target(tmp_path: Path) -> None:
    """A speak event with no target renders as 'Aki spoke aloud'."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="Aki",
            action="speak",
            target=None,
            payload={"thought": "musing on the season"},
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    assert "Aki spoke aloud" in out.recent_episodic
    assert "musing on the season" not in "\n".join(out.recent_episodic)


def test_format_episodic_world_event_tagged(tmp_path: Path) -> None:
    """World events render as '[world] weather.storm' — distinguishable
    from agent actions by the bracket tag."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(actor="world", action="weather.storm", target=None, payload={})
        ep.append(
            actor="Aki",
            action="craft",
            target=None,
            payload={"thought": "the rain helps me focus", "artifact": "a small bowl"},
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    assert "[world] weather.storm" in out.recent_episodic, (
        f"expected '[world] weather.storm', got {out.recent_episodic!r}"
    )


def test_format_episodic_study_and_travel_bare(tmp_path: Path) -> None:
    """Non-craft, non-speak agent actions render as '{actor} {past_tense}',
    matching the verb tense used by `_compress_action_runs` so single
    events and compressed runs read in a consistent voice.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(actor="Aki", action="study", target=None, payload={"thought": "deep focus"})
        ep.append(actor="Aki", action="travel", target=None, payload={"thought": "to the river"})
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    joined = "\n".join(out.recent_episodic)
    assert "deep focus" not in joined
    assert "to the river" not in joined
    assert "Aki studied" in out.recent_episodic, f"got {out.recent_episodic!r}"
    assert "Aki traveled" in out.recent_episodic, f"got {out.recent_episodic!r}"


def test_compress_action_runs_collapses_speak_streak(tmp_path: Path) -> None:
    """A run of >=2 consecutive same-actor speaks compresses to a
    count-only summary, mirroring Layer C/D/E.1 for rest. This denies
    the LLM a same-action streak as another expression channel."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for i in range(5):
            ep.append(
                actor="Aki",
                action="speak",
                target="Bo",
                payload={"thought": f"speak number {i}"},
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    # 5 < 10 (the suppress threshold) — still summarised, not dropped.
    summary_lines = [line for line in out.recent_episodic if "spoke" in line and "5 times" in line]
    assert summary_lines, f"expected a 'spoke ... 5 times' summary, got {out.recent_episodic!r}"
    # Per-event thoughts must NOT survive into the slice.
    joined = "\n".join(out.recent_episodic)
    assert "speak number" not in joined, f"per-event thoughts leaked into slice, got {joined!r}"


def test_compress_action_runs_collapses_study_streak(tmp_path: Path) -> None:
    """The same compression applies to study runs, which were the
    fallback mode in Layer F's silent-craftsperson route-around."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for i in range(4):
            ep.append(
                actor="Aki",
                action="study",
                target=None,
                payload={"thought": f"study {i}"},
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    summary_lines = [
        line for line in out.recent_episodic if "studied" in line and "4 times" in line
    ]
    assert summary_lines, f"expected a 'studied ... 4 times' summary, got {out.recent_episodic!r}"


def test_compress_action_runs_suppresses_above_threshold(tmp_path: Path) -> None:
    """A speak streak >= the suppress threshold (10) drops entirely —
    extending Layer E.1 to non-rest actions. The count alone would be
    a same-shape signal feeding the next-tick prompt."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for i in range(15):
            ep.append(
                actor="Aki",
                action="speak",
                target="Bo",
                payload={"thought": f"speak {i}"},
            )
        # An anchoring craft so we can verify the slice is non-empty.
        ep.append(
            actor="Aki",
            action="craft",
            target=None,
            payload={"thought": "anchor", "artifact": "a wooden anchor"},
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    speak_lines = [line for line in out.recent_episodic if "spoke" in line]
    assert speak_lines == [], f"speak run >= 10 must be suppressed entirely, got {speak_lines!r}"
    craft_lines = [line for line in out.recent_episodic if line.startswith("Aki crafted:")]
    assert craft_lines, "anchor craft must still surface"


def test_compress_action_runs_preserves_rest_behavior(tmp_path: Path) -> None:
    """Layer E.1's suppress-above-threshold for rest stays exactly as
    it was — the generalisation must not regress existing behavior.
    A run of 80 rests still emits no summary line and no bare rest."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="Aki",
            action="craft",
            target=None,
            payload={"thought": "before", "artifact": "a cedar bowl"},
        )
        for _ in range(80):
            ep.append(
                actor="Aki",
                action="rest",
                target=None,
                payload={"thought": "the mandate for rest is absolute"},
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    rest_summaries = [line for line in out.recent_episodic if "rested" in line]
    assert rest_summaries == [], (
        f"80-rest run must be suppressed entirely (Layer E.1), got {rest_summaries!r}"
    )
    bare_rest = [line for line in out.recent_episodic if line.startswith("Aki rest")]
    assert bare_rest == [], f"no bare 'Aki rest' lines either — full suppression, got {bare_rest!r}"
    joined = "\n".join(out.recent_episodic)
    assert "mandate for rest" not in joined, "trap thought must not survive"


def test_isolated_rest_renders_without_thought(tmp_path: Path) -> None:
    """A single isolated rest still appears in the slice (it's real
    recent context), but the thought is dropped — consistent with the
    no-thought-feedback contract."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="Aki",
            action="craft",
            target=None,
            payload={"thought": "shaped a bowl", "artifact": "a small bowl"},
        )
        ep.append(
            actor="Aki",
            action="rest",
            target=None,
            payload={"thought": "a brief pause"},
        )
        ep.append(
            actor="Aki",
            action="craft",
            target=None,
            payload={"thought": "shaped another", "artifact": "a wide bowl"},
        )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    assert "a brief pause" not in "\n".join(out.recent_episodic)
    bare_rest = [line for line in out.recent_episodic if line == "Aki rested"]
    assert len(bare_rest) == 1, (
        f"isolated rest must render as bare 'Aki rested', got {out.recent_episodic!r}"
    )


def test_build_context_recent_episodic_has_zero_thought_substrings(tmp_path: Path) -> None:
    """Sanity check across a realistic mixed history: none of the
    distinctive thought tokens must survive into recent_episodic."""
    distinctive_thoughts = [
        "every fiber demands silence",
        "the wood's song at its zenith",
        "speech is a distraction",
        "I will greet them warmly",
        "the mandate for rest",
    ]
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for i, t in enumerate(distinctive_thoughts):
            ep.append(
                actor="Aki",
                action="craft",
                target=None,
                payload={"thought": t, "artifact": f"artifact-{i}"},
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    joined = "\n".join(out.recent_episodic)
    for t in distinctive_thoughts:
        assert t not in joined, f"thought {t!r} leaked into recent_episodic: {joined!r}"


def test_harvest_rated_runs_do_not_collapse(tmp_path: Path) -> None:
    """A flush ranks N candidates and emits N consecutive harvest 'rated'
    events — same actor, same action, but each carries a distinct
    payload (score, accepted, actor, kind). The compressor must NOT
    collapse them into a count-only summary or suppress them entirely:
    the per-event signal IS the value of the Alt-B feedback. Without
    this guard, ``_compress_action_runs`` flattens 2-9 ratings to
    "harvest rated N times" (losing scores) and ≥10 ratings vanish
    entirely — nullifying Alt-B in practice.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for i, accepted in enumerate([True, False, True]):
            ep.append(
                actor="harvest",
                action="rated",
                target=None,
                payload={
                    "actor": "Aki",
                    "kind": "craft",
                    "score": 0.1 * (i + 1),
                    "accepted": accepted,
                },
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    rated_lines = [line for line in out.recent_episodic if line.startswith("[harvest]")]
    assert len(rated_lines) == 3, (
        f"each harvest 'rated' event must surface individually, got {out.recent_episodic!r}"
    )
    joined = "\n".join(out.recent_episodic)
    assert "rated 3 times" not in joined, (
        f"harvest events must never collapse to a count summary, got {joined!r}"
    )
    assert "Aki's craft 0.10 (accepted)" in joined
    assert "Aki's craft 0.20 (rejected)" in joined
    assert "Aki's craft 0.30 (accepted)" in joined


def test_harvest_rated_runs_above_threshold_still_render(tmp_path: Path) -> None:
    """Even with a flush of >= REST_SUMMARY_SUPPRESS_AT consecutive
    rated events, every individual rating must still surface — the
    suppress-above-threshold rule for repetitive agent actions does
    not apply to exogenous harvest feedback.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for i in range(12):
            ep.append(
                actor="harvest",
                action="rated",
                target=None,
                payload={
                    "actor": "Aki",
                    "kind": "craft",
                    "score": 0.5,
                    "accepted": i % 2 == 0,
                },
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    rated_lines = [line for line in out.recent_episodic if line.startswith("[harvest]")]
    assert len(rated_lines) == 12, (
        f"all 12 harvest events must render, got {len(rated_lines)}: {out.recent_episodic!r}"
    )


def test_harvest_event_does_not_break_adjacent_action_runs(tmp_path: Path) -> None:
    """A harvest 'rated' event between two same-actor same-action events
    must not unintentionally extend or suppress agent action runs
    around it — the harvest event flushes its surrounding context
    cleanly, leaving prior and subsequent agent runs intact.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for i in range(2):
            ep.append(
                actor="Aki",
                action="study",
                target=None,
                payload={"thought": f"study {i}"},
            )
        ep.append(
            actor="harvest",
            action="rated",
            target=None,
            payload={"actor": "Aki", "kind": "craft", "score": 0.7, "accepted": True},
        )
        for i in range(3):
            ep.append(
                actor="Aki",
                action="study",
                target=None,
                payload={"thought": f"study after {i}"},
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    harvest_lines = [line for line in out.recent_episodic if line.startswith("[harvest]")]
    assert len(harvest_lines) == 1, f"harvest event must render once, got {out.recent_episodic!r}"
    summary_lines = [line for line in out.recent_episodic if "studied" in line]
    assert sorted(summary_lines) == ["Aki studied 2 times", "Aki studied 3 times"], (
        "the harvest event must split the study events into two distinct runs "
        "(2 before, 3 after) — a single merged 'Aki studied 5 times' would mean "
        f"the run-state was not flushed at the harvest boundary; got {out.recent_episodic!r}"
    )


def test_episodic_budget_still_capped(tmp_path: Path) -> None:
    """R1 changes the line shape but must not regress the token cap."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for i in range(200):
            ep.append(
                actor="Aki",
                action="craft",
                target=None,
                payload={"thought": "x" * 100, "artifact": f"artifact number {i} " * 20},
            )
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
            episodic_tok=1500,
        )

    assert est_tokens("\n".join(out.recent_episodic)) <= 1500
