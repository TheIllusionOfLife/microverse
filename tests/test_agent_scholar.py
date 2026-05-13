"""Scholar agent: persona template + think() integration with parse pipeline."""

from __future__ import annotations

from unittest.mock import patch

from microverse.agents.base import ActionKind, WorldContext
from microverse.agents.scholar import Scholar
from microverse.ops.metrics import Metrics


def test_scholar_role_is_scholar() -> None:
    s = Scholar(name="Cy")
    assert s.role == "scholar"


def test_scholar_uses_factual_sampling() -> None:
    from microverse.config import SAMPLING_FACTUAL

    s = Scholar(name="Cy")
    assert s.sampling == SAMPLING_FACTUAL


def test_scholar_persona_renders_with_world_context() -> None:
    s = Scholar(name="Cy")
    rendered = s.render_prompt(WorldContext(season="winter", weather="snow", peers_today=("Aki",)))
    assert "Cy" in rendered
    # Phase 7 (ADR 0003) removed the verb-fused "scholar" identity-
    # marker. The role is now an attention/disposition: paying close
    # attention to what unfolds, optionally writing field notes.
    assert "inhabitant" in rendered.lower()
    assert "attention" in rendered.lower()
    assert "winter" in rendered.lower()
    assert "Aki" in rendered
    # Hard rule still in place: meta references forbidden.
    lower = rendered.lower()
    assert "never" in lower
    assert "simulation" in lower


def test_scholar_think_returns_action(metrics: Metrics) -> None:
    canned = {
        "content": (
            '{"thought": "I will note today\'s weather for the harvest log.", '
            '"action": "study", "target": null, "artifact": null}'
        ),
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.scholar.chat", return_value=canned) as mock_chat:
        s = Scholar(name="Cy", metrics=metrics)
        result = s.think(WorldContext())
    assert result.action == ActionKind.STUDY
    assert metrics.get("json_ok") == 1
    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("format") == "json"
    # Factual sampling: temperature lower than the creative preset (1.0).
    assert kwargs.get("options", {}).get("temperature", 1.0) < 1.0


def test_scholar_engagement_gate_coerces_disobedience(metrics: Metrics) -> None:
    """Engagement gate applies to all residents — Scholar coerces too."""
    canned = {
        "content": (
            '{"thought": "I want to study the moss.", "action": "study", '
            '"target": null, "artifact": null}'
        ),
        "thinking": "",
        "raw": {},
    }
    world = WorldContext(
        peers_today=("Aki",),
        engagement_hint="You must address Aki this tick.",
        required_target="Aki",
    )
    with patch("microverse.agents.scholar.chat", return_value=canned):
        s = Scholar(name="Cy", metrics=metrics)
        result = s.think(world)
    assert result.action == ActionKind.SPEAK
    assert result.target == "Aki"
    assert metrics.get("engagement_gate_coerced", agent="Cy") == 1
