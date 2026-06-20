"""Tests for scripts/check_results_data.py — the results.html data-integrity checker.

The checker validates that every number embedded in results.html (the public writeup)
matches its committed source: per-seed gate-report.json files for the dose/lever metrics,
and findings-doc text for the few doc-only values. It also asserts EN/JA parity (same
parsed data, same strings key-shape, same citations and anchors). These tests exercise the
pure parse/compare logic against fixtures so they run offline in CI (the real data/ dirs
are untracked).
"""

import importlib.util
import json
from pathlib import Path

import pytest

# Load the script directly (it lives under scripts/, not on sys.path); the asserts keep
# type-checkers happy and surface a clear error if the spec can't be built.
_SPEC = importlib.util.spec_from_file_location(
    "check_results_data",
    Path(__file__).resolve().parent.parent / "scripts" / "check_results_data.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
crd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(crd)


def _html_with(data: dict, strings: dict | None = None) -> str:
    block = json.dumps(data)
    sblock = json.dumps(strings if strings is not None else {"cmp": {"before": "Before"}})
    return (
        "<!doctype html><html><body>"
        f'<script id="microverse-data" type="application/json">{block}</script>'
        f'<script id="microverse-strings" type="application/json">{sblock}</script>'
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


def _html_full(data: dict, strings: dict) -> str:
    return (
        "<!doctype html><html><body>"
        f'<script id="microverse-data" type="application/json">{json.dumps(data)}</script>'
        f'<script id="microverse-strings" type="application/json">{json.dumps(strings)}</script>'
        '<section id="dose"></section><section id="specialization"></section>'
        "</body></html>"
    )


def test_parity_strings_same_shape_different_text_passes():
    data = {"meta": {"floor": 0.25}}
    en = _html_full(data, {"cmp": {"before": "Before", "after": "After"}})
    ja = _html_full(data, {"cmp": {"before": "変更前", "after": "変更後"}})
    assert crd.check_parity(en, ja) == []


def test_parity_strings_key_drift_flagged():
    data = {"meta": {"floor": 0.25}}
    en = _html_full(data, {"cmp": {"before": "Before", "after": "After"}})
    ja = _html_full(data, {"cmp": {"before": "変更前"}})  # missing 'after'
    errs = crd.check_parity(en, ja)
    assert any("strings block" in e.lower() for e in errs)


def test_parity_missing_anchor_flagged():
    data = {"meta": {}}
    strings = {"cmp": {"before": "Before"}}
    en = _html_with(data, strings)
    # JA missing the #dose anchor
    ja = (
        "<!doctype html><html><body>"
        f'<script id="microverse-data" type="application/json">{json.dumps(data)}</script>'
        f'<script id="microverse-strings" type="application/json">{json.dumps(strings)}</script>'
        '<section id="specialization"></section></body></html>'
    )
    errs = crd.check_parity(en, ja)
    assert errs
    assert any("anchor" in e.lower() for e in errs)


def test_parity_anchor_missing_from_both_flagged():
    data = {"meta": {}}
    strings = {"cmp": {"before": "B"}}
    body = (
        "<!doctype html><html><body>"
        f'<script id="microverse-data" type="application/json">{json.dumps(data)}</script>'
        f'<script id="microverse-strings" type="application/json">{json.dumps(strings)}</script>'
        '<section id="specialization"></section></body></html>'  # #dose absent in BOTH
    )
    errs = crd.check_parity(body, body)
    assert any("#dose" in e and "both pages" in e for e in errs)


def test_parity_cite_href_drift_flagged():
    data = {"meta": {}}
    strings = {"cmp": {"before": "B"}}

    def page(href: str) -> str:
        sblock = json.dumps(strings)
        return (
            "<!doctype html><html><body>"
            f'<script id="microverse-data" type="application/json">{json.dumps(data)}</script>'
            f'<script id="microverse-strings" type="application/json">{sblock}</script>'
            f'<a class="cite" href="{href}">ADR 0016</a>'
            '<section id="dose"></section><section id="specialization"></section></body></html>'
        )

    errs = crd.check_parity(page("/docs/adr/0016.md"), page("/docs/adr/0018.md"))
    assert any("citation" in e.lower() for e in errs)
    # same href on both -> no citation error
    assert crd.check_parity(page("/docs/adr/0016.md"), page("/docs/adr/0016.md")) == []


# ── doc-value matching ──────────────────────────────────────────────────────


def test_doc_has_digit_boundary(tmp_path: Path):
    (tmp_path / "d.md").write_text("the share was 0.265 in that run", encoding="utf-8")
    assert crd._doc_has(tmp_path, "d.md", 0.265) is True
    # 0.26 must NOT match inside 0.265
    assert crd._doc_has(tmp_path, "d.md", 0.26) is False


def test_doc_has_reads_utf8(tmp_path: Path):
    (tmp_path / "d.md").write_text("日本語 0.156 の値", encoding="utf-8")
    assert crd._doc_has(tmp_path, "d.md", 0.156) is True


# ── robustness: corrupt source, missing cited doc ───────────────────────────


def test_corrupt_gate_report_is_hard_error(tmp_path: Path):
    d = tmp_path / "econ-x-s101"
    d.mkdir()
    (d / "gate-report.json").write_text("{not valid json", encoding="utf-8")
    _, err = crd.read_seed_metric(tmp_path, "econ-x-s101", "mean_pairwise_jsd", 0.3053)
    assert err is not None
    assert "unreadable" in err.lower()
    # corrupt != missing: must NOT be classified as a soft skip
    assert crd._MISSING_DIR not in err


def test_missing_cited_doc_is_error(tmp_path: Path):
    data = {
        "monoculture": {
            "contribute_no_economy": [0.88, 0.92],
            "contribute_with_economy": [0.26, 0.31],
            "doc": "docs/does-not-exist.md",
        },
        "specialization": {
            "before": {"dir": "data/x", "mpj": 0.2, "agents": {}},
            "after": {"dir": "data/y", "mpj": 0.3, "agents": {}},
        },
        "dose": [],
        "levers": [],
    }
    errs = crd.check_sources(data, tmp_path)
    assert any("cited doc not found" in e for e in errs)
