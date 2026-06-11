"""Offline replay + synthetic simulator (scripts/replay_economy.py, ADR 0008).

Stage 0/1 estimators must be deterministic and must reuse the live executor
(``EnergyLedger.resolve_executed_verb``) so a draining policy is genuinely
throttled. Zero LLM compute.
"""

from __future__ import annotations

import importlib.util
import json
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


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--energy-max", "nan"),
        ("--energy-max", "inf"),
        ("--regen", "nan"),
        ("--regen", "inf"),
    ],
)
def test_cli_rejects_non_finite_knobs(flag: str, value: str):
    # float("nan")/inf pass the <= 0 / < 0 guards (nan comparisons are False, inf
    # is positive) and corrupt the replay the same way a non-finite bal target
    # would. Reject them consistently with --bal-contribute (Codex review).
    with pytest.raises(SystemExit):
        replay_economy.parse_args(["--synthetic", flag, value])


# --- Stage 6 R2: bal-contribute target + per-actor scarcity probe ---
# The offline instrument that pins the live tune target T* WITHOUT live compute:
# replay a locked economy-OFF trace at candidate contribute targets and read how
# often the lower-weight scholar's contribute is out of reach while its study
# specialty stays affordable (the mechanical proxy for the live hint firing).


def _scholar_ledger(level: float, *, target: float | None = None) -> EnergyLedger:
    from microverse.world.economy import build_cost_table

    led = EnergyLedger.fresh(
        ["Cy"],
        max_energy=100.0,
        regen_per_tick=8.0,
        cost_table=build_cost_table("bal", balanced_contribute=target),
    )
    led._pool["Cy"] = level
    return led


def test_replay_regens_whole_roster_per_event():
    # Faithful per-tick regen: a non-acting roster member still regenerates each
    # event (each trace event ~= one live tick). Without this the lightly-
    # scheduled scholar is under-regenerated ~2.4x, overstating its scarcity and
    # mis-pinning the Stage 6 tune target (R2 fidelity).
    from microverse.world.economy import EnergyLedger

    trace = [("Aki", "artisan", "craft")] * 10  # only Aki acts
    led = EnergyLedger.fresh(
        ["Aki", "Cy"], max_energy=100.0, regen_per_tick=8.0, cost_table=VERB_COST_BY_ROLE
    )
    led._pool["Cy"] = 10.0
    replay_economy.replay_executor(trace, ledger=led)
    assert led.current("Cy") == pytest.approx(90.0)  # +8 on each of Aki's 10 events


def test_classify_scarcity_states():
    # bal@30: contribute costs 30, study 6. Pool 10 -> contribute out of reach,
    # study affordable (the desired drain state). Pool 2 -> nothing productive
    # (rest only). Pool 100 -> all affordable.
    blocked_study_ok = replay_economy._classify_scarcity(
        _scholar_ledger(10.0, target=30.0), "Cy", "scholar"
    )
    assert blocked_study_ok == (False, True, True)  # (contribute_ok, study_ok, any_productive_ok)
    rest_only = replay_economy._classify_scarcity(
        _scholar_ledger(2.0, target=30.0), "Cy", "scholar"
    )
    assert rest_only == (False, False, False)
    ample = replay_economy._classify_scarcity(_scholar_ledger(100.0, target=30.0), "Cy", "scholar")
    assert ample == (True, True, True)


def test_replay_executor_reports_per_actor_scarcity():
    # A scholar contributing every turn under bal@30 (regen 8 < contribute 30):
    # contribute is out of reach most turns while study stays affordable, so the
    # probe reports a high contribute-out / study-ok rate and near-zero rest-only.
    trace = [("Cy", "scholar", "contribute")] * 80
    r = replay_economy.replay_executor(trace, ledger=_scholar_ledger(20.0, target=30.0))
    sc = r["scarcity"]["Cy"]
    assert sc["free_turns"] == 80
    assert sc["contribute_out_study_ok_rate"] > 0.8
    assert sc["rest_only_rate"] < 0.05


def test_replay_executor_scarcity_excludes_forced_turns():
    # Forced scene turns are never substituted and must not count as free turns
    # in the scarcity denominator (they cannot trigger the hint).
    trace = [("Cy", "scholar", "contribute", True)] * 40
    r = replay_economy.replay_executor(trace, ledger=_scholar_ledger(20.0, target=30.0))
    assert r["scarcity"]["Cy"]["free_turns"] == 0


