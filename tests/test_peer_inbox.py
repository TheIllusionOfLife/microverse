"""Path-3 slice 1: ``PeerSpeech`` + ``WorldContext.peer_inbox`` schema.

Removes the autobiographical ``recent_episodic`` channel and replaces
it with ``peer_inbox`` — most-recent-tick speaks-to-self by other
agents only. One-shot semantics (drained on next own-tick) are
enforced by the helper's caller in slice 2; this slice pins only the
schema.

The ``peer_inbox`` carries:
  * ``speaker``: the addressing peer's name.
  * ``utterance``: the speak utterance, truncated at a word boundary
    to ≤80 chars (slice 2 implements the truncation; slice 1 just
    accepts the field).

Cross-agent narrative laundering is bounded at slice 2 by dropping
any utterance containing the receiving agent's name as a whole word
(per Codex review).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from microverse.agents.base import PeerSpeech, WorldContext
from microverse.memory import _build_peer_inbox
from microverse.memory.episodic import EpisodicMemory
from microverse.ops.metrics import Metrics


def test_peer_speech_is_a_frozen_dataclass() -> None:
    """``PeerSpeech`` must be immutable so callers cannot mutate the
    inbox content after assembly. ``frozen=True`` with ``slots=True``
    matches the ``WorldContext`` discipline.
    """
    speech = PeerSpeech(speaker="Bo", utterance="hello")
    assert dataclasses.is_dataclass(speech)
    with pytest.raises(dataclasses.FrozenInstanceError):
        speech.speaker = "Cy"  # type: ignore[misc]


def test_peer_speech_requires_speaker_and_utterance() -> None:
    """Both fields are required, no defaults — the value of the inbox
    is precisely that ``who said it`` and ``what they said`` are both
    present.
    """
    with pytest.raises(TypeError):
        PeerSpeech(speaker="Bo")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        PeerSpeech(utterance="hello")  # type: ignore[call-arg]


def test_peer_speech_fields_are_strings() -> None:
    speech = PeerSpeech(speaker="Bo", utterance="have you seen the river?")
    assert speech.speaker == "Bo"
    assert speech.utterance == "have you seen the river?"


def test_world_context_default_peer_inbox_is_empty_tuple() -> None:
    """An agent with no recent peer speech must see an empty tuple,
    not ``None`` and not a missing attribute.
    """
    world = WorldContext()
    assert world.peer_inbox == ()
    assert isinstance(world.peer_inbox, tuple)


def test_world_context_round_trips_peer_inbox() -> None:
    """Constructing with explicit peer_inbox preserves the value
    exactly. Order matters for prompt rendering, so the tuple is
    iterated in insertion order.
    """
    inbox = (
        PeerSpeech(speaker="Bo", utterance="have you seen the river?"),
        PeerSpeech(speaker="Cy", utterance="the storm is coming."),
    )
    world = WorldContext(peer_inbox=inbox)
    assert world.peer_inbox == inbox
    assert world.peer_inbox[0].speaker == "Bo"
    assert world.peer_inbox[1].utterance == "the storm is coming."


def test_world_context_peer_inbox_is_immutable() -> None:
    """The dataclass is frozen, so reassignment of the field must
    raise. Bound on accidental mutation between assembly and render.
    """
    world = WorldContext()
    with pytest.raises(dataclasses.FrozenInstanceError):
        world.peer_inbox = (PeerSpeech("Bo", "hi"),)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Slice 2: ``_build_peer_inbox`` helper.
#
# Source for the utterance text is the speaker's ``payload["thought"]`` —
# the existing ``Action`` schema has no separate utterance field, so a
# speak event's only available content is the speaker's thought-when-
# speaking. This means the utterance carries the speaker's narrative
# voice, which is exactly the cross-agent-leak channel Codex flagged
# in the Layer-G review. Two structural mitigations:
#   * word-boundary truncation to ≤80 chars (slice-2 implementation
#     detail; bound on multi-paragraph manifesto seeding).
#   * drop entire PeerSpeech when the utterance contains the receiver's
#     name as a whole-word match — closes the most direct
#     ``"<receiver>, you have already <attractor>" `` laundering route.
# Both are tested below. The metric ``peer_inbox_dropped`` records how
# often the name filter fires so a soak run can observe whether the
# mitigation is load-bearing.
# ---------------------------------------------------------------------------


def _seed_speak(
    ep: EpisodicMemory,
    *,
    actor: str,
    target: str | None,
    thought: str,
    ts: float,
) -> None:
    ep.append(
        actor=actor,
        action="speak",
        target=target,
        payload={"thought": thought, "artifact": None},
        ts=ts,
    )


def test_build_peer_inbox_filters_by_target_and_since_ts(tmp_path: Path) -> None:
    """Only speaks with ``target == agent_name`` and ``ts >= since_ts``
    surface. Stale speaks (before since_ts) and speaks to other peers
    are excluded.
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(ep, actor="Bo", target="Aki", thought="hello aunt", ts=100.0)
        _seed_speak(ep, actor="Cy", target="Aki", thought="hello cousin", ts=110.0)
        _seed_speak(ep, actor="Bo", target="Cy", thought="not for aki", ts=105.0)
        _seed_speak(ep, actor="Bo", target="Aki", thought="too old", ts=50.0)
        inbox = _build_peer_inbox(ep, agent_name="Aki", since_ts=99.0, metrics=metrics)
    speakers = sorted(s.speaker for s in inbox)
    assert speakers == ["Bo", "Cy"], f"only fresh speaks-to-Aki survive, got {inbox!r}"
    utterances = " ".join(s.utterance for s in inbox)
    assert "not for aki" not in utterances, "speaks to other targets must be excluded"
    assert "too old" not in utterances, "speaks before since_ts must be excluded"


