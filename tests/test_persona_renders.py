"""Slice 4: persona templates render the new bounded fields.

The Path-3 stateless tick replaces "Recent things you witnessed"
(autobiographical) with two community-facing blocks:

  * "Messages addressed to you this tick" — peer_inbox, when
    non-empty, lists ``- <speaker>: <utterance>`` lines.
  * "World events since you were last active" — world_events, when
    non-empty, lists each ``[world] <action>`` string verbatim.

Both blocks are conditional: empty fields produce no headline and
no body. The autobiographical "Recent things you witnessed" /
"What you've heard since arriving" blocks are gone — Slice 5 will
remove the field, but the templates stop reading it now.
"""

from __future__ import annotations

import pytest

from microverse.agents.base import PeerSpeech, WorldContext
from microverse.prompts import render


@pytest.mark.parametrize(
    "template",
    ["persona_artisan.j2", "persona_scholar.j2", "persona_stranger.j2"],
)
def test_persona_renders_with_empty_world_omits_inbox_and_events(template: str) -> None:
    """Cold-start view: no inbox, no world events, no autobiographical
    block. Persona must still render cleanly without any headline
    suggesting empty content (no "Messages addressed to you" header
    when peer_inbox is empty, etc.).
    """
    out = render(template, name="Aki", world=WorldContext())
    assert "Messages addressed to you" not in out, (
        f"empty peer_inbox must not produce a headline, got:\n{out}"
    )
    assert "World events since" not in out, (
        f"empty world_events must not produce a headline, got:\n{out}"
    )
    # The dead autobiographical block must NOT render — slice 4
    # contract removes it from the templates.
    assert "Recent things you witnessed" not in out, (
        f"autobiographical block must be gone in Path-3 templates, got:\n{out}"
    )
    assert "What you've heard since arriving" not in out


@pytest.mark.parametrize(
    "template",
    ["persona_artisan.j2", "persona_scholar.j2", "persona_stranger.j2"],
)
def test_persona_renders_peer_inbox_when_present(template: str) -> None:
    """A non-empty peer_inbox produces a headline + per-speaker lines
    naming the speaker and surfacing the truncated utterance.
    """
    inbox = (
        PeerSpeech(speaker="Bo", utterance="have you seen the river?"),
        PeerSpeech(speaker="Cy", utterance="the storm is coming."),
    )
    out = render(template, name="Aki", world=WorldContext(peer_inbox=inbox))
    assert "Messages addressed to you" in out, (
        f"non-empty peer_inbox must produce a headline, got:\n{out}"
    )
    assert "Bo" in out, f"speaker Bo must appear, got:\n{out}"
    assert "have you seen the river?" in out
    assert "Cy" in out
    assert "the storm is coming." in out


@pytest.mark.parametrize(
    "template",
    ["persona_artisan.j2", "persona_scholar.j2", "persona_stranger.j2"],
)
def test_persona_renders_world_events_when_present(template: str) -> None:
    """A non-empty world_events tuple produces a headline + each
    ``[world] <action>`` string verbatim.
    """
    events = (
        "[world] weather.storm",
        "[world] stranger.arrived",
    )
    out = render(template, name="Aki", world=WorldContext(world_events=events))
    assert "World events since" in out, (
        f"non-empty world_events must produce a headline, got:\n{out}"
    )
    assert "[world] weather.storm" in out
    assert "[world] stranger.arrived" in out


@pytest.mark.parametrize(
    "template",
    ["persona_artisan.j2", "persona_scholar.j2", "persona_stranger.j2"],
)
def test_persona_does_not_render_recent_episodic(template: str) -> None:
    """Even if recent_episodic is populated (it still is, until
    Slice 5 removes the field), the templates must NOT render it —
    the autobiographical channel is structurally cut at the prompt
    layer in Slice 4.
    """
    out = render(
        template,
        name="Aki",
        world=WorldContext(recent_episodic=("Aki crafted a bowl",)),
    )
    assert "Aki crafted a bowl" not in out, (
        f"recent_episodic must NOT surface in Path-3 prompts, got:\n{out}"
    )
