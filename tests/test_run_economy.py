"""Run-loop wiring for the action-economy spike (ADR 0008).

End-to-end with mocked Ollama. Asserts: the economy is a clean no-op when
off, substitution fires during a real run once an agent drains, and the
scene-initiation gate blocks scenes when the initiator cannot afford a
contribute (preserving the ADR 0006 scene contract by never opening one).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from microverse import config
from microverse.run import run


@contextmanager
def _economy(mode: str, *, energy_max: float = 100.0):
    """Patch the import-time economy flags so run() sees the chosen mode."""
    with (
        patch.object(config, "ECONOMY_MODE", mode),
        patch.object(config, "ECONOMY_ENABLED", mode != "0"),
        patch.object(config, "_ECONOMY_SCENE_GATE", mode in ("1", "flat", "throttle")),
        patch.object(config, "_ECONOMY_SUBSTITUTE", mode in ("1", "flat", "sub")),
        patch.object(config, "ENERGY_MAX", energy_max),
    ):
        yield


def _events(data_dir: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(str(data_dir / "episodic.sqlite")) as conn:
        return list(conn.execute("SELECT actor, action FROM events ORDER BY id"))


def _payloads(data_dir: Path) -> list[dict]:
    with sqlite3.connect(str(data_dir / "episodic.sqlite")) as conn:
        rows = conn.execute("SELECT payload_json FROM events WHERE actor NOT IN ('world')")
        return [json.loads(r[0]) for r in rows if r[0]]


def _study_only() -> dict:
    return {
        "content": '{"thought": "I study the soil.", "action": "study", '
        '"target": null, "artifact": null}',
        "thinking": "",
        "raw": {},
    }


def test_economy_off_is_default_noop(tmp_path: Path):
    """With the economy off (default), no parsed_verb telemetry is written
    and the substitution metric never appears: pre-spike behavior."""
    data_dir = tmp_path / "data"
    with patch("microverse.agents.artisan.chat", return_value=_study_only()):
        run(ticks=8, seed=1, tempo=0, data_dir=data_dir, harvest_dir=tmp_path / "h", solo=True)
    assert all("parsed_verb" not in p for p in _payloads(data_dir))
    with sqlite3.connect(str(data_dir / "metrics.sqlite")) as conn:
        row = conn.execute(
            "SELECT MAX(value) FROM metrics WHERE name='economy_verb_substituted'"
        ).fetchone()
    assert row[0] is None  # metric never bumped


def test_economy_on_substitutes_when_drained(tmp_path: Path):
    """A solo Artisan that always wants study (cost 14 > regen 12) drains
    below the study cost and the lever substitutes to craft. parsed_verb
    telemetry is stamped so Gate 9 can read the chosen stream."""
    data_dir = tmp_path / "data"
    # Small pool so study (cost 14 > regen 12) drives the pool under the study
    # cost within a few ticks; diversity lever disabled so the drain is clean.
    with (
        _economy("1", energy_max=30.0),
        patch("microverse.agents.artisan.DIVERSITY_SUBSTITUTE_PROB", 0.0),
        patch("microverse.agents.artisan.chat", return_value=_study_only()),
    ):
        run(ticks=40, seed=1, tempo=0, data_dir=data_dir, harvest_dir=tmp_path / "h", solo=True)
    with sqlite3.connect(str(data_dir / "metrics.sqlite")) as conn:
        subs = conn.execute(
            "SELECT MAX(value) FROM metrics WHERE name='economy_verb_substituted'"
        ).fetchone()[0]
    assert subs is not None, "economy_verb_substituted metric never recorded"
    assert subs >= 1, "lever should fire once energy drains"
    # The executed stream now contains craft (substituted) even though the
    # model only ever chose study; parsed_verb records the model's choice.
    actions = {a for _, a in _events(data_dir)}
    assert "craft" in actions
    payloads = _payloads(data_dir)
    assert any(p.get("parsed_verb") == "study" for p in payloads)


def test_scene_gate_blocked_when_initiator_cannot_afford_contribute(tmp_path: Path):
    """With a tiny energy pool the initiator cannot afford a contribute, so
    no scene is opened (no scene.open event) even at SCENE_GATE_P=1. The
    scene contract is preserved by never opening a partial scene."""
    data_dir = tmp_path / "data"
    with (
        _economy("throttle", energy_max=5.0),  # < every role's contribute cost
        patch.object(config, "SCENE_GATE_P", 1.0),
        patch("microverse.agents.artisan.chat", return_value=_study_only()),
        patch("microverse.agents.scholar.chat", return_value=_study_only()),
    ):
        run(ticks=12, seed=3, tempo=0, data_dir=data_dir, harvest_dir=tmp_path / "h")
    actions = [a for _, a in _events(data_dir)]
    assert "scene.open" not in actions


def test_scene_fires_when_affordable(tmp_path: Path):
    """Same forced scene roll, but ample energy: scenes DO open (scene.open
    present), confirming the gate only blocks on the energy precondition."""
    data_dir = tmp_path / "data"
    contribute = {
        "content": '{"thought": "I add a line to the scroll.", "action": "contribute", '
        '"contribute_to": "village_scroll", '
        '"artifact": "' + ("a steady line of careful work " * 6).strip() + '"}',
        "thinking": "",
        "raw": {},
    }
    with (
        _economy("throttle", energy_max=100.0),
        patch.object(config, "SCENE_GATE_P", 1.0),
        patch("microverse.agents.artisan.chat", return_value=contribute),
        patch("microverse.agents.scholar.chat", return_value=contribute),
    ):
        run(ticks=12, seed=3, tempo=0, data_dir=data_dir, harvest_dir=tmp_path / "h")
    actions = [a for _, a in _events(data_dir)]
    assert "scene.open" in actions
