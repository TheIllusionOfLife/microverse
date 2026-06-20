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
* EN and JA pages must carry a semantically identical data block (same parsed values,
  key-order-insensitive), the same localized-strings key structure, the same citation
  links, and the same section anchors, so the two language versions cannot silently drift.

``data/`` is untracked, so the gate-report cross-check needs the local run dirs. By default a
missing run dir (or a missing JA page) is a **hard failure** — the checker refuses to report
success when it verified nothing. Pass ``--allow-missing-data`` to run the structure/parity
checks alone (e.g. a fresh clone) and demote absent run dirs to warnings. The pure
parse/compare logic is covered offline by ``tests/test_check_results_data.py``. Run before
opening or updating the writeup PR::

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
_STRINGS_RE = re.compile(
    r'<script id="microverse-strings" type="application/json">(.*?)</script>',
    re.DOTALL,
)
# Source-trail citations: both languages must point at the same docs/ADRs.
_CITE_RE = re.compile(r'<a[^>]*class="cite"[^>]*href="([^"]+)"')
# Section anchors the page JS and in-page nav depend on; both languages must carry them.
_REQUIRED_ANCHORS = ("dose", "specialization")
# Tag shared by read_seed_metric() and main() so skip-classification has one source of truth.
_MISSING_DIR = "missing run dir"


def extract_data(html: str) -> dict:
    """Parse the embedded ``microverse-data`` JSON block out of an HTML page."""
    m = _DATA_RE.search(html)
    if not m:
        raise ValueError("microverse-data script block not found")
    return json.loads(m.group(1))


def extract_strings(html: str) -> dict:
    """Parse the embedded ``microverse-strings`` (localized prose) JSON block."""
    m = _STRINGS_RE.search(html)
    if not m:
        raise ValueError("microverse-strings script block not found")
    return json.loads(m.group(1))


