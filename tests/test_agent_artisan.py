"""Artisan agent: persona template + think() integration with parse pipeline."""

from __future__ import annotations

from unittest.mock import patch

from microverse.agents.artisan import Artisan
from microverse.agents.base import ActionKind, WorldContext
from microverse.ops.metrics import Metrics


def test_artisan_role_is_lowercase_artisan():
    a = Artisan(name="Aki")
    assert a.role == "artisan"


def test_artisan_uses_creative_sampling():
    from microverse.config import SAMPLING_CREATIVE

    a = Artisan(name="Aki")
    assert a.sampling == SAMPLING_CREATIVE


def test_artisan_persona_renders_with_world_context():
    a = Artisan(name="Aki")
    rendered = a.render_prompt(WorldContext(season="winter", weather="snow", peers_today=("Bo",)))
    # Persona must mention name and current world state. Phase 7
    # (ADR 0003) intentionally removed the verb-fused "artisan"
    # identity-marker — the role is now an attention/disposition, not
    # a primary verb. We still assert name + world fields render.
    assert "Aki" in rendered
    assert "inhabitant" in rendered.lower()
    assert "winter" in rendered.lower()
    assert "Bo" in rendered
    # Hard rule: persona explicitly forbids meta-references. The
    # forbidding word ("never") and the meta-token ("simulation") must
    # both appear so the model is reminded *to refuse* meta-references,
    # not just told they exist.
    lower = rendered.lower()
    assert "never" in lower
    assert "simulation" in lower


