"""Tests for scripts/check_results_data.py — the results.html data-integrity checker.

The checker validates that every number embedded in results.html (the public writeup)
matches its committed source: per-seed gate-report.json files for the dose/lever metrics,
and findings-doc text for the few doc-only values. It also asserts EN/JA parity (the two
pages must carry a byte-identical data block). These tests exercise the pure parse/compare
logic against fixtures so they run offline in CI (the real data/ dirs are untracked).
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_results_data",
    Path(__file__).resolve().parent.parent / "scripts" / "check_results_data.py",
)
crd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(crd)


def _html_with(data: dict) -> str:
    block = json.dumps(data)
    return (
        "<!doctype html><html><body>"
        f'<script id="microverse-data" type="application/json">{block}</script>'
        '<section id="dose"></section><section id="specialization"></section>'
        "</body></html>"
    )


def _gate_report(mpj: float | None = None, jsd: float | None = None, agents: dict | None = None):
    chosen: dict = {}
    if mpj is not None:
        chosen["mean_pairwise_jsd"] = mpj
    if jsd is not None:
        chosen["jsd_norm"] = jsd
    if agents is not None:
        chosen["per_agent_verb_share"] = agents
    return {"gate_9_verb_diversity": {"chosen": chosen}}


# ── extract_data ────────────────────────────────────────────────────────────


def test_extract_data_parses_json_block():
    data = {"meta": {"floor": 0.25}}
    assert crd.extract_data(_html_with(data)) == data


def test_extract_data_missing_block_raises():
    with pytest.raises(ValueError, match="microverse-data"):
        crd.extract_data("<html><body>no data here</body></html>")


# ── mean recomputation ──────────────────────────────────────────────────────


def test_mean_mismatch_is_flagged():
    entry = {"label": "x", "dose": "bal@30", "seeds": [0.30, 0.40, 0.50], "mean": 0.99}
    errs = crd.check_dose_mean(entry)
    assert len(errs) == 1
    assert "mean" in errs[0]


def test_mean_match_passes():
    entry = {"label": "x", "dose": "bal@30", "seeds": [0.2714, 0.2488, 0.2583], "mean": 0.2595}
    assert crd.check_dose_mean(entry) == []


# ── source cross-check (per-seed gate-report.json) ──────────────────────────


def test_seed_value_matches_source(tmp_path: Path):
    d = tmp_path / "econ-x-s101"
    d.mkdir()
    (d / "gate-report.json").write_text(json.dumps(_gate_report(mpj=0.3053)))
    val, err = crd.read_seed_metric(tmp_path, "econ-x-s101", "mean_pairwise_jsd", 0.3053)
    assert err is None
    assert val == 0.3053


def test_seed_value_mismatch_flagged(tmp_path: Path):
    d = tmp_path / "econ-x-s101"
    d.mkdir()
    (d / "gate-report.json").write_text(json.dumps(_gate_report(mpj=0.40)))
    _, err = crd.read_seed_metric(tmp_path, "econ-x-s101", "mean_pairwise_jsd", 0.3053)
    assert err is not None
    assert "0.3053" in err


def test_seed_missing_dir_reports_skip(tmp_path: Path):
    _, err = crd.read_seed_metric(tmp_path, "econ-missing-s101", "jsd_norm", 0.25)
    assert err is not None
    assert "missing" in err.lower()


def test_within_tolerance_passes(tmp_path: Path):
    d = tmp_path / "econ-x-s101"
    d.mkdir()
    # 4dp rounding: page rounds 0.30529 -> 0.3053; source is full precision
    (d / "gate-report.json").write_text(json.dumps(_gate_report(mpj=0.30529)))
    _, err = crd.read_seed_metric(tmp_path, "econ-x-s101", "mean_pairwise_jsd", 0.3053)
    assert err is None


# ── EN/JA parity ────────────────────────────────────────────────────────────


def test_parity_identical_blocks_pass():
    data = {"meta": {"floor": 0.25}, "dose": []}
    en = _html_with(data)
    ja = _html_with(data)
    assert crd.check_parity(en, ja) == []


def test_parity_diverging_data_flagged():
    en = _html_with({"meta": {"floor": 0.25}})
    ja = _html_with({"meta": {"floor": 0.30}})
    errs = crd.check_parity(en, ja)
    assert len(errs) == 1
    assert "data block" in errs[0].lower()


def test_parity_missing_anchor_flagged():
    data = {"meta": {}}
    en = _html_with(data)
    # JA missing the #dose anchor
    ja = (
        "<!doctype html><html><body>"
        f'<script id="microverse-data" type="application/json">{json.dumps(data)}</script>'
        '<section id="specialization"></section></body></html>'
    )
    errs = crd.check_parity(en, ja)
    assert errs
    assert any("anchor" in e.lower() for e in errs)
