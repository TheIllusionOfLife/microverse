#!/usr/bin/env python3
"""Verify the kill-safety contract on an existing data dir.

Reads ``data/episodic.sqlite`` and checks:
  - Row count > 0.
  - All event ids are unique and contiguous (no gaps in the surviving
    id sequence — a committed event silently dropping out of the
    middle would be visible as a hole).
  - When ``--watermark W`` is supplied: every id in ``1..W`` must
    still exist after the restart. ``W`` is captured as
    ``SELECT MAX(id)`` *before* the SIGKILL, which means by
    definition it is the highest committed event at kill time —
    therefore id ``W`` itself must survive. (The in-flight tick
    discarded by SIGKILL is at id ``W+1``, which the watermark
    filter ``id <= W`` excludes regardless.) A count-based check
    is NOT sufficient: if SIGKILL drops the tail and the restarted
    process then appends fresh events, the count recovers but the
    original tail is gone — a hole the raw count never sees.

Recommended SIGKILL drill flow:

    # Before the kill — capture the pre-kill high-watermark.
    W=$(sqlite3 data/episodic.sqlite 'SELECT COALESCE(MAX(id), 0) FROM events')

    # Send SIGKILL, restart the run, then:
    uv run python scripts/verify_kill_drill.py \\
        --db data/episodic.sqlite --watermark "$W"

Without ``--watermark`` the script only proves event-log internal
integrity (no gaps, no duplicates), not zero tail loss.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def verify(
    db: Path,
    watermark: int | None = None,
    *,
    check_scenes: bool = False,
) -> int:
    if not db.exists():
        print(f"db not found: {db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(db))
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM events ORDER BY id ASC")]
        scene_rows = (
            list(
                conn.execute(
                    "SELECT id, action, payload_json FROM events "
                    "WHERE action IN ('scene.open','scene.abort','contribute') "
                    "ORDER BY id ASC"
                )
            )
            if check_scenes
            else []
        )
    finally:
        conn.close()

    if not ids:
        print("kill_drill_FAIL: zero events in db", file=sys.stderr)
        return 1
    # Contiguity: any gap means a committed event was lost from the
    # *middle* of the sequence. (Tail loss is invisible here; that's
    # what --watermark is for.)
    expected = list(range(ids[0], ids[-1] + 1))
    if ids != expected:
        missing = sorted(set(expected) - set(ids))[:10]
        print(
            f"kill_drill_FAIL: gap in id sequence; missing first 10: {missing}",
            file=sys.stderr,
        )
        return 1
    if len(set(ids)) != len(ids):
        print("kill_drill_FAIL: duplicate ids", file=sys.stderr)
        return 1
    # Tail-loss check: only meaningful when the operator captured a
    # pre-kill high-watermark MAX(id). We restrict the check to ids
    # <= watermark so newly appended post-restart events cannot
    # mask a missing pre-kill tail. Allow the single in-flight tick
    # at the watermark itself to have been discarded.
    if watermark is not None:
        if watermark < 1:
            print(
                f"kill_drill_FAIL: invalid watermark {watermark} (must be >= 1; "
                "the drill requires at least one committed pre-kill event)",
                file=sys.stderr,
            )
            return 1
        pre = [i for i in ids if i <= watermark]
        # W = MAX(id) was captured BEFORE SIGKILL, so id=W is by
        # definition committed and MUST survive. Losing it would be
        # real data loss, not an acceptable "in-flight" case (the
        # in-flight tick is at id=W+1 and is filtered out by the
        # `i <= watermark` predicate above). Therefore the only
        # passing state is the full prefix 1..W.
        #
        # Assumption: the events table uses INTEGER PRIMARY KEY
        # autoincrementing from 1 (see microverse.memory.episodic),
        # so the canonical pre-watermark prefix starts at id=1. A
        # database whose ids start higher would have its pre-prefix
        # marked missing, which is the correct outcome — the
        # kill-drill contract is specifically about 1..W.
        expected_full = list(range(1, watermark + 1))
        if pre == expected_full:
            tail_note = f", all 1..{watermark} survived (pre-kill watermark {watermark})"
        else:
            missing = sorted(set(expected_full) - set(pre))[:10]
            print(
                f"kill_drill_FAIL: tail loss — pre-watermark ids missing "
                f"(first 10): {missing} (pre-kill watermark={watermark})",
                file=sys.stderr,
            )
            return 1
    else:
        tail_note = ", tail-loss check SKIPPED (no --watermark)"

    # Scene-boundary integrity (ADR 0005 D3, gate 6). Every scene.open
    # must be followed by either all expected contributes, OR a
    # scene.abort with the same scene_id. An orphan scene.open with
    # neither is a kill-safety violation: the scene's authors were
    # logged but no fragment landed AND no abort marker either.
    scene_note = ""
    if check_scenes:
        import json as _json

        opens: dict[str, dict] = {}
        contribs: dict[str, set[int]] = {}
        aborts: set[str] = set()
        for _id, action, payload_json in scene_rows:
            try:
                p = _json.loads(payload_json or "{}")
            except _json.JSONDecodeError:
                continue
            sid = p.get("scene_id")
            if not sid:
                continue
            if action == "scene.open":
                opens[sid] = p
            elif action == "scene.abort":
                aborts.add(sid)
            elif action == "contribute":
                ti = p.get("turn_index")
                if ti in (1, 2, 3):
                    contribs.setdefault(sid, set()).add(int(ti))
        orphans: list[str] = []
        for sid in opens:
            if sid in aborts:
                continue
            if contribs.get(sid):
                continue
            # Open with no contribute AND no abort. Orphan.
            orphans.append(sid)
        if orphans:
            print(
                f"kill_drill_FAIL: orphan scene.open without contributes or abort: {orphans[:10]}",
                file=sys.stderr,
            )
            return 1
        scene_note = (
            f", scenes opened={len(opens)} aborted={len(aborts)} "
            f"partial={sum(1 for v in contribs.values() if 0 < len(v) < 3)}"
        )

    print(
        f"kill_drill_ok ({len(ids)} events, ids {ids[0]}..{ids[-1]}, contiguous"
        f"{tail_note}{scene_note})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, type=Path)
    p.add_argument(
        "--watermark",
        type=int,
        default=None,
        help="Pre-kill high-watermark MAX(id). Every id in 1..W must survive. "
        "The in-flight tick (at id=W+1) may be discarded by SIGKILL but the "
        "watermark predicate filters it out before the prefix check.",
    )
    p.add_argument(
        "--scene-boundary",
        choices=[
            "pre-open",
            "open-to-turn1",
            "turn1-to-turn2",
            "turn2-to-turn3",
            "post-turn3-pre-accept",
            "any",
        ],
        default=None,
        help="When set, also verify scene-event integrity: every "
        "scene.open must be followed by all expected contributes or a "
        "scene.abort with the same scene_id. The choice tags the "
        "boundary at which the kill was simulated (informational only "
        "for the operator's notes — the integrity rule is the same).",
    )
    args = p.parse_args(argv)
    return verify(
        args.db,
        watermark=args.watermark,
        check_scenes=args.scene_boundary is not None,
    )


if __name__ == "__main__":
    sys.exit(main())
