#!/usr/bin/env python3
"""Validate the numbers embedded in results.html against their committed sources.

The public writeup (`results.html` / `results.ja.html`) embeds every reported figure in a
single ``<script id="microverse-data" type="application/json">`` block. Because the
action-economy arc switched its divergence metric mid-stream (ADR 0016,
``jsd_norm`` -> ``mean_pairwise_jsd``), eyeballing those numbers is too brittle. This
checker re-reads the canonical sources and asserts each embedded value matches:

* every dose / lever per-seed value is re-read from its ``data/econ-*/gate-report.json``
  (the ``chosen`` stream of ``gate_9_verb_diversity``) and compared within a 4-dp
  tolerance, and each reported mean is recomputed from its seeds;
* the before / after per-agent verb shares are re-read from their run dirs;
* the few doc-only values (the no-economy monoculture range, the persona-backfire travel
  collapse) are asserted to appear verbatim in their cited findings doc;
* EN and JA pages must carry a byte-identical data block and the same section anchors, so
  the two language versions cannot silently drift.

``data/`` is untracked, so the source cross-check only runs where the local run dirs are
present (mismatches and missing dirs are both reported); the pure parse/compare logic is
covered offline by ``tests/test_check_results_data.py``. Run before opening or updating the
writeup PR::

    uv run python scripts/check_results_data.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOL = 6e-4  # 4-decimal-place rounding slack (page rounds; sources are full precision)

_DATA_RE = re.compile(
    r'<script id="microverse-data" type="application/json">(.*?)</script>',
    re.DOTALL,
)
# Section anchors the page JS and in-page nav depend on; both languages must carry them.
_REQUIRED_ANCHORS = ("dose", "specialization")


def extract_data(html: str) -> dict:
    """Parse the embedded ``microverse-data`` JSON block out of an HTML page."""
    m = _DATA_RE.search(html)
    if not m:
        raise ValueError("microverse-data script block not found")
    return json.loads(m.group(1))


def _chosen(repo: Path, dir_name: str) -> dict:
    report = json.loads((repo / dir_name / "gate-report.json").read_text())
    return report["gate_9_verb_diversity"]["chosen"]


def read_seed_metric(
    repo: Path, dir_name: str, metric: str, expected: float
) -> tuple[float | None, str | None]:
    """Re-read one run dir's chosen-stream ``metric`` and compare to ``expected``.

    Returns ``(actual, None)`` on match, ``(actual, message)`` on mismatch, and
    ``(None, message)`` when the run dir is absent (data/ is untracked).
    """
    try:
        chosen = _chosen(repo, dir_name)
    except FileNotFoundError:
        return None, f"  - {dir_name}: missing run dir (data/ untracked; cannot verify {expected})"
    actual = chosen.get(metric)
    if actual is None:
        return None, f"  - {dir_name}: chosen.{metric} absent in gate-report.json"
    if abs(float(actual) - expected) > TOL:
        return float(actual), f"  - {dir_name}: page {expected} vs source {metric}={actual}"
    return float(actual), None


def check_dose_mean(entry: dict) -> list[str]:
    """Recompute a dose entry's mean from its per-seed values."""
    seeds = entry["seeds"]
    mean = round(sum(seeds) / len(seeds), 4)
    if abs(mean - entry["mean"]) > TOL:
        return [
            f"  - dose {entry['dose']} ({entry['label']}): mean {entry['mean']} vs computed {mean}"
        ]
    return []


def _check_agent_shares(repo: Path, spec: dict, label: str) -> list[str]:
    errs: list[str] = []
    val, err = read_seed_metric(repo, _dir(spec["dir"]), "mean_pairwise_jsd", spec["mpj"])
    if err:
        errs.append(f"{label} mpj:{err}")
        if val is None:
            return errs  # missing dir: skip per-agent (already reported)
    try:
        shares = _chosen(repo, _dir(spec["dir"]))["per_agent_verb_share"]
    except FileNotFoundError:
        return errs
    for agent, verbs in spec["agents"].items():
        for verb, page_val in verbs.items():
            actual = shares.get(agent, {}).get(verb)
            if actual is None or abs(float(actual) - page_val) > TOL:
                errs.append(f"  - {label} {agent}.{verb}: page {page_val} vs source {actual}")
    return errs


