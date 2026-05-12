# ruff: noqa: T201  # measurement utility — print is the output
"""Phase 0 halt-criterion measurement.

Reads each ``data/soak-spike-<arm>/episodic.sqlite`` and
``harvest/soak-spike-<arm>/manifest.jsonl`` and prints per-arm:

  * Aki craft-share (fraction of Aki's actions that are ``craft``)
  * artifact median length in words (over accepted harvest entries)

Halt criterion fires if, across arms B / C / D:

  - Aki craft-share does NOT drop below 75% in at least one arm AND
  - Artifact median length does NOT rise above 25 words in at least one arm

In that case the workshop mechanism claim is falsified and the v0.2
plan must be replanned (model-swap or persona-only).
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from pathlib import Path

ARMS = ("A", "B", "C", "D", "E")
COMPARE_ARMS = ("B", "C", "D")
CRAFT_SHARE_HALT = 0.75
LENGTH_HALT_WORDS = 25


def _aki_craft_share(ep_path: Path) -> tuple[float, int] | None:
    if not ep_path.exists():
        return None
    conn = sqlite3.connect(str(ep_path))
    try:
        rows = conn.execute(
            "SELECT action FROM events WHERE actor=?", ("Aki",)
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    total = len(rows)
    crafts = sum(1 for (a,) in rows if a == "craft")
    return crafts / total, total


def _artifact_word_lengths(harvest_dir: Path) -> list[int]:
    manifest = harvest_dir / "manifest.jsonl"
    if not manifest.exists():
        return []
    lengths: list[int] = []
    inbox = harvest_dir / "inbox"
    with open(manifest, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not rec.get("accepted"):
                continue
            path = rec.get("path")
            if not path:
                continue
            artifact_path = harvest_dir / path
            if not artifact_path.exists():
                continue
            text = artifact_path.read_text(encoding="utf-8", errors="replace")
            body = text.split("---\n", 2)[-1] if text.startswith("---\n") else text
            lengths.append(len(body.split()))
    # Inbox-only fallback (manifest missing path field on older runs).
    if not lengths and inbox.exists():
        for md in inbox.rglob("*.md"):
            text = md.read_text(encoding="utf-8", errors="replace")
            body = text.split("---\n", 2)[-1] if text.startswith("---\n") else text
            lengths.append(len(body.split()))
    return lengths


def main() -> int:
    rows = []
    for arm in ARMS:
        ep = Path(f"data/soak-spike-{arm}/episodic.sqlite")
        harvest = Path(f"harvest/soak-spike-{arm}")
        cs = _aki_craft_share(ep)
        lens = _artifact_word_lengths(harvest)
        rows.append((arm, cs, lens))

    print(f"{'arm':>3}  {'aki actions':>12}  {'craft share':>12}  "
          f"{'artifacts':>10}  {'median words':>12}")
    for arm, cs, lens in rows:
        if cs is None:
            print(f"{arm:>3}  {'no data':>12}  {'-':>12}  {len(lens):>10}  -")
            continue
        share, total = cs
        median = statistics.median(lens) if lens else 0
        print(f"{arm:>3}  {total:>12}  {share:>12.0%}  "
              f"{len(lens):>10}  {median:>12.1f}")

    print()
    print("=== Halt criterion (Phase 0) ===")
    arm_to_data = {arm: (cs, lens) for arm, cs, lens in rows}
    any_share_drops = False
    any_length_rises = False
    for arm in COMPARE_ARMS:
        cs, lens = arm_to_data.get(arm, (None, []))
        if cs is None:
            print(f"  {arm}: no data — cannot evaluate.")
            continue
        share, _ = cs
        median = statistics.median(lens) if lens else 0
        if share < CRAFT_SHARE_HALT:
            any_share_drops = True
        if median > LENGTH_HALT_WORDS:
            any_length_rises = True
    print(f"  any arm B/C/D drops Aki craft-share < {CRAFT_SHARE_HALT:.0%}: "
          f"{'YES' if any_share_drops else 'NO'}")
    print(f"  any arm B/C/D raises artifact median > {LENGTH_HALT_WORDS} words: "
          f"{'YES' if any_length_rises else 'NO'}")
    print()
    if any_share_drops and any_length_rises:
        print("PROCEED — mechanism claim is supported. Ship Phases 1-7.")
        return 0
    if any_share_drops or any_length_rises:
        print("PARTIAL — one signal but not both. Reconsider before committing v0.2.")
        return 0
    print("HALT — neither signal fired. Mechanism claim falsified. "
          "Replan with model-swap or persona-only routes from ADR 0002.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
