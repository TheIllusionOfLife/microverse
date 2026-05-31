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

import pytest

from microverse import config
from microverse.agents.artisan import Artisan
from microverse.config import VERB_COST_BY_ROLE
from microverse.run import _compute_energy_hint, _lazy_attach_energy, run
from microverse.world.economy import EnergyLedger


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
    # The model only ever chose study, but once drained the lever substitutes
    # toward a payload-free verb (rest at this small pool). It must NEVER
    # fabricate a hollow craft (review); parsed_verb records the model's choice.
    actions = {a for _, a in _events(data_dir)}
    assert "craft" not in actions
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


def test_energy_hint_only_in_substitution_modes():
    """The energy_hint (prompt-level scarcity signal) is paired with the
    substitution lever, so the scene-gate-only ``throttle`` ablation stays
    clean: no prompt pressure, only the gate."""
    led = EnergyLedger.fresh(
        ["Aki"], max_energy=100.0, regen_per_tick=12.0, cost_table=VERB_COST_BY_ROLE
    )
    led._pool["Aki"] = 0.0  # drained -> hint would fire if the mode allows it
    agent = Artisan(name="Aki")
    with _economy("throttle"):
        assert _compute_energy_hint(led, agent) == ""  # scene-gate-only: no hint
    with _economy("sub"):
        assert _compute_energy_hint(led, agent) != ""  # substitution arm: hint on


def test_flag_off_run_is_deterministic(tmp_path: Path):
    """Two economy-off runs with the same seed + mocked chat commit an
    identical event stream, confirming the flag-off path adds no rng
    perturbation. The diversity lever is disabled because it draws from an
    unseeded per-agent rng (pre-existing, unrelated to the economy) which would
    otherwise make any two runs differ."""

    def _run_once(d: Path) -> list[tuple[str, str]]:
        with (
            patch("microverse.agents.artisan.DIVERSITY_SUBSTITUTE_PROB", 0.0),
            patch("microverse.agents.artisan.chat", return_value=_study_only()),
        ):
            run(ticks=10, seed=5, tempo=0, data_dir=d, harvest_dir=d / "h", solo=True)
        return _events(d)

    assert _run_once(tmp_path / "a") == _run_once(tmp_path / "b")


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


def _fresh_ledger() -> EnergyLedger:
    return EnergyLedger.fresh(
        ["Aki"], max_energy=100.0, regen_per_tick=12.0, cost_table=VERB_COST_BY_ROLE
    )


def test_lazy_attach_energy_attaches_unattached_agent():
    """A Watchdog-spawned Stranger registers mid-run without an EnergyLedger;
    the run loop must attach it so the lever applies to it like the startup
    roster (otherwise it would execute unaffordable verbs, biasing the A/B)."""
    led = _fresh_ledger()
    spawned = Artisan(name="Zix")  # never in the fresh roster, no energy attached
    assert spawned._energy is None
    with _economy("sub"):
        _lazy_attach_energy(spawned, led)
    assert spawned._energy is led


def test_lazy_attach_energy_noop_in_scene_gate_only_mode():
    """``throttle`` is scene-gate-only: no substitution, so no agent gets the
    ledger attached — the spawned Stranger stays consistent with the roster."""
    led = _fresh_ledger()
    spawned = Artisan(name="Zix")
    with _economy("throttle"):
        _lazy_attach_energy(spawned, led)
    assert spawned._energy is None


def test_lazy_attach_energy_noop_when_economy_off():
    spawned = Artisan(name="Zix")
    _lazy_attach_energy(spawned, None)  # economy off: no ledger to attach
    assert spawned._energy is None


def test_invalid_economy_mode_fails_fast(tmp_path: Path):
    """A typo'd MICROVERSE_ECONOMY must raise, not silently run an unlabeled
    no-op arm (ECONOMY_ENABLED but neither gate nor substitution)."""
    with (
        patch.object(config, "ECONOMY_MODE", "bogus"),
        patch.object(config, "ECONOMY_ENABLED", True),
        pytest.raises(ValueError, match="not a valid economy mode"),
    ):
        run(
            ticks=1,
            seed=1,
            tempo=0,
            data_dir=tmp_path / "data",
            harvest_dir=tmp_path / "h",
            solo=True,
        )
