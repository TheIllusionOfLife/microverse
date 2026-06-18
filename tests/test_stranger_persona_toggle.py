"""MICROVERSE_STRANGER_PERSONA toggle: the stronger-travel Stranger variant.

ADR 0017 (R3 strong-specializer sweep). The toggle selects a travel-leaning
persona for the Stranger so its ``travel`` specialty is obeyed like the
Scholar's ``study`` — without changing default behavior (Watchdog rehab
immigrants, the frozen R2 condition) when unset. Mirrors the
``MICROVERSE_BAL_CONTRIBUTE`` import-time parse+validate pattern in config.
"""

from __future__ import annotations

import pytest

from microverse import config
from microverse.agents.stranger import Stranger


def test_parse_stranger_persona_defaults_when_unset() -> None:
    assert config._parse_stranger_persona(None) == "default"
    assert config._parse_stranger_persona("") == "default"
    assert config._parse_stranger_persona("  ") == "default"


def test_parse_stranger_persona_accepts_travel() -> None:
    assert config._parse_stranger_persona("travel") == "travel"
    # Surrounding whitespace is tolerated like the economy knobs.
    assert config._parse_stranger_persona("  travel ") == "travel"


def test_parse_stranger_persona_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="MICROVERSE_STRANGER_PERSONA"):
        config._parse_stranger_persona("bogus")


def test_stranger_persona_template_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "STRANGER_PERSONA", "default")
    assert Stranger(name="Mira").persona_template == "persona_stranger.j2"


def test_stranger_persona_template_travel_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "STRANGER_PERSONA", "travel")
    assert Stranger(name="Mira").persona_template == "persona_stranger_travel.j2"