def test_main_threads_bal_contribute_into_cost_table(monkeypatch):
    captured: dict[str, object] = {}
    real = replay_economy.build_cost_table

    def _spy(mode: str, **kwargs: object) -> dict:
        captured["mode"] = mode
        captured["balanced_contribute"] = kwargs.get("balanced_contribute")
        return real(mode, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(replay_economy, "build_cost_table", _spy)
    rc = replay_economy.main(
        ["--synthetic", "--ticks", "5", "--mode", "bal", "--bal-contribute", "28"]
    )
    assert rc == 0
    assert captured == {"mode": "bal", "balanced_contribute": 28.0}


def test_cli_rejects_nonpositive_bal_contribute():
    with pytest.raises(SystemExit):
        replay_economy.parse_args(["--synthetic", "--mode", "bal", "--bal-contribute", "0"])


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_cli_rejects_non_finite_bal_contribute(value: str):
    # A non-finite cost makes every affordability comparison degenerate (the
    # ledger treats contribute as permanently unavailable) and silently corrupts
    # the sweep instead of failing fast (Codex review).
    with pytest.raises(SystemExit):
        replay_economy.parse_args(["--synthetic", "--mode", "bal", "--bal-contribute", value])


def test_replay_regens_once_per_scene_not_per_scene_turn():
    # Live regenerates the whole roster ONCE after a 3-turn scene completes
    # (run.py:1127), not once per forced contribute. The replay must match: a
    # scene is a single tick. Over-regenerating +2 per scene inflates later
    # affordability and under-reports scarcity, mis-pinning the tune target
    # (Codex review P1).
    trace = [
        ("Aki", "artisan", "contribute", True, "scene-1"),
        ("Aki", "artisan", "contribute", True, "scene-1"),
        ("Aki", "artisan", "contribute", True, "scene-1"),
    ]
    led = EnergyLedger.fresh(
        ["Aki", "Cy"], max_energy=100.0, regen_per_tick=8.0, cost_table=VERB_COST_BY_ROLE
    )
    led._pool["Cy"] = 10.0
    replay_economy.replay_executor(trace, ledger=led)
    assert led.current("Cy") == pytest.approx(18.0)  # +8 once for the whole scene, not +24


def test_replay_free_turn_after_scene_is_its_own_tick():
    # A non-scene turn following a scene is its own tick and regenerates again.
    trace = [
        ("Aki", "artisan", "contribute", True, "scene-1"),
        ("Aki", "artisan", "contribute", True, "scene-1"),
        ("Cy", "scholar", "study", False, None),
    ]
    led = EnergyLedger.fresh(
        ["Aki", "Cy"], max_energy=100.0, regen_per_tick=8.0, cost_table=VERB_COST_BY_ROLE
    )
    led._pool["Cy"] = 10.0
    replay_economy.replay_executor(trace, ledger=led)
    # scene block regen once (+8 -> 18); Cy free turn deduct study 6 (-> 12) then regen +8 (-> 20)
    assert led.current("Cy") == pytest.approx(20.0)


def test_replay_free_turns_keyed_on_forced_not_scene_id():
    # The scene collapse must key on `forced`, not scene_id alone. Real traces
    # never emit a non-forced turn carrying a scene_id (forced == bool(scene_id)),
    # but the grouping must still treat two non-forced turns as two ticks even if
    # they happen to share a scene_id, so a malformed/hand-built trace cannot
    # suppress a free-turn regen (Codex review, defensive).
    trace = [
        ("Aki", "artisan", "craft", False, "s1"),
        ("Aki", "artisan", "craft", False, "s1"),
    ]
    led = EnergyLedger.fresh(
        ["Aki", "Cy"], max_energy=100.0, regen_per_tick=8.0, cost_table=VERB_COST_BY_ROLE
    )
    led._pool["Cy"] = 10.0
    replay_economy.replay_executor(trace, ledger=led)
    assert led.current("Cy") == pytest.approx(26.0)  # two free ticks => +8 twice, not once


def test_replay_two_adjacent_scenes_regen_once_each():
    # Distinct scene_ids are distinct ticks even back-to-back.
    trace = [
        ("Aki", "artisan", "contribute", True, "scene-1"),
        ("Aki", "artisan", "contribute", True, "scene-1"),
        ("Aki", "artisan", "contribute", True, "scene-2"),
        ("Aki", "artisan", "contribute", True, "scene-2"),
    ]
    led = EnergyLedger.fresh(
        ["Aki", "Cy"], max_energy=100.0, regen_per_tick=8.0, cost_table=VERB_COST_BY_ROLE
    )
    led._pool["Cy"] = 10.0
    replay_economy.replay_executor(trace, ledger=led)
    assert led.current("Cy") == pytest.approx(26.0)  # +8 per scene, two scenes


def test_trace_from_episodic_carries_scene_id(tmp_path):
    # The episodic trace must expose scene_id as the 5th tuple element so the
    # replay can group a scene's three forced contributes into one regen tick.
    run = tmp_path / "run"
    run.mkdir()
    conn = sqlite3.connect(run / "episodic.sqlite")
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, actor TEXT, action TEXT, payload_json TEXT)"
    )
    conn.executemany(
        "INSERT INTO events (actor, action, payload_json) VALUES (?,?,?)",
        [
            ("Cy", "study", '{"role": "scholar", "parsed_verb": "study"}'),
            ("Aki", "contribute", '{"role": "artisan", "scene_id": "scene-9"}'),
        ],
    )
    conn.commit()
    conn.close()
    trace = replay_economy._trace_from_episodic(run / "episodic.sqlite")
    assert trace[0] == ("Cy", "scholar", "study", False, None)
    assert trace[1] == ("Aki", "artisan", "contribute", True, "scene-9")


def test_main_reports_effective_bal_contribute_from_env(monkeypatch, capsys):
    # When --mode bal runs without --bal-contribute but the live env exported
    # ECONOMY_BALANCED_CONTRIBUTE, the report must record the EFFECTIVE target so a
    # stale value cannot silently relabel a bal@22 replay as bal@30 (Codex review P2).
    from microverse import config

    monkeypatch.setattr(config, "ECONOMY_BALANCED_CONTRIBUTE", 30.0)
    rc = replay_economy.main(["--mode", "bal", "--synthetic", "--ticks", "1"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["bal_contribute"] is None  # raw CLI flag was not passed
    assert report["effective_bal_contribute"] == 30.0  # but the applied target is honest


def test_main_effective_bal_contribute_is_natural_dearest_without_env(monkeypatch, capsys):
    from microverse import config

    monkeypatch.setattr(config, "ECONOMY_BALANCED_CONTRIBUTE", None)
    rc = replay_economy.main(["--mode", "bal", "--synthetic", "--ticks", "1"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["effective_bal_contribute"] == 22.0  # the table's natural dearest


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
