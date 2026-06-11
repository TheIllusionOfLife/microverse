"""Mechanism audit for the Stage 6 Gate 9 PASS (ADR 0012 Phase 2 item 2).

The audit must show the scarcity hint is the OPERATIVE channel behind the
``bal@30`` specialization, not a coincidence: reconstruct per-turn hint state
offline by replaying the energy ledger over the logged executed-verb stream,
then read hint firing rates, conditional chosen-verb probabilities, and a
predicted-vs-logged substitution fidelity check. Zero LLM compute; the
reconstruction must match ``run.py``'s live hint semantics exactly (parity
tests below pin it to the live helpers).
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from microverse.world.economy import EnergyLedger, build_cost_table

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "replay_economy.py"
_spec = importlib.util.spec_from_file_location("replay_economy", _PATH)
assert _spec is not None
assert _spec.loader is not None
replay_economy = importlib.util.module_from_spec(_spec)
sys.modules["replay_economy"] = replay_economy
_spec.loader.exec_module(replay_economy)


def _ledger(
    levels: dict[str, float] | None = None,
    *,
    mode: str = "bal",
    target: float | None = 30.0,
    regen: float = 8.0,
    names: tuple[str, ...] = ("Aki", "Cy"),
) -> EnergyLedger:
    led = EnergyLedger.fresh(
        names,
        max_energy=100.0,
        regen_per_tick=regen,
        cost_table=build_cost_table(mode, balanced_contribute=target if mode == "bal" else None),
    )
    for name, level in (levels or {}).items():
        led._pool[name] = level
    return led


def _ev(
    actor: str = "Cy",
    role: str = "scholar",
    chosen: str = "contribute",
    executed: str | None = None,
    **kwargs: object,
) -> object:
    return replay_economy.AuditEvent(
        actor=actor,
        role=role,
        chosen=chosen,
        executed=executed if executed is not None else chosen,
        **kwargs,  # type: ignore[arg-type]
    )


# --- trace extraction -------------------------------------------------------


def _write_episodic(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, actor TEXT, action TEXT, payload_json TEXT)"
    )
    conn.executemany("INSERT INTO events (actor, action, payload_json) VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def test_audit_trace_extracts_executed_chosen_and_flags(tmp_path):
    # The audit trace must carry BOTH verb streams plus the per-event telemetry
    # flags: executed (the action column), chosen (payload.parsed_verb),
    # parse_fallback and economy_substituted (gate9 stream parity).
    ep = _write_episodic(
        tmp_path / "episodic.sqlite",
        [
            (
                "Cy",
                "study",
                '{"role": "scholar", "parsed_verb": "contribute", '
                '"parse_fallback": false, "economy_substituted": true}',
            ),
            ("Aki", "contribute", '{"role": "artisan", "scene_id": "s-1"}'),
            ("world", "weather.rain", "{}"),
            ("Cy", "rest", '{"role": "scholar", "parsed_verb": "rest", "parse_fallback": true}'),
        ],
    )
    trace = replay_economy._audit_trace_from_episodic(ep)
    assert len(trace) == 3  # world event dropped
    first = trace[0]
    assert (first.actor, first.role) == ("Cy", "scholar")
    assert (first.chosen, first.executed) == ("contribute", "study")
    assert first.economy_substituted is True
    assert first.parse_fallback is False
    assert first.forced is False
    scene = trace[1]
    assert scene.forced is True
    assert scene.scene_id == "s-1"
    fallback = trace[2]
    assert fallback.parse_fallback is True


# --- hint-state parity with the live helpers --------------------------------


@pytest.mark.parametrize("mode", ["adv", "bal", "sub"])
@pytest.mark.parametrize("level", [100.0, 25.0, 10.0, 2.0])
def test_hint_state_parity_with_run_helpers(monkeypatch, mode: str, level: float):
    # The offline reconstruction must agree with run.py's live hint helpers
    # (_compute_energy_hint fires, _energy_hint_verb names) at every pool level
    # and mode — this test is the drift guard for the audit's validity.
    from microverse import config, run

    monkeypatch.setattr(config, "ECONOMY_MODE", mode)
    monkeypatch.setattr(config, "_ECONOMY_SUBSTITUTE", True)
    led = _ledger({"Cy": level}, mode=mode, target=30.0 if mode == "bal" else None)
    # Duck-typed stand-in: the live helpers only read .name/.role.
    agent: Any = SimpleNamespace(name="Cy", role="scholar")
    fired, easy = replay_economy._hint_state(led, "Cy", "scholar", mode=mode)
    assert fired == (run._compute_energy_hint(led, agent) != "")
    assert easy == run._energy_hint_verb(led, agent)


def test_hint_state_silent_in_non_substitute_mode():
    # Mode "throttle" has no prompt hint live (run.py gates on _ECONOMY_SUBSTITUTE);
    # the reconstruction must stay silent for it too.
    led = _ledger({"Cy": 2.0}, mode="adv", target=None)
    assert replay_economy._hint_state(led, "Cy", "scholar", mode="throttle") == (False, None)


# --- ledger arithmetic fidelity ---------------------------------------------


def test_audit_deducts_logged_executed_verb():
    # Live deducts the FINAL committed verb (run.py:1222), which can differ from
    # resolve_executed_verb(chosen) when a later lever (engagement gate)
    # overrode the economy's output. The audit must follow the log, not re-run
    # the executor. Here resolve(contribute) at pool 50 would keep contribute
    # (affordable, cost 30) and leave 28; the log says study was executed (6).
    led = _ledger({"Cy": 50.0})
    replay_economy.audit_run([_ev(chosen="contribute", executed="study")], ledger=led, mode="bal")
    assert led.current("Cy") == pytest.approx(52.0)  # 50 - 6 + regen 8


def test_audit_scene_turns_excluded_and_single_regen():
    # Scene turns are forced contributes: never hint-eligible (run.py forces
    # energy_hint="" in scenes), excluded from free-turn/conditional streams,
    # and a whole scene is ONE regen tick (run.py:1127).
    led = _ledger({"Cy": 10.0})
    events = [
        _ev("Aki", "artisan", "contribute", forced=True, scene_id="s1"),
        _ev("Aki", "artisan", "contribute", forced=True, scene_id="s1"),
        _ev("Aki", "artisan", "contribute", forced=True, scene_id="s1"),
    ]
    report = replay_economy.audit_run(events, ledger=led, mode="bal")
    assert report["agents"]["Aki"]["free_turns"] == 0
    assert report["agents"]["Aki"]["chosen"] == {
        "hint": {},
        "absent_low": {},
        "absent_comfortable": {},
    }
    assert led.current("Cy") == pytest.approx(18.0)  # one +8, not three


# --- hint firing under drain -------------------------------------------------


def _drain_trace(n: int) -> list[object]:
    return [_ev(chosen="contribute", executed="contribute") for _ in range(n)]


def test_audit_hint_firing_rate_rises_with_target():
    # The R2 dose mechanism: a dearer balanced contribute drains the scholar
    # below affordability sooner, so the reconstructed hint fires more often at
    # target 30 than at 22 — and never under a generous regen.
    r30 = replay_economy.audit_run(
        _drain_trace(12), ledger=_ledger(target=30.0, names=("Cy",)), mode="bal"
    )
    r22 = replay_economy.audit_run(
        _drain_trace(12), ledger=_ledger(target=22.0, names=("Cy",)), mode="bal"
    )
    generous = replay_economy.audit_run(
        _drain_trace(12), ledger=_ledger(target=30.0, regen=50.0, names=("Cy",)), mode="bal"
    )
    rate30 = r30["agents"]["Cy"]["hint"]["rate"]
    rate22 = r22["agents"]["Cy"]["hint"]["rate"]
    assert rate30 > rate22 > 0.0
    assert generous["agents"]["Cy"]["hint"]["rate"] == 0.0


# --- conditional verb probabilities + obedience ------------------------------


def test_audit_conditional_split_and_obedience():
    # Pool pinned at 10 (regen 0, rest costs 0): contribute(30) is out of reach
    # every turn, so the hint fires every turn and names study (cheapest
    # affordable perceived). The agent always chooses study: P(study|hint)=1.0,
    # obedience 1.0, and the absent strata stay empty.
    led = _ledger({"Cy": 10.0}, regen=0.0)
    events = [_ev(chosen="study", executed="rest") for _ in range(10)]
    report = replay_economy.audit_run(events, ledger=led, mode="bal")
    cy = report["agents"]["Cy"]
    assert cy["hint"]["rate"] == 1.0
    assert cy["hint"]["easy_verbs"] == {"study": 10}
    assert cy["p_chosen"]["hint"]["study"] == 1.0
    assert cy["chosen"]["absent_low"] == {}
    assert cy["chosen"]["absent_comfortable"] == {}
    assert cy["obedience_rate"] == 1.0


def test_audit_low_energy_stratum_splits_hint_absent_turns():
    # Deconfound stratum (audit review): hint-absent turns split into
    # "low" (contribute affordable but within one regen of the threshold:
    # energy < cost + regen) vs "comfortable". Pool 31 at bal@30/regen 8 is
    # affordable-but-low (31 < 38); pool 100 is comfortable.
    low = replay_economy.audit_run(
        [_ev(chosen="contribute", executed="rest")],
        ledger=_ledger({"Cy": 31.0}, regen=0.0),
        mode="bal",
    )
    comfy = replay_economy.audit_run(
        [_ev(chosen="contribute", executed="rest")],
        ledger=_ledger({"Cy": 100.0}, regen=0.0),
        mode="bal",
    )
    assert low["agents"]["Cy"]["chosen"]["absent_low"] == {"contribute": 1}
    assert low["agents"]["Cy"]["chosen"]["absent_comfortable"] == {}
    assert comfy["agents"]["Cy"]["chosen"]["absent_comfortable"] == {"contribute": 1}
    assert comfy["agents"]["Cy"]["chosen"]["absent_low"] == {}


def test_audit_parse_fallback_excluded_from_chosen_streams():
    # gate9 parity: a parse-fallback REST is not a free verb choice; it must not
    # pollute the conditional chosen streams (but the turn still counts toward
    # the hint-rate denominator — the hint was computed live regardless).
    led = _ledger({"Cy": 100.0}, regen=0.0)
    events = [_ev(chosen="rest", executed="rest", parse_fallback=True)]
    report = replay_economy.audit_run(events, ledger=led, mode="bal")
    cy = report["agents"]["Cy"]
    assert cy["free_turns"] == 1
    assert cy["chosen"]["absent_comfortable"] == {}


# --- energy trace summary -----------------------------------------------------


def test_audit_energy_trace_summary():
    # Pre-deduct snapshots: pool pinned at 10 by regen 0 + rest. The summary
    # must report min/mean/equilibrium at 10 and contribute unaffordable on
    # every free turn (10 < 30).
    led = _ledger({"Cy": 10.0}, regen=0.0)
    events = [_ev(chosen="study", executed="rest") for _ in range(8)]
    report = replay_economy.audit_run(events, ledger=led, mode="bal")
    energy = report["agents"]["Cy"]["energy"]
    assert energy["min"] == pytest.approx(10.0)
    assert energy["mean"] == pytest.approx(10.0)
    assert energy["equilibrium"] == pytest.approx(10.0)
    assert energy["contribute_unaffordable_rate"] == 1.0


# --- predicted-vs-logged substitution fidelity --------------------------------


def test_audit_substitution_agreement_perfect():
    # Drained pool, chosen=contribute: the offline executor predicts a
    # substitution; the log agrees (economy_substituted=True, executed=study).
    led = _ledger({"Cy": 10.0}, regen=0.0)
    events = [
        _ev(chosen="contribute", executed="study", economy_substituted=True) for _ in range(5)
    ]
    report = replay_economy.audit_run(events, ledger=led, mode="bal")
    fid = report["fidelity"]
    assert fid["events"] == 5
    assert fid["rate"] == 1.0
    assert fid["hint_on"]["events"] == 5
    assert fid["hint_on"]["rate"] == 1.0
    assert fid["hint_off"]["events"] == 0


def test_audit_substitution_agreement_counts_mismatches():
    # An event the reconstruction predicts substituted but the log says was not
    # (or vice versa) must lower the agreement rate — this is the C5 gate that
    # decides whether the reconstruction can carry the audit at all.
    led = _ledger({"Cy": 10.0}, regen=0.0)
    events = [
        _ev(chosen="contribute", executed="study", economy_substituted=True),
        _ev(chosen="contribute", executed="study", economy_substituted=True),
        _ev(chosen="contribute", executed="contribute", economy_substituted=False),  # mismatch
        _ev(chosen="study", executed="study", economy_substituted=False),  # affordable: agree
    ]
    report = replay_economy.audit_run(events, ledger=led, mode="bal")
    fid = report["fidelity"]
    assert fid["events"] == 4
    assert fid["agreements"] == 3
    assert fid["rate"] == pytest.approx(0.75)


def test_audit_deterministic():
    led1 = _ledger({"Cy": 40.0})
    led2 = _ledger({"Cy": 40.0})
    events = _drain_trace(20)
    r1 = replay_economy.audit_run(events, ledger=led1, mode="bal")
    r2 = replay_economy.audit_run(events, ledger=led2, mode="bal")
    assert r1 == r2


# --- audit spec parsing --------------------------------------------------------


def test_parse_audit_spec_accepts_mode_target_path():
    spec = replay_economy.parse_audit_spec("bal@30=data/econ-stage6-bal30-s42")
    assert spec.mode == "bal"
    assert spec.target == 30.0
    assert spec.path == Path("data/econ-stage6-bal30-s42")
    assert spec.arm == "bal@30"
    plain = replay_economy.parse_audit_spec("adv=data/econ-stage6-adv-s42")
    assert plain.mode == "adv"
    assert plain.target is None
    assert plain.arm == "adv"


@pytest.mark.parametrize(
    "spec",
    [
        "data/no-equals",  # malformed
        "warp=data/x",  # unknown mode
        "adv@30=data/x",  # target only valid for bal
        "bal@nan=data/x",  # non-finite target
        "bal@-5=data/x",  # non-positive target
        "bal@30=",  # empty path
    ],
)
def test_parse_audit_spec_rejects_invalid(spec: str):
    with pytest.raises(ValueError, match="audit spec"):
        replay_economy.parse_audit_spec(spec)


# --- aggregation ----------------------------------------------------------------


def _wrap(arm: str, run: str, report: dict) -> dict:
    return {"arm": arm, "run": run, "report": report}


def test_aggregate_top_verb_stability_and_decomposition():
    # Two bal arms, one run each. Cy: hint fires more at 30 and study tracks the
    # hint; Aki: craft dominates everywhere. The aggregate must report per-arm
    # mean firing rates, per-agent top-verb stability, and the decomposition
    # (observed delta-study vs delta-firing x conditional effect).
    led22 = _ledger({"Cy": 100.0, "Aki": 100.0}, target=22.0)
    led30 = _ledger({"Cy": 100.0, "Aki": 100.0}, target=30.0)
    mixed22 = [
        ev
        for _ in range(15)
        for ev in (
            _ev("Cy", "scholar", "contribute", "contribute"),
            _ev("Aki", "artisan", "craft", "craft"),
        )
    ]
    mixed30 = [
        ev
        for _ in range(15)
        for ev in (
            _ev("Cy", "scholar", "contribute", "contribute"),
            _ev("Aki", "artisan", "craft", "craft"),
        )
    ]
    r22 = replay_economy.audit_run(mixed22, ledger=led22, mode="bal")
    r30 = replay_economy.audit_run(mixed30, ledger=led30, mode="bal")
    agg = replay_economy.aggregate_audit([_wrap("bal@22", "s42", r22), _wrap("bal@30", "s42", r30)])
    assert (
        agg["arms"]["bal@30"]["Cy"]["hint_rate_mean"]
        >= (agg["arms"]["bal@22"]["Cy"]["hint_rate_mean"])
    )
    aki = agg["stability"]["Aki"]
    assert aki["top_verb_by_run"] == {"bal@22/s42": "craft", "bal@30/s42": "craft"}
    assert aki["stable"] is True
    assert "decomposition" in agg
    assert "Cy" in agg["decomposition"]
    cy_dec = agg["decomposition"]["Cy"]
    assert set(cy_dec) >= {"verb", "observed_delta", "predicted_delta", "fit_ratio"}


def test_aggregate_single_arm_has_no_decomposition():
    led = _ledger({"Cy": 100.0}, target=30.0)
    r = replay_economy.audit_run(_drain_trace(5), ledger=led, mode="bal")
    agg = replay_economy.aggregate_audit([_wrap("bal@30", "s42", r)])
    assert agg["decomposition"] == {}


# --- CLI wiring -------------------------------------------------------------------


def test_main_audit_cli_end_to_end(tmp_path, capsys):
    # --audit must thread the spec's mode/target into the cost table, replay the
    # run dir's episodic.sqlite, and emit a mechanism_audit section with per-run
    # reports and the aggregate.
    run = tmp_path / "econ-stage6-bal30-s42"
    run.mkdir()
    _write_episodic(
        run / "episodic.sqlite",
        [
            (
                "Cy",
                "contribute",
                '{"role": "scholar", "parsed_verb": "contribute", '
                '"parse_fallback": false, "economy_substituted": false}',
            )
            for _ in range(5)
        ],
    )
    rc = replay_economy.main(["--audit", f"bal@30={run}"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    audit = report["mechanism_audit"]
    assert len(audit["runs"]) == 1
    assert audit["runs"][0]["arm"] == "bal@30"
    assert audit["runs"][0]["run"] == run.name
    assert "Cy" in audit["runs"][0]["report"]["agents"]
    assert "arms" in audit["aggregate"]


def test_main_audit_missing_episodic_fails_fast(tmp_path):
    run = tmp_path / "missing"
    run.mkdir()
    rc = replay_economy.main(["--audit", f"bal@30={run}"])
    assert rc == 2


def test_main_rejects_malformed_audit_spec():
    with pytest.raises(SystemExit):
        replay_economy.parse_args(["--audit", "warp=data/x"])