def test_artisan_think_returns_action(metrics: Metrics):
    canned = {
        "content": (
            '{"thought": "I will craft a wooden bowl.", "action": "craft", '
            '"target": null, "artifact": "wooden bowl"}'
        ),
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.artisan.chat", return_value=canned) as mock_chat:
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(WorldContext())

    assert result.action == ActionKind.CRAFT
    assert result.artifact == "wooden bowl"
    assert metrics.get("json_ok") == 1
    # Sanity: chat was invoked with creative sampling and JSON format.
    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("format") == "json"
    assert kwargs.get("options", {}).get("temperature", 0) == 1.0


def test_artisan_think_falls_back_on_garbage_response(metrics: Metrics):
    canned = {"content": "not json", "thinking": "", "raw": {}}
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(WorldContext())

    assert result.action == ActionKind.REST
    assert metrics.get("json_fallback_rest") == 1


def _intentional_rest_chat(thought: str = "I will rest a moment to gather myself."):
    return {
        "content": (
            f'{{"thought": "{thought}", "action": "rest", "target": null, "artifact": null}}'
        ),
        "thinking": "",
        "raw": {},
    }


def test_artisan_preserves_streak_of_three_intentional_rests(metrics: Metrics):
    """Layer E.2 (Codex): a streak of <= 3 intentional rests must
    pass through unchanged — only the 4th-in-a-row triggers the
    rate-limiter."""
    with patch("microverse.agents.artisan.chat", return_value=_intentional_rest_chat()):
        a = Artisan(name="Aki", metrics=metrics)
        results = [a.think(WorldContext()) for _ in range(3)]

    assert all(r.action == ActionKind.REST for r in results), (
        f"first three rests must pass through, got {[r.action for r in results]!r}"
    )
    assert metrics.get("artisan_rest_rate_limited", agent="Aki") == 0


def test_artisan_rate_limits_fourth_intentional_rest(metrics: Metrics):
    """Layer E.2: a 4th intentional rest in a row gets coerced. With no
    peers, the coerced action is ``study`` (the safe non-rest fallback);
    with peers, it becomes ``speak``. Metric ``artisan_rest_rate_limited``
    bumps once per coercion."""
    with patch("microverse.agents.artisan.chat", return_value=_intentional_rest_chat()):
        a = Artisan(name="Aki", metrics=metrics)
        # Three pass through, fourth gets coerced.
        for _ in range(3):
            a.think(WorldContext())
        assert metrics.get("artisan_rest_rate_limited", agent="Aki") == 0
        coerced = a.think(WorldContext())

    assert coerced.action != ActionKind.REST, (
        f"4th consecutive rest must be coerced, got {coerced.action!r}"
    )
    assert coerced.action == ActionKind.STUDY, (
        f"with no peers, coerce target is study, got {coerced.action!r}"
    )
    assert metrics.get("artisan_rest_rate_limited", agent="Aki") == 1


def test_artisan_rate_limit_coerces_to_speak_when_peers_present(metrics: Metrics):
    """When peers are present in the world, the rate-limit coerces
    rest -> speak (with a peer as target) rather than study."""
    world = WorldContext(peers_today=("Bo",))
    with patch("microverse.agents.artisan.chat", return_value=_intentional_rest_chat()):
        a = Artisan(name="Aki", metrics=metrics)
        for _ in range(3):
            a.think(world)
        coerced = a.think(world)

    assert coerced.action == ActionKind.SPEAK, (
        f"with peers present, coerce target is speak, got {coerced.action!r}"
    )
    assert coerced.target == "Bo", f"speak target must be a peer, got {coerced.target!r}"


def test_artisan_rate_limit_resets_on_non_rest(metrics: Metrics):
    """A non-rest action breaks the streak; subsequent rests start
    counting from 0 again."""
    rest_chat = _intentional_rest_chat()
    craft_chat = {
        "content": (
            '{"thought": "I shaped a bowl.", "action": "craft", '
            '"target": null, "artifact": "wooden bowl"}'
        ),
        "thinking": "",
        "raw": {},
    }
    responses = [rest_chat, rest_chat, rest_chat, craft_chat, rest_chat, rest_chat, rest_chat]

    with patch("microverse.agents.artisan.chat", side_effect=responses):
        a = Artisan(name="Aki", metrics=metrics)
        results = [a.think(WorldContext()) for _ in responses]

    actions = [r.action for r in results]
    assert actions == [
        ActionKind.REST,
        ActionKind.REST,
        ActionKind.REST,
        ActionKind.CRAFT,
        ActionKind.REST,
        ActionKind.REST,
        ActionKind.REST,
    ], f"streak resets after craft, got {actions!r}"
    assert metrics.get("artisan_rest_rate_limited", agent="Aki") == 0


def _craft_chat(thought: str = "I will shape the wood.", artifact: str | None = None):
    """Build an Ollama-style chat response with action=craft. ``artifact``
    of None renders as JSON ``null``; non-None renders as a quoted string."""
    artifact_json = "null" if artifact is None else f'"{artifact}"'
    return {
        "content": (
            f'{{"thought": "{thought}", "action": "craft", '
            f'"target": null, "artifact": {artifact_json}}}'
        ),
        "thinking": "",
        "raw": {},
    }


def test_artisan_passes_through_craft_with_artifact(metrics: Metrics):
    """Layer F.2: a craft with a real artifact is unaffected."""
    canned = _craft_chat(artifact="a wooden bowl")
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(WorldContext())
    assert result.action == ActionKind.CRAFT
    assert result.artifact == "a wooden bowl"
    assert metrics.get("artisan_empty_craft_coerced", agent="Aki") == 0


def test_artisan_coerces_empty_craft_to_study(metrics: Metrics):
    """Layer F.2: craft + artifact=null gets coerced to study, the
    empty-craft metric bumps, and the original 'silent' thought is
    DROPPED rather than preserved (per Codex insight: feeding the
    fatigue-or-silent narrative back into recent_episodic is what
    sustained the trap)."""
    canned = _craft_chat(thought="Every fiber demands silence", artifact=None)
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(WorldContext())
    assert result.action == ActionKind.STUDY
    assert "silent" not in result.thought.lower()
    assert "demands" not in result.thought.lower()
    assert metrics.get("artisan_empty_craft_coerced", agent="Aki") == 1


def test_artisan_coerces_whitespace_craft_to_study(metrics: Metrics):
    """Pydantic ``str_strip_whitespace=True`` normalises ' ' to '' so
    the coercion fires the same way as None."""
    canned = _craft_chat(artifact="   ")
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(WorldContext())
    assert result.action == ActionKind.STUDY
    assert metrics.get("artisan_empty_craft_coerced", agent="Aki") == 1


def test_artisan_empty_craft_does_not_trigger_rest_limiter(metrics: Metrics):
    """Empty-craft is an active tick, not avoidance. Coercion must
    NOT bump artisan_rest_rate_limited and must reset the rest streak,
    so a trailing rest after coercion is the 1st in a new streak."""
    rest_chat = _intentional_rest_chat()
    empty_craft = _craft_chat(artifact=None)
    responses = [rest_chat, rest_chat, rest_chat, empty_craft, rest_chat]
    with patch("microverse.agents.artisan.chat", side_effect=responses):
        a = Artisan(name="Aki", metrics=metrics)
        for _ in responses:
            a.think(WorldContext())
    assert metrics.get("artisan_rest_rate_limited", agent="Aki") == 0
    assert metrics.get("artisan_empty_craft_coerced", agent="Aki") == 1


def test_artisan_empty_craft_uses_neutral_replacement_thought(metrics: Metrics):
    """Codex Layer F insight: the replacement thought must break the
    silent-woodworker narrative continuity rather than preserve it."""
    canned = _craft_chat(thought="Every fiber demands silence", artifact=None)
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(WorldContext())
    assert "demands silence" not in result.thought
    assert "silent" not in result.thought.lower()
    # Replacement thought references something productive the agent
    # falls back to: studying materials.
    lower = result.thought.lower()
    assert "study" in lower or "materials" in lower


def test_artisan_rate_limit_skips_empty_thought_rest(metrics: Metrics):
    """Layer E.2 trade-off: the rate-limiter classifies "intentional
    rest" as ``action == REST and bool(thought)``. A legitimate LLM rest
    with a deliberately-empty thought would slip past the limiter — in
    practice the persona prompt asks for a thought on every action so
    this case is rare. We pin the trade-off here so a future change to
    the heuristic surfaces as a deliberate test update.
    """
    canned = {
        "content": '{"thought": "", "action": "rest", "target": null, "artifact": null}',
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        results = [a.think(WorldContext()) for _ in range(5)]

    assert all(r.action == ActionKind.REST for r in results), (
        f"empty-thought rests must pass through (treated as fallback-shaped),"
        f" got {[r.action for r in results]!r}"
    )
    assert metrics.get("artisan_rest_rate_limited", agent="Aki") == 0


def test_artisan_rate_limit_skips_parse_fallback_rests(metrics: Metrics):
    """Parse-fallback rests (empty thought from a malformed LLM
    response) must NOT count toward the rate-limit. Otherwise a broken
    LLM that always emits garbage would be coerced into fake speak/study
    events that obscure the real JSON-failure signal the watchdog needs
    to pause the agent."""
    garbage = {"content": "not json", "thinking": "", "raw": {}}
    with patch("microverse.agents.artisan.chat", return_value=garbage):
        a = Artisan(name="Aki", metrics=metrics)
        results = [a.think(WorldContext()) for _ in range(5)]

    # All five are parse-fallback rests; none should be coerced.
    assert all(r.action == ActionKind.REST for r in results), (
        f"parse-fallback rests must pass through, got {[r.action for r in results]!r}"
    )
    assert metrics.get("artisan_rest_rate_limited", agent="Aki") == 0
    assert metrics.get("json_fallback_rest") == 5
