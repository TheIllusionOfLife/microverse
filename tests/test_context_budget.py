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


def test_build_context_compresses_consecutive_rest_runs(tmp_path: Path) -> None:
    """The 24h soak (#29) saw Aki collapse into a 451-event rest streak
    that poisoned ``recent_episodic`` with a wall of identical rest
    narratives. PR #17's prompt nudge alone could not break this; the
    fix is at the memory layer.

    A run of >=2 consecutive same-actor rests must collapse to a single
    summary line carrying ONLY the count, no thought text. PR #19
    initially preserved the latest rest's thought, but soak-24h-3 hour-1
    showed Aki keeps constructing a coherent ``exhausted artisan`` self-
    narrative when the latest fatigue thought is fed forward — even one
    summary line is enough seed for the loop. Layer D drops the thought.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(
            actor="Aki",
            action="craft",
            target=None,
            payload={"thought": "I shaped the cedar.", "artifact": "cedar bowl"},
        )
        for _ in range(80):
            ep.append(
                actor="Aki",
                action="rest",
                target=None,
                payload={"thought": "the mandate for rest is absolute"},
            )
        out = build_context(
            world_base=WorldContext(),
            episodic=ep,
            semantic=se,
            topic="",
            episodic_tok=1500,
        )

    joined = "\n".join(out.recent_episodic)

    summary_lines = [line for line in out.recent_episodic if line.startswith("Aki rested ")]
    assert len(summary_lines) == 1, f"expected one rest summary, got {summary_lines!r}"
    assert summary_lines[0] == "Aki rested 80 times", (
        f"summary must be count-only after Layer D, got {summary_lines[0]!r}"
    )

    bare_rest = [line for line in out.recent_episodic if line.startswith("Aki rest:")]
    assert len(bare_rest) <= 1, f"expected <=1 bare 'Aki rest:' line, got {bare_rest!r}"

    craft_lines = [line for line in out.recent_episodic if line.startswith("Aki craft")]
    assert craft_lines, "the older craft event must still surface"

    mandate_repeats = joined.count("mandate for rest")
    assert mandate_repeats == 0, (
        f"trap language must NOT survive into compressed slice, got {mandate_repeats}x"
    )

    assert est_tokens(joined) <= 1500


def test_build_context_keeps_isolated_rest_uncompressed(tmp_path: Path) -> None:
    """A single isolated rest must not be summarised; only runs of
    >=2 consecutive rests get collapsed."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        ep.append(actor="Aki", action="craft", target=None, payload={"thought": "shaped a bowl"})
        ep.append(actor="Aki", action="rest", target=None, payload={"thought": "a brief pause"})
        ep.append(actor="Aki", action="craft", target=None, payload={"thought": "shaped another"})
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    summary_lines = [line for line in out.recent_episodic if line.startswith("Aki rested ")]
    assert summary_lines == [], f"isolated rest must not be summarised, got {summary_lines!r}"

    bare_rest = [line for line in out.recent_episodic if line.startswith("Aki rest:")]
    assert len(bare_rest) == 1, f"isolated rest must appear verbatim, got {bare_rest!r}"


def test_build_context_compression_run_broken_by_other_actor(tmp_path: Path) -> None:
    """A non-Aki event (e.g. world weather) breaks a rest run, producing
    two summaries flanking the interloper rather than one merged span."""
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for _ in range(5):
            ep.append(actor="Aki", action="rest", target=None, payload={"thought": "tired"})
        ep.append(actor="world", action="weather.drought", target=None, payload={"thought": ""})
        for _ in range(7):
            ep.append(actor="Aki", action="rest", target=None, payload={"thought": "still tired"})
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    summary_lines = [line for line in out.recent_episodic if line.startswith("Aki rested ")]
    assert len(summary_lines) == 2, (
        f"expected two summaries flanking weather, got {summary_lines!r}"
    )
    counts = sorted(int(line.split()[2]) for line in summary_lines)
    assert counts == [5, 7], f"counts must match the two runs, got {counts}"


def test_build_context_rest_summary_omits_thoughts(tmp_path: Path) -> None:
    """Layer D inversion of the earlier "captures latest thought" test.

    Hour-1 of the post-Layer-C 24h soak (seed 38) showed the LLM
    constructing a coherent "exhausted artisan" narrative even when
    100+ rests were collapsed to one summary line: the ``Latest:``
    thought in that summary kept seeding the next tick's reasoning
    with fatigue language. The fix is to drop the thought entirely
    from compressed summaries — the count alone signals magnitude.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for marker in ("first", "second", "third"):
            ep.append(
                actor="Aki",
                action="rest",
                target=None,
                payload={"thought": f"the {marker} rest"},
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    summary_lines = [line for line in out.recent_episodic if line.startswith("Aki rested ")]
    assert len(summary_lines) == 1, f"expected one summary, got {summary_lines!r}"
    summary = summary_lines[0]
    assert summary == "Aki rested 3 times", (
        f"summary must be count-only, got {summary!r}"
    )
    assert "Latest" not in summary
    for marker in ("first", "second", "third"):
        assert f"the {marker} rest" not in summary


def test_build_context_compression_separates_runs_by_actor(tmp_path: Path) -> None:
    """Watchdog can spawn a Stranger mid-run, so a real production
    scenario is `Aki rest * 3 / Stranger rest * 2 / Aki rest * 4`.
    The actor change must break the run, producing three separate
    summaries (or, when the middle run has length 1, leaving it
    verbatim) rather than collapsing into one merged span keyed off
    'rest action'.
    """
    with EpisodicMemory(tmp_path / "ep.sqlite") as ep, SemanticMemory(tmp_path / "se.sqlite") as se:
        for _ in range(3):
            ep.append(actor="Aki", action="rest", target=None, payload={"thought": "Aki tired"})
        for _ in range(2):
            ep.append(actor="Mira", action="rest", target=None, payload={"thought": "Mira tired"})
        for _ in range(4):
            ep.append(
                actor="Aki",
                action="rest",
                target=None,
                payload={"thought": "Aki still tired"},
            )
        out = build_context(world_base=WorldContext(), episodic=ep, semantic=se, topic="")

    aki_summaries = [line for line in out.recent_episodic if line.startswith("Aki rested ")]
    mira_summaries = [line for line in out.recent_episodic if line.startswith("Mira rested ")]
    assert len(aki_summaries) == 2, f"expected two Aki summaries, got {aki_summaries!r}"
    assert len(mira_summaries) == 1, f"expected one Mira summary, got {mira_summaries!r}"

    aki_counts = sorted(int(line.split()[2]) for line in aki_summaries)
    assert aki_counts == [3, 4], f"Aki counts must match its two runs, got {aki_counts}"
    assert "rested 2 times" in mira_summaries[0]


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