def _shape(obj: object) -> object:
    """A value's key/structure skeleton, ignoring leaf text (so prose may differ)."""
    if isinstance(obj, dict):
        return {k: _shape(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_shape(v) for v in obj]
    return None


def _chosen(repo: Path, dir_name: str) -> dict:
    report = json.loads((repo / dir_name / "gate-report.json").read_text(encoding="utf-8"))
    return report["gate_9_verb_diversity"]["chosen"]


def read_seed_metric(
    repo: Path, dir_name: str, metric: str, expected: float
) -> tuple[float | None, str | None]:
    """Re-read one run dir's chosen-stream ``metric`` and compare to ``expected``.

    Returns ``(actual, None)`` on match, ``(actual, message)`` on mismatch, and
    ``(None, message)`` when the run dir is absent (the message carries ``_MISSING_DIR``)
    or its gate-report.json is unreadable (a hard error, not a skip).
    """
    try:
        chosen = _chosen(repo, dir_name)
    except FileNotFoundError:
        return None, f"  - {dir_name}: {_MISSING_DIR} (data/ untracked; cannot verify {expected})"
    except (json.JSONDecodeError, KeyError) as exc:
        return None, f"  - {dir_name}: unreadable gate-report.json ({exc!r})"
    actual = chosen.get(metric)
    if actual is None:
        return None, f"  - {dir_name}: chosen.{metric} absent in gate-report.json"
    if abs(float(actual) - expected) > TOL:
        return float(actual), f"  - {dir_name}: page {expected} vs source {metric}={actual}"
    return float(actual), None


def check_dose_mean(entry: dict) -> list[str]:
    """Recompute a dose entry's mean from its per-seed values (raw, not pre-rounded)."""
    seeds = entry["seeds"]
    mean = sum(seeds) / len(seeds)  # raw: TOL absorbs the page's 4-dp display rounding only
    if abs(mean - entry["mean"]) > TOL:
        label = entry.get("dose") or entry.get("key") or "?"
        return [f"  - dose {label}: mean {entry['mean']} vs computed {mean:.5f}"]
    return []


def _check_agent_shares(repo: Path, spec: dict, label: str) -> list[str]:
    errs: list[str] = []
    val, err = read_seed_metric(repo, spec["dir"], "mean_pairwise_jsd", spec["mpj"])
    if err:
        errs.append(f"{label} mpj:{err}")
        if val is None:
            return errs  # missing/unreadable dir: skip per-agent (already reported)
    try:
        shares = _chosen(repo, spec["dir"])["per_agent_verb_share"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return errs
    for agent, verbs in spec["agents"].items():
        for verb, page_val in verbs.items():
            actual = shares.get(agent, {}).get(verb)
            if actual is None or abs(float(actual) - page_val) > TOL:
                errs.append(f"  - {label} {agent}.{verb}: page {page_val} vs source {actual}")
    return errs


def _doc_has(repo: Path, doc: str, value: float) -> bool:
    text = (repo / doc).read_text(encoding="utf-8")
    # Match the value as written (e.g. 0.156, 0.88) ignoring trailing zeros, with digit
    # boundaries so 0.26 does not spuriously match 0.265 or 10.26.
    needle = f"{value:.4f}".rstrip("0").rstrip(".")
    return re.search(r"(?<!\d)" + re.escape(needle) + r"(?!\d)", text) is not None


def check_sources(data: dict, repo: Path) -> list[str]:
    """Cross-check every embedded number against its committed source."""
    errs: list[str] = []

    for entry in data["dose"]:
        errs.extend(check_dose_mean(entry))
        for dir_name, seed_val in zip(entry["dirs"], entry["seeds"], strict=True):
            _, err = read_seed_metric(repo, dir_name, entry["metric"], seed_val)
            if err:
                errs.append(f"dose {entry['size']}res {entry['dose']}:\n{err}")

    spec = data["specialization"]
    errs.extend(_check_agent_shares(repo, spec["before"], "specialization.before"))
    errs.extend(_check_agent_shares(repo, spec["after"], "specialization.after"))

    for lever in data["levers"]:
        if "seeds" in lever and "dirs" in lever:
            errs.extend(check_dose_mean({**lever, "dose": lever["key"]}))
            for dir_name, seed_val in zip(lever["dirs"], lever["seeds"], strict=True):
                _, err = read_seed_metric(repo, dir_name, lever["metric"], seed_val)
                if err:
                    errs.append(f"lever {lever['key']}:\n{err}")
        elif "dir" in lever:
            # single-dir levers report the after / wash value under their metric
            target = lever.get("after", lever.get("value"))
            _, err = read_seed_metric(repo, lever["dir"], lever["metric"], target)
            if err:
                errs.append(f"lever {lever['key']}:\n{err}")

    # Doc-only values: assert they appear verbatim in the cited findings doc. A missing
    # cited doc is itself an error (a broken source trail), not a silent skip.
    mono = data["monoculture"]
    if not (repo / mono["doc"]).exists():
        errs.append(f"monoculture: cited doc not found: {mono['doc']}")
    else:
        for value in (*mono["contribute_no_economy"], *mono["contribute_with_economy"]):
            if not _doc_has(repo, mono["doc"], value):
                errs.append(f"monoculture: {value} not found in {mono['doc']}")
    persona = next((lv for lv in data["levers"] if lv.get("key") == "persona"), None)
    if persona:
        if not (repo / persona["doc"]).exists():
            errs.append(f"persona: cited doc not found: {persona['doc']}")
        else:
            for value in (persona["travel_before"], persona["travel_after"]):
                if not _doc_has(repo, persona["doc"], value):
                    errs.append(f"persona travel {value} not found in {persona['doc']}")

    return errs


def check_parity(en_html: str, ja_html: str) -> list[str]:
    """Assert the EN and JA pages share data, string shape, citations, and anchors.

    The data block must be semantically identical (same parsed values; key order and
    whitespace are irrelevant). The localized strings block may differ in text but must
    share the exact same key structure (so a missing or renamed key can't break one
    language). Both pages must cite the same source URLs and carry the required anchors.
    """
    errs: list[str] = []
    if extract_data(en_html) != extract_data(ja_html):
        errs.append("EN/JA data block diverged (the embedded numbers must match)")
    try:
        if _shape(extract_strings(en_html)) != _shape(extract_strings(ja_html)):
            errs.append(
                "EN/JA strings block key structure diverged (localized prose must share keys)"
            )
    except ValueError as exc:
        errs.append(f"strings block: {exc}")
    if sorted(_CITE_RE.findall(en_html)) != sorted(_CITE_RE.findall(ja_html)):
        errs.append("EN/JA citation hrefs diverged (both pages must cite the same sources)")
    for anchor in _REQUIRED_ANCHORS:
        token = f'id="{anchor}"'
        en_has, ja_has = token in en_html, token in ja_html
        if not en_has or not ja_has:
            if not en_has and not ja_has:
                where = "both pages"
            else:
                where = "results.ja.html" if en_has else "results.html"
            errs.append(f"section anchor #{anchor} missing from {where}")
    return errs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--en", default="results.html", help="English page (default: results.html)")
    parser.add_argument(
        "--ja", default="results.ja.html", help="Japanese page (default: results.ja.html)"
    )
    parser.add_argument("--repo", default=str(REPO_ROOT), help="repo root for source dirs")
    parser.add_argument(
        "--allow-missing-data",
        action="store_true",
        help="demote absent run dirs / JA page to warnings (default: fail closed)",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    en_path, ja_path = repo / args.en, repo / args.ja
    en_html = en_path.read_text(encoding="utf-8")
    data = extract_data(en_html)

    errs = check_sources(data, repo)
    ja_missing = not ja_path.exists()
    if not ja_missing:
        errs.extend(check_parity(en_html, ja_path.read_text(encoding="utf-8")))

    # Absent run dirs and an absent JA page are "could not verify", not "verified wrong".
    # Fail closed by default so the checker never reports success having verified nothing;
    # --allow-missing-data demotes them to warnings for structure-only runs (e.g. CI clone).
    missing = [e for e in errs if _MISSING_DIR in e]
    hard = [e for e in errs if _MISSING_DIR not in e]
    if ja_missing:
        missing.append(f"  - {args.ja} not found (EN/JA parity not checked)")

    if missing and not args.allow_missing_data:
        hard.extend(missing)
        missing = []

    if missing:
        print(f"WARNING — {len(missing)} source(s) absent (not verified; --allow-missing-data):")
        for m in missing:
            print(m)
    if hard:
        print(f"\nFAIL — {len(hard)} integrity issue(s):")
        for e in hard:
            print(e)
        return 1
    if args.allow_missing_data and missing:
        print("\nOK — page structure + EN/JA parity valid (run dirs absent; not cross-checked).")
    else:
        print("\nOK — every embedded number matches its committed source (EN/JA in parity).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
