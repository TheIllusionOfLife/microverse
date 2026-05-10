"""Context-assembly budget tests for ``microverse.memory.build_context``.

Path-3 stateless-tick contract: prompts must fit comfortably inside
``gemma4:e4b``'s window. ``build_context`` assembles working +
``peer_inbox`` + ``world_events`` + ``lore_excerpt`` under a hard
4096-token budget (``len(text) // 4`` heuristic).

Slice-2 rewrite (Codex review HIGH on slice ordering): the prior
file pinned the ``recent_episodic`` contract that Slice 5 removes
(rest-run compression, suppress-above-threshold, etc.). Those tests
are deleted here so the suite stays GREEN through Slices 2-4 and
the dead contract does not need a graveyard of skipped tests.
``recent_episodic`` is still populated by ``build_context`` until
Slice 5 lands, but is no longer the file under test.
"""

from __future__ import annotations

import random
import string
import time
from pathlib import Path

import pytest

from microverse.agents.artisan import Artisan
from microverse.agents.base import PeerSpeech, WorldContext
from microverse.memory import build_context, est_tokens
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory


def _rand_text(rng: random.Random, n: int) -> str:
    return "".join(rng.choices(string.ascii_letters + " ", k=n))


def _seed_episodic(mem: EpisodicMemory, rng: random.Random, n: int) -> None:
    base = time.time()
    for i in range(n):
        ts = base - rng.randint(0, 30 * 86400)
        mem.append(
            actor=rng.choice(("aki", "bo", "cy")),
            action=rng.choice(("speak", "craft", "rest")),
            target=None,
            payload={"thought": _rand_text(rng, rng.randint(40, 200)), "n": i},
            ts=ts,
        )


def _seed_semantic(mem: SemanticMemory, rng: random.Random, n: int) -> None:
    topics = ("forest", "river", "harvest", "stone", "wood")
    for i in range(n):
        topic = rng.choice(topics)
        body = f"{topic} {_rand_text(rng, rng.randint(60, 300))}"
        mem.index(
            doc_id=f"doc-{i}",
            text=body,
            payload={"topic": topic, "text": body[:120]},
        )


def test_build_context_returns_world_context_preserving_base_fields(
    tmp_path: Path,
) -> None:
    """``build_context`` returns a WorldContext where ``season``,
    ``weather``, ``peers_today``, ``engagement_hint``,
    ``required_target``, ``peer_inbox``, and ``world_events`` from
    ``world_base`` are preserved verbatim. Only ``lore_excerpt`` is
    sourced from ``semantic`` inside ``build_context`` itself (and,
    until Slice 5, ``recent_episodic`` from ``episodic``).
    """
    rng = random.Random(0)
    inbox = (PeerSpeech(speaker="Bo", utterance="have you seen the river?"),)
    world_events = ("[world] weather.storm",)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_episodic(ep, rng, 10)
        _seed_semantic(se, rng, 8)
        out = build_context(
            world_base=WorldContext(
                season="summer",
                weather="clear",
                peers_today=("Bo", "Cy"),
                peer_inbox=inbox,
                world_events=world_events,
                engagement_hint="You must address Bo this tick.",
                required_target="Bo",
            ),
            episodic=ep,
            semantic=se,
            topic="forest harvest",
        )
    assert out.season == "summer"
    assert out.weather == "clear"
    assert out.peers_today == ("Bo", "Cy")
    assert out.peer_inbox == inbox
    assert out.world_events == world_events
    assert out.engagement_hint == "You must address Bo this tick."
    assert out.required_target == "Bo"
    assert isinstance(out.lore_excerpt, tuple)


def test_build_context_with_empty_stores_returns_empty_lore(tmp_path: Path) -> None:
    """Cold start: no events, no lore. ``lore_excerpt`` is an empty
    tuple; the new bounded fields default to empty too because
    ``world_base`` carries no inbox / events.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="anything",
        )
    assert out.lore_excerpt == ()
    assert out.peer_inbox == ()
    assert out.world_events == ()


def test_build_context_preserves_inbox_unchanged(tmp_path: Path) -> None:
    """``build_context`` MUST NOT mutate ``peer_inbox`` or
    ``world_events`` from ``world_base``. The pure-helper builders
    (`_build_peer_inbox`, `_build_world_events`) are called from
    ``run.py``, not from ``build_context``; their output rides
    through the world_base channel.
    """
    inbox = (
        PeerSpeech(speaker="Bo", utterance="storm coming"),
        PeerSpeech(speaker="Cy", utterance="bring the lantern"),
    )
    events = ("[world] weather.storm", "[world] stranger.arrived")
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        out = build_context(
            world_base=WorldContext(peer_inbox=inbox, world_events=events),
            episodic=ep,
            semantic=se,
            topic="",
        )
    assert out.peer_inbox == inbox, "inbox round-trip must preserve order and content"
    assert out.world_events == events, "world_events round-trip must preserve order"


@pytest.mark.parametrize("seed", range(20))
def test_rendered_prompt_under_4096_tokens(tmp_path: Path, seed: int) -> None:
    """Across many random worlds, the Artisan's rendered prompt must
    stay under 4096 tokens (~16k chars at 4 chars/token). The new
    bounded fields plus lore must not blow this budget even when
    every channel is populated.
    """
    rng = random.Random(seed)
    inbox = tuple(
        PeerSpeech(speaker=f"Peer{i}", utterance=_rand_text(rng, 80))
        for i in range(rng.randint(0, 4))
    )
    events = tuple(
        f"[world] weather.{rng.choice(('storm', 'clear', 'fog', 'rain'))}"
        for _ in range(rng.randint(0, 3))
    )
    with (
        EpisodicMemory(tmp_path / f"ep-{seed}.sqlite") as ep,
        SemanticMemory(tmp_path / f"se-{seed}.sqlite") as se,
    ):
        _seed_episodic(ep, rng, rng.randint(20, 80))
        _seed_semantic(se, rng, rng.randint(5, 25))
        world = build_context(
            world_base=WorldContext(
                season="autumn",
                weather="rain",
                peer_inbox=inbox,
                world_events=events,
            ),
            episodic=ep,
            semantic=se,
            topic="harvest river",
        )
    artisan = Artisan(name="Aki")
    prompt = artisan.render_prompt(world)
    tokens = est_tokens(prompt)
    assert tokens <= 4096, f"prompt budget blown: {tokens} tokens (seed={seed})"


def test_est_tokens_is_len_div_four() -> None:
    assert est_tokens("hello world") == len("hello world") // 4
    assert est_tokens("") == 0
