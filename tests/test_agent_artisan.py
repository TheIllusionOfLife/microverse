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
    rendered = a.render_prompt(
        WorldContext(season="winter", weather="snow", peers_today=("Bo",))
    )
    # Persona must mention name, role, and current world state.
    assert "Aki" in rendered
    assert "artisan" in rendered.lower()
    assert "winter" in rendered or "Winter" in rendered
    # Hard rule: persona forbids meta-references.
    assert "simulation" not in rendered.lower() or "do not" in rendered.lower()


def test_artisan_think_returns_action():
    metrics = Metrics(":memory:")
    canned = {
        "content": (
            '{"thought": "I will craft a wooden bowl.", "action": "craft", '
            '"target": null, "artifact": "wooden bowl"}'
        ),
        "thinking": "",
        "raw": {},
    }
    with patch(
        "microverse.agents.artisan.chat", return_value=canned
    ) as mock_chat:
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(WorldContext())

    assert result.action == ActionKind.CRAFT
    assert result.artifact == "wooden bowl"
    assert metrics.get("json_ok") == 1
    # Sanity: chat was invoked with creative sampling and JSON format.
    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("format") == "json"
    assert kwargs.get("options", {}).get("temperature", 0) == 1.0


def test_artisan_think_falls_back_on_garbage_response():
    metrics = Metrics(":memory:")
    canned = {"content": "not json", "thinking": "", "raw": {}}
    with patch("microverse.agents.artisan.chat", return_value=canned):
        a = Artisan(name="Aki", metrics=metrics)
        result = a.think(WorldContext())

    assert result.action == ActionKind.REST
    assert metrics.get("json_fallback_rest") == 1
