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

import pytest

from microverse.agents.base import PeerSpeech, WorldContext


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