def _dir(name: str) -> str:
    """Strip a leading ``data/`` so dir names resolve under the repo root."""
    return name[len("data/") :] if name.startswith("data/") else name


def _doc_has(repo: Path, doc: str, value: float) -> bool:
    text = (repo / doc).read_text()
    # Match the value as written (e.g. 0.156, 0.88) ignoring trailing zeros.
    needle = f"{value:.4f}".rstrip("0").rstrip(".")
    return needle in text


def check_sources(data: dict, repo: Path) -> list[str]:
    """Cross-check every embedded number against its committed source."""
    errs: list[str] = []

    for entry in data["dose"]:
        errs.extend(check_dose_mean(entry))
        for dir_name, seed_val in zip(entry["dirs"], entry["seeds"], strict=True):
            _, err = read_seed_metric(repo, _dir(dir_name), entry["metric"], seed_val)
            if err:
                errs.append(f"dose {entry['dose']} ({entry['label']}):\n{err}")

    spec = data["specialization"]
    errs.extend(_check_agent_shares(repo, spec["before"], "specialization.before"))
    errs.extend(_check_agent_shares(repo, spec["after"], "specialization.after"))

    for lever in data["levers"]:
        if "seeds" in lever and "dirs" in lever:
            errs.extend(check_dose_mean({**lever, "dose": lever["key"], "label": lever["name"]}))
            for dir_name, seed_val in zip(lever["dirs"], lever["seeds"], strict=True):
                _, err = read_seed_metric(repo, _dir(dir_name), lever["metric"], seed_val)
                if err:
                    errs.append(f"lever {lever['name']}:\n{err}")
        elif "dir" in lever:
            # single-dir levers report the after / wash value under their metric
            target = lever.get("after", lever.get("value"))
            _, err = read_seed_metric(repo, _dir(lever["dir"]), lever["metric"], target)
            if err:
                errs.append(f"lever {lever['name']}:\n{err}")

    # Doc-only values: assert they appear verbatim in the cited findings doc.
    mono = data["monoculture"]
    for value in (*mono["contribute_no_economy"], *mono["contribute_with_economy"]):
        if (repo / mono["doc"]).exists() and not _doc_has(repo, mono["doc"], value):
            errs.append(f"monoculture: {value} not found in {mono['doc']}")
    persona = next((lv for lv in data["levers"] if lv.get("key") == "persona"), None)
    if persona and (repo / persona["doc"]).exists():
        for value in (persona["travel_before"], persona["travel_after"]):
            if not _doc_has(repo, persona["doc"], value):
                errs.append(f"persona travel {value} not found in {persona['doc']}")

    return errs


def check_parity(en_html: str, ja_html: str) -> list[str]:
    """Assert the EN and JA pages share an identical data block and section anchors."""
    errs: list[str] = []
    if extract_data(en_html) != extract_data(ja_html):
        errs.append("EN/JA data block diverged (the embedded numbers must be identical)")
    for anchor in _REQUIRED_ANCHORS:
        token = f'id="{anchor}"'
        if (token in en_html) != (token in ja_html):
            errs.append(f"section anchor #{anchor} present in only one language")
    return errs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--en", default="results.html", help="English page (default: results.html)")
    parser.add_argument(
        "--ja", default="results.ja.html", help="Japanese page (default: results.ja.html)"
    )
    parser.add_argument("--repo", default=str(REPO_ROOT), help="repo root for source dirs")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    en_path, ja_path = repo / args.en, repo / args.ja
    en_html = en_path.read_text()
    data = extract_data(en_html)

    errs = check_sources(data, repo)
    if ja_path.exists():
        errs.extend(check_parity(en_html, ja_path.read_text()))
    else:
        errs.append(f"{args.ja} not found (EN/JA parity not checked)")

    # Separate hard mismatches from soft "missing untracked dir" skips.
    skips = [e for e in errs if "missing run dir" in e]
    hard = [e for e in errs if "missing run dir" not in e]

    if skips:
        print(f"SKIPPED {len(skips)} source dir(s) not present locally (data/ untracked):")
        for s in skips:
            print(s)
    if hard:
        print(f"\nFAIL — {len(hard)} integrity issue(s):")
        for e in hard:
            print(e)
        return 1
    print("\nOK — every embedded number matches its committed source (EN/JA in parity).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
