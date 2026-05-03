"""Context-assembly budget tests for ``microverse.memory.build_context``.

Phase 3a contract: prompts must fit comfortably inside ``gemma4:e4b``'s
window. ``build_context`` assembles working + episodic_recent +
lore_excerpt under a hard 4096-token budget (``len(text) // 4`` heuristic).
"""

from __future__ import annotations

import random
import string
import time
from pathlib import Path

import pytest

from microverse.agents.artisan import Artisan
from microverse.agents.base import WorldContext
from microverse.memory import build_context, est_tokens
from microverse.memory.episodic import EpisodicMemory
from microverse.memory.semantic import SemanticMemory


def _rand_text(rng: random.Random, n: int) -> str:
    return "".join(rng.choices(string.ascii_letters + " ", k=n))


def _seed_episodic(mem: EpisodicMemory, rng: random.Random, n: int) -> None:
    base = time.time()
    for i in range(n):
        # Spread across the last 30 days; build_context only keeps last 7.
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
        # Ensure the topic word actually appears in the indexed text so
        # FTS5 can match it; pad with random letters to vary length.
        body = f"{topic} {_rand_text(rng, rng.randint(60, 300))}"
        mem.index(
            doc_id=f"doc-{i}",
            text=body,
            payload={"topic": topic, "text": body[:120]},
        )


def test_build_context_returns_world_context_with_excerpts(tmp_path: Path):
    rng = random.Random(0)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        _seed_episodic(ep, rng, 30)
        _seed_semantic(se, rng, 12)
        out = build_context(
            world_base=WorldContext(season="summer", weather="clear"),
            episodic=ep,
            semantic=se,
            topic="forest harvest",
        )

    # Base fields preserved.
    assert out.season == "summer"
    assert out.weather == "clear"
    # New populated fields.
    assert isinstance(out.recent_episodic, tuple)
    assert isinstance(out.lore_excerpt, tuple)
    assert len(out.recent_episodic) > 0
    assert len(out.lore_excerpt) > 0


@pytest.mark.parametrize("seed", range(20))  # 20 seeds, deterministic
def test_rendered_prompt_under_4096_tokens(tmp_path: Path, seed: int):
    """Across many random worlds, the Artisan's rendered prompt must
    stay under 4096 tokens (== ~16k chars at our 4-char/token heuristic).
    """
    rng = random.Random(seed)
    with (
        EpisodicMemory(tmp_path / f"ep-{seed}.sqlite") as ep,
        SemanticMemory(tmp_path / f"se-{seed}.sqlite") as se,
    ):
        _seed_episodic(ep, rng, rng.randint(20, 80))
        _seed_semantic(se, rng, rng.randint(5, 25))

        world = build_context(
            world_base=WorldContext(season="autumn", weather="rain"),
            episodic=ep,
            semantic=se,
            topic="harvest river",
        )

    artisan = Artisan(name="Aki")
    prompt = artisan.render_prompt(world)
    tokens = est_tokens(prompt)
    assert tokens <= 4096, f"prompt budget blown: {tokens} tokens (seed={seed})"


def test_build_context_with_empty_stores_returns_empty_excerpts(tmp_path: Path):
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="anything",
        )
    assert out.recent_episodic == ()
    assert out.lore_excerpt == ()


def test_build_context_drops_episodic_older_than_7_days(tmp_path: Path):
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        now = time.time()
        ep.append(
            actor="aki",
            action="craft",
            target=None,
            payload={"thought": "yesterday I made a bowl"},
            ts=now - 86400,
        )
        ep.append(
            actor="aki",
            action="craft",
            target=None,
            payload={"thought": "ten days ago I forgot what I made"},
            ts=now - 10 * 86400,
        )
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="bowl",
        )
    joined = " ".join(out.recent_episodic)
    assert "yesterday" in joined
    assert "ten days ago" not in joined


def test_est_tokens_is_len_div_four():
    assert est_tokens("hello world") == len("hello world") // 4
    assert est_tokens("") == 0


def test_episodic_excerpts_capped_to_budget(tmp_path: Path):
    rng = random.Random(7)
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        # Pile 200 fresh events, each ~250 chars of payload thought.
        base = time.time()
        for i in range(200):
            ep.append(
                actor="aki",
                action="craft",
                target=None,
                payload={"thought": _rand_text(rng, 250), "n": i},
                ts=base - i,
            )
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="craft",
            episodic_tok=1500,
            lore_tok=600,
        )
    # Each episodic line is the formatted "actor: thought" string. The
    # cumulative len // 4 must be <= 1500.
    cumulative = est_tokens("\n".join(out.recent_episodic))
    assert cumulative <= 1500
