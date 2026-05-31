"""Offline replay + synthetic simulator (scripts/replay_economy.py, ADR 0008).

Stage 0/1 estimators must be deterministic and must reuse the live executor
(``EnergyLedger.resolve_executed_verb``) so a draining policy is genuinely
throttled. Zero LLM compute.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from microverse.config import VERB_COST_BY_ROLE
from microverse.world.economy import EnergyLedger

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "replay_economy.py"
_spec = importlib.util.spec_from_file_location("replay_economy", _PATH)
assert _spec is not None
assert _spec.loader is not None
replay_economy = importlib.util.module_from_spec(_spec)
sys.modules["replay_economy"] = replay_economy
_spec.loader.exec_module(replay_economy)


def _ledger(level: float | None = None) -> EnergyLedger:
    led = EnergyLedger.fresh(
        ["Aki"], max_energy=100.0, regen_per_tick=12.0, cost_table=VERB_COST_BY_ROLE
    )
    if level is not None:
        led._pool["Aki"] = level
    return led


def test_replay_executor_deterministic():
    trace = [("Aki", "artisan", "study")] * 30
    r1 = replay_economy.replay_executor(trace, ledger=_ledger())
    r2 = replay_economy.replay_executor(trace, ledger=_ledger())
    assert r1 == r2  # pure function of (trace, fresh ledger)


def test_replay_keeps_affordable_specialty():
    # Artisan crafting (cost 6 < regen 12): always affordable, never substituted.
    trace = [("Aki", "artisan", "craft")] * 50
    r = replay_economy.replay_executor(trace, ledger=_ledger())
    assert r["substitution_rate"] == 0.0
    assert r["executed_counts"] == {"craft": 50}


def test_replay_throttles_unaffordable_drain():
    # Artisan contributing every tick (cost 22 > regen 12) drains and the
    # executor substitutes once it cannot pay. craft is a payload verb the lever
    # cannot fabricate, so the target is a payload-free verb (study), never a
    # hollow craft (review).
    trace = [("Aki", "artisan", "contribute")] * 60
    r = replay_economy.replay_executor(trace, ledger=_ledger())
    assert r["chosen_contribute_share"] == 1.0
    assert r["executed_contribute_share"] < 1.0  # throttled
    assert r["substitution_rate"] > 0.0
    assert "craft" not in r["executed_counts"]  # never fabricated by substitution
    assert "study" in r["executed_counts"]  # cheapest affordable payload-free verb


def test_replay_forced_scene_contributes_not_substituted():
    # Scene turns (forced=True) are deducted but NEVER substituted — matching
    # live, where the lever skips scene turns — even when the pool cannot afford
    # them (deduct clamps at 0). Without this the offline estimate would predict
    # substitutions that cannot happen live and inflate the rate (review).
    trace = [("Aki", "artisan", "contribute", True)] * 60
    r = replay_economy.replay_executor(trace, ledger=_ledger())
    assert r["substitution_rate"] == 0.0
    assert r["executed_counts"] == {"contribute": 60}


def test_synthetic_always_contribute_single_agent_throttled():
    # One agent, contribute >> regen: a pure-contribute policy must be throttled.
    out = replay_economy.synthetic_run(
        "always-contribute",
        n_ticks=80,
        roster=(("Aki", "artisan"),),
        cost_table=VERB_COST_BY_ROLE,
        seed=1,
    )
    assert out["executed_contribute_share"] < 1.0
    assert out["substitution_rate"] > 0.0


def test_synthetic_respects_energy_overrides():
    # The override knobs must thread through to the ledger so the Stage-1 sweep
    # can find throttling numbers without editing config. Same 2-agent
    # always-contribute policy + seed: a generous regen sustains it (not
    # throttled), a tight regen drains it (throttled).
    roster = (("Aki", "artisan"), ("Cy", "scholar"))
    generous = replay_economy.synthetic_run(
        "always-contribute",
        n_ticks=400,
        roster=roster,
        cost_table=VERB_COST_BY_ROLE,
        seed=42,
        max_energy=100.0,
        regen_per_tick=12.0,
    )
    tight = replay_economy.synthetic_run(
        "always-contribute",
        n_ticks=400,
        roster=roster,
        cost_table=VERB_COST_BY_ROLE,
        seed=42,
        max_energy=100.0,
        regen_per_tick=8.0,
    )
    assert generous["substitution_rate"] == 0.0
    assert tight["substitution_rate"] > 0.0


def test_main_threads_overrides_into_synthetic(monkeypatch):
    # The --synthetic CLI path must hand the parsed knobs to synthetic_run, not
    # the bare config constants (otherwise the sweep silently ignores --regen).
    captured: dict[str, float] = {}

    def _spy(policy: str, **kwargs: object) -> dict:
        captured["max_energy"] = kwargs["max_energy"]  # type: ignore[assignment]
        captured["regen_per_tick"] = kwargs["regen_per_tick"]  # type: ignore[assignment]
        return {
            "policy": policy,
            "total": 0,
            "executed_counts": {},
            "substitution_rate": 0.0,
            "executed_contribute_share": 0.0,
            "executed_entropy_norm": 0.0,
        }

    monkeypatch.setattr(replay_economy, "synthetic_run", _spy)
    rc = replay_economy.main(["--synthetic", "--ticks", "5", "--energy-max", "55", "--regen", "7"])
    assert rc == 0
    assert captured == {"max_energy": 55.0, "regen_per_tick": 7.0}


def test_main_threads_overrides_into_stage0(monkeypatch, tmp_path):
    # The --data (Stage 0) path must construct the ledger with the parsed knobs.
    run = tmp_path / "run"
    run.mkdir()
    conn = sqlite3.connect(run / "episodic.sqlite")
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, actor TEXT, action TEXT, payload_json TEXT)"
    )
    conn.commit()
    conn.close()

    captured: dict[str, float] = {}
    real_fresh = replay_economy.EnergyLedger.fresh

    def _spy_fresh(names: object, **kwargs: object) -> EnergyLedger:
        captured["max_energy"] = kwargs["max_energy"]  # type: ignore[assignment]
        captured["regen_per_tick"] = kwargs["regen_per_tick"]  # type: ignore[assignment]
        return real_fresh(names, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(replay_economy.EnergyLedger, "fresh", staticmethod(_spy_fresh))
    rc = replay_economy.main(["--data", str(run), "--energy-max", "55", "--regen", "7"])
    assert rc == 0
    assert captured == {"max_energy": 55.0, "regen_per_tick": 7.0}


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--energy-max", "0"), ("--energy-max", "-5"), ("--regen", "-1")],
)
def test_cli_rejects_nonsensical_knobs(flag: str, value: str):
    # Fail fast on meaningless tuning input rather than silently producing a
    # degenerate all-rest sweep (a non-positive pool / negative regen is never a
    # valid economy). argparse error() exits with code 2.
    with pytest.raises(SystemExit):
        replay_economy.parse_args(["--synthetic", flag, value])


def test_synthetic_role_biased_diversifies():
    out = replay_economy.synthetic_run(
        "role-biased",
        n_ticks=200,
        roster=(("Aki", "artisan"), ("Cy", "scholar")),
        cost_table=VERB_COST_BY_ROLE,
        seed=7,
    )
    # Two specialists pulling toward different cheap verbs => non-trivial entropy.
    assert out["executed_entropy_norm"] > 0.0
