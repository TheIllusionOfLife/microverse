"""sweep_report: aggregate per-seed Gate-9 + fidelity reads into one sweep verdict.

Unit-tests the pure verdict functions on synthetic dicts and a CLI smoke test
that monkeypatches the heavy fidelity audit (which needs an episodic.sqlite).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sweep_report.py"
_spec = importlib.util.spec_from_file_location("sweep_report", _PATH)
sr = importlib.util.module_from_spec(_spec)
sys.modules["sweep_report"] = sr
_spec.loader.exec_module(sr)

_VERBS = ("speak", "craft", "study", "rest", "travel", "contribute")
_ROLES = {"Aki": "artisan", "Cy": "scholar", "Vesna": "stranger"}


def _share(**verbs: float) -> dict[str, float]:
    base: dict[str, float] = dict.fromkeys(_VERBS, 0.0)
    base.update(verbs)
    return base


_PASS_SHARES = {"Aki": _share(craft=1.0), "Cy": _share(study=1.0), "Vesna": _share(travel=1.0)}


def _fid(
    on: float | None = 0.95,
    off: float | None = 0.97,
    logged: float | None = 0.96,
    on_ev: int = 50,
    off_ev: int = 50,
    logged_ev: int = 50,
) -> dict:
    return {
        "rate": 0.96,
        "events": on_ev + off_ev,
        "agreements": 0,
        "hint_on": {"events": on_ev, "agreements": 0, "rate": on},
        "hint_off": {"events": off_ev, "agreements": 0, "rate": off},
        "hint_logged": {"events": logged_ev, "agreements": 0, "rate": logged},
    }


def _gate_report(mpj: float, shares: dict) -> dict:
    return {
        "gate_9_verb_diversity": {
            "chosen": {"mean_pairwise_jsd": mpj, "per_agent_verb_share": shares}
        }
    }


# --- parse_roster ----------------------------------------------------------
def test_parse_roster_maps_name_to_role():
    r = sr.parse_roster("artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70")
    assert r == {"Aki": "artisan", "Cy": "scholar", "Vesna": "stranger"}


def test_parse_roster_rejects_unknown_role():
    with pytest.raises(ValueError, match="no defined specialty"):
        sr.parse_roster("elder:Mara:50")


def test_parse_roster_rejects_malformed_entry():
    with pytest.raises(ValueError, match="role:name:tokens"):
        sr.parse_roster("artisan:Aki")


def test_parse_roster_rejects_duplicate_name():
    with pytest.raises(ValueError, match="duplicate"):
        sr.parse_roster("artisan:Aki:100,scholar:Aki:70")


# --- role_stability (Layer 2) ---------------------------------------------
def test_role_stability_disjoint_specialists_pass():
    shares = {"Aki": _share(craft=1.0), "Cy": _share(study=1.0), "Vesna": _share(travel=1.0)}
    out = sr.role_stability(shares, _ROLES)
    assert out["pass"] is True
    assert out["per_resident"]["Aki"]["top_non_contribute_verb"] == "craft"
    assert out["per_resident"]["Aki"]["cross_bleed"] == 0.0


def test_role_stability_contribute_dominant_but_specialty_topped_passes():
    shares = {
        "Aki": _share(contribute=0.6, craft=0.4),
        "Cy": _share(contribute=0.6, study=0.4),
        "Vesna": _share(contribute=0.6, travel=0.4),
    }
    out = sr.role_stability(shares, _ROLES)
    assert out["pass"] is True
    assert out["per_resident"]["Aki"]["is_specialty"] is True


def test_role_stability_cross_bleed_above_floor_fails():
    shares = {
        "Aki": _share(contribute=0.5, craft=0.42, study=0.08),
        "Cy": _share(study=1.0),
        "Vesna": _share(travel=1.0),
    }
    out = sr.role_stability(shares, _ROLES)
    assert out["per_resident"]["Aki"]["is_specialty"] is True
    assert out["per_resident"]["Aki"]["cross_bleed"] == pytest.approx(0.08)
    assert out["per_resident"]["Aki"]["pass"] is False
    assert out["pass"] is False


def test_role_stability_wrong_top_verb_fails():
    shares = {
        "Aki": _share(craft=1.0),
        "Cy": _share(study=1.0),
        "Vesna": _share(contribute=0.5, speak=0.4, travel=0.1),
    }
    out = sr.role_stability(shares, _ROLES)
    assert out["per_resident"]["Vesna"]["top_non_contribute_verb"] == "speak"
    assert out["per_resident"]["Vesna"]["is_specialty"] is False
    assert out["pass"] is False


def test_role_stability_reports_extra_agents_separately():
    shares = {
        "Aki": _share(craft=1.0),
        "Cy": _share(study=1.0),
        "Vesna": _share(travel=1.0),
        "Wanderer": _share(travel=1.0),  # watchdog rehab stranger, not in roster
    }
    out = sr.role_stability(shares, _ROLES)
    assert out["extra_agents"] == ["Wanderer"]
    assert "Wanderer" not in out["per_resident"]
    assert out["pass"] is True  # extras are informational, never gate


def test_role_stability_missing_resident_fails():
    shares = {"Aki": _share(craft=1.0), "Cy": _share(study=1.0)}  # Vesna absent
    out = sr.role_stability(shares, _ROLES)
    assert out["per_resident"]["Vesna"]["present"] is False
    assert out["per_resident"]["Vesna"]["pass"] is False
    assert out["pass"] is False


def test_role_stability_cross_bleed_is_max_not_sum_of_other_specialties():
    # Aki bleeds study 0.04 AND travel 0.04: sum=0.08 (>floor) but the binding
    # single verb is 0.04 (<=floor). The locked rule (ADR 0018) is per-verb max,
    # so this PASSES — guards against a regression to summing.
    shares = {
        "Aki": _share(contribute=0.5, craft=0.42, study=0.04, travel=0.04),
        "Cy": _share(study=1.0),
        "Vesna": _share(travel=1.0),
    }
    out = sr.role_stability(shares, _ROLES)
    assert out["per_resident"]["Aki"]["cross_bleed"] == pytest.approx(0.04)
    assert out["per_resident"]["Aki"]["pass"] is True
    assert out["pass"] is True


def test_role_stability_cross_bleed_at_floor_to_3dp_passes():
    # ADR 0018 s202 boundary: raw 0.0504 rounds to 0.050 == floor -> PASS, matching
    # the published adjudication precision (CROSS_BLEED_DECIMALS).
    shares = {
        "Aki": _share(contribute=0.5, craft=0.45, study=0.0504),
        "Cy": _share(study=1.0),
        "Vesna": _share(travel=1.0),
    }
    out = sr.role_stability(shares, _ROLES)
    assert out["per_resident"]["Aki"]["cross_bleed"] == pytest.approx(0.0504)
    assert out["per_resident"]["Aki"]["pass"] is True


def test_role_stability_own_specialty_never_counts_as_cross_bleed():
    # Two artisans share craft; an agent's OWN specialty must not be bled.
    roles = {"Aki": "artisan", "Bo": "artisan", "Cy": "scholar"}
    shares = {"Aki": _share(craft=1.0), "Bo": _share(craft=1.0), "Cy": _share(study=1.0)}
    out = sr.role_stability(shares, roles)
    assert out["per_resident"]["Aki"]["cross_bleed"] == 0.0
    assert out["pass"] is True


# --- fidelity_verdict ------------------------------------------------------
def test_fidelity_verdict_pass():
    assert sr.fidelity_verdict(_fid())["pass"] is True


def test_fidelity_verdict_low_rate_fails():
    out = sr.fidelity_verdict(_fid(on=0.80))
    assert out["pass"] is False
    assert out["subsets"]["hint_on"]["pass"] is False


def test_fidelity_verdict_too_few_events_fails():
    assert sr.fidelity_verdict(_fid(on_ev=3))["pass"] is False


def test_fidelity_verdict_none_rate_fails():
    assert sr.fidelity_verdict(_fid(on=None, on_ev=0))["pass"] is False


# --- seed_verdict / sweep_verdict -----------------------------------------
def test_seed_verdict_all_pass():
    sv = sr.seed_verdict("s101", _gate_report(0.31, _PASS_SHARES), _fid(), _ROLES)
    assert sv["pass"] is True
    assert sv["layer1"]["pass"] is True
    assert sv["status"] == "PASS"


def test_seed_verdict_layer1_below_floor_fails():
    sv = sr.seed_verdict("s101", _gate_report(0.20, _PASS_SHARES), _fid(), _ROLES)
    assert sv["layer1"]["pass"] is False
    assert sv["pass"] is False
    assert sv["status"] == "FAIL"


def test_seed_verdict_instrument_invalid_blocks_behavioral():
    sv = sr.seed_verdict("s101", _gate_report(0.31, _PASS_SHARES), _fid(on=0.5), _ROLES)
    assert sv["instrument_valid"] is False
    assert sv["pass"] is False
    assert sv["status"] == "INSTRUMENT-INVALID"


def test_sweep_verdict_two_of_three_passes():
    seeds = [
        sr.seed_verdict("s101", _gate_report(0.31, _PASS_SHARES), _fid(), _ROLES),
        sr.seed_verdict("s202", _gate_report(0.29, _PASS_SHARES), _fid(), _ROLES),
        sr.seed_verdict("s303", _gate_report(0.20, _PASS_SHARES), _fid(), _ROLES),
    ]
    out = sr.sweep_verdict(seeds)
    assert out["n_pass"] == 2
    assert out["n_total"] == 3
    assert out["pass"] is True


def test_sweep_verdict_one_of_three_fails():
    seeds = [
        sr.seed_verdict("s101", _gate_report(0.31, _PASS_SHARES), _fid(), _ROLES),
        sr.seed_verdict("s202", _gate_report(0.20, _PASS_SHARES), _fid(), _ROLES),
        sr.seed_verdict("s303", _gate_report(0.20, _PASS_SHARES), _fid(), _ROLES),
    ]
    out = sr.sweep_verdict(seeds)
    assert out["n_pass"] == 1
    assert out["pass"] is False


def test_thresholds_are_emitted_for_traceability():
    sv = sr.seed_verdict("s101", _gate_report(0.31, _PASS_SHARES), _fid(), _ROLES)
    assert sv["layer1"]["floor"] == sr.MEAN_PAIRWISE_FLOOR
    assert sv["fidelity"]["floor"] == sr.FIDELITY_FLOOR


# --- read_seed guard + CLI smoke ------------------------------------------
def test_read_seed_raises_on_legacy_report_without_per_agent_share(tmp_path, monkeypatch):
    (tmp_path / "gate-report.json").write_text(
        json.dumps({"gate_9_verb_diversity": {"chosen": {"mean_pairwise_jsd": 0.31}}})
    )
    monkeypatch.setattr(sr, "compute_fidelity", lambda *a, **k: _fid())
    with pytest.raises(ValueError, match="per_agent_verb_share"):
        sr.read_seed(tmp_path, "bal@42", _ROLES)


def test_main_smoke_writes_json_and_returns_zero(tmp_path, monkeypatch, capsys):
    for seed, mpj in (("s101", 0.31), ("s202", 0.29), ("s303", 0.20)):
        d = tmp_path / f"econ-x-{seed}"
        d.mkdir()
        (d / "gate-report.json").write_text(json.dumps(_gate_report(mpj, _PASS_SHARES)))
    # Avoid the episodic.sqlite audit entirely.
    monkeypatch.setattr(sr, "compute_fidelity", lambda *a, **k: _fid())
    out_json = tmp_path / "sweep-verdict.json"
    rc = sr.main(
        [
            str(tmp_path / "econ-x-s101"),
            str(tmp_path / "econ-x-s202"),
            str(tmp_path / "econ-x-s303"),
            "--roster",
            "artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70",
            "--audit-mode",
            "bal@42",
            "--json-out",
            str(out_json),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr().out
    assert "PASS" in captured
    obj = json.loads(out_json.read_text())
    assert obj["sweep"]["pass"] is True
    assert obj["sweep"]["n_pass"] == 2
    # Anti-fork traceability: thresholds + audit mode recorded in the artifact.
    assert obj["thresholds"]["mean_pairwise_floor"] == sr.MEAN_PAIRWISE_FLOOR
    assert obj["audit_mode"] == "bal@42"