def test_build_peer_inbox_excludes_self_speaks(tmp_path: Path) -> None:
    """An agent's OWN speaks (where actor == agent_name) must never
    appear in their own inbox even if the SQL filter happens to be
    permissive. Defence-in-depth against autobiographical leak.
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(ep, actor="Aki", target="Aki", thought="self soliloquy", ts=100.0)
        _seed_speak(ep, actor="Bo", target="Aki", thought="from a peer", ts=110.0)
        inbox = _build_peer_inbox(ep, agent_name="Aki", since_ts=0.0, metrics=metrics)
    assert all(s.speaker != "Aki" for s in inbox), (
        f"self-speaks must never appear in own inbox, got {inbox!r}"
    )
    assert any(s.speaker == "Bo" for s in inbox), "peer speak must remain"


def test_build_peer_inbox_truncates_at_word_boundary(tmp_path: Path) -> None:
    """Utterances longer than 80 chars are bounded at the last word
    boundary within the 80-char ceiling so the prompt never carries
    a mid-word fragment. Codex review MEDIUM.
    """
    long_thought = "the river " * 12  # 120 chars; exceeds the 80-char ceiling
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(ep, actor="Bo", target="Aki", thought=long_thought, ts=100.0)
        inbox = _build_peer_inbox(ep, agent_name="Aki", since_ts=0.0, metrics=metrics)
    assert len(inbox) == 1
    utt = inbox[0].utterance
    assert len(utt) <= 81, f"utterance must be <= 80 chars + ellipsis, got {len(utt)}: {utt!r}"
    assert utt.endswith("…"), f"truncation must be marked with ellipsis, got {utt!r}"
    # The body before the ellipsis ends at a space-bounded word.
    body = utt[:-1].rstrip()
    assert not body.endswith("rive"), f"truncation must not leave a half-word, got body={body!r}"


def test_build_peer_inbox_drops_utterance_with_receiver_name(tmp_path: Path) -> None:
    """When a peer's utterance contains the receiving agent's name as
    a whole word, the entire PeerSpeech is dropped (not just the
    name redacted). Closes the cross-agent narrative-laundering
    channel Codex flagged in the Layer-G review. The metric records
    each drop so soak runs can see how often the filter fires.
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(
            ep,
            actor="Cy",
            target="Aki",
            thought="Aki, I remember when you finished your final bowl",
            ts=100.0,
        )
        _seed_speak(ep, actor="Bo", target="Aki", thought="have you seen the river?", ts=110.0)
        inbox = _build_peer_inbox(ep, agent_name="Aki", since_ts=0.0, metrics=metrics)
    speakers = [s.speaker for s in inbox]
    assert speakers == ["Bo"], (
        f"utterance containing receiver name must be dropped entirely, got {inbox!r}"
    )
    assert metrics.get("peer_inbox_dropped", agent="Aki") >= 1, (
        "the drop metric must record the filter firing"
    )


def test_build_peer_inbox_name_match_is_whole_word(tmp_path: Path) -> None:
    """The name-filter checks WHOLE-WORD match — a substring like
    "Akihiko" or "akin" must NOT trip the filter for receiver "Aki".
    Otherwise legitimate utterances are over-blocked.
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(
            ep,
            actor="Bo",
            target="Aki",
            thought="Akihiko built a kiln nearby",
            ts=100.0,
        )
        _seed_speak(
            ep,
            actor="Cy",
            target="Aki",
            thought="the work feels akin to weaving",
            ts=110.0,
        )
        inbox = _build_peer_inbox(ep, agent_name="Aki", since_ts=0.0, metrics=metrics)
    assert len(inbox) == 2, f"substring matches must NOT trip whole-word filter, got {inbox!r}"


def test_build_peer_inbox_returns_chronological_order(tmp_path: Path) -> None:
    """Inbox is rendered into the prompt as a list; chronological
    order (oldest-first) gives the LLM a sane reading sequence.
    """
    metrics = Metrics(":memory:")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(ep, actor="Bo", target="Aki", thought="first", ts=100.0)
        _seed_speak(ep, actor="Cy", target="Aki", thought="second", ts=110.0)
        _seed_speak(ep, actor="Bo", target="Aki", thought="third", ts=120.0)
        inbox = _build_peer_inbox(ep, agent_name="Aki", since_ts=0.0, metrics=metrics)
    assert [s.utterance for s in inbox] == ["first", "second", "third"], (
        f"inbox must be chronological (oldest-first), got {inbox!r}"
    )


def test_build_peer_inbox_metrics_optional(tmp_path: Path) -> None:
    """Helper must work without a metrics argument so unit tests and
    library-as-import contexts don't have to thread a Metrics through.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep:
        _seed_speak(ep, actor="Bo", target="Aki", thought="hi", ts=100.0)
        inbox = _build_peer_inbox(ep, agent_name="Aki", since_ts=0.0)
    assert len(inbox) == 1
