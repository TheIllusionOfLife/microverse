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


def verify(db: Path, watermark: int | None = None) -> int:
    if not db.exists():
        print(f"db not found: {db}", file=sys.stderr)  # noqa: T201
        return 1
    conn = sqlite3.connect(str(db))
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM events ORDER BY id ASC")]
    finally:
        conn.close()

    if not ids:
        print("kill_drill_FAIL: zero events in db", file=sys.stderr)  # noqa: T201
        return 1
    # Contiguity: any gap means a committed event was lost from the
    # *middle* of the sequence. (Tail loss is invisible here; that's
    # what --watermark is for.)
    expected = list(range(ids[0], ids[-1] + 1))
    if ids != expected:
        missing = sorted(set(expected) - set(ids))[:10]
        print(  # noqa: T201
            f"kill_drill_FAIL: gap in id sequence; missing first 10: {missing}",
            file=sys.stderr,
        )
        return 1
    if len(set(ids)) != len(ids):
        print("kill_drill_FAIL: duplicate ids", file=sys.stderr)  # noqa: T201
        return 1
    # Tail-loss check: only meaningful when the operator captured a
    # pre-kill high-watermark MAX(id). We restrict the check to ids
    # <= watermark so newly appended post-restart events cannot
    # mask a missing pre-kill tail. Allow the single in-flight tick
    # at the watermark itself to have been discarded.
    if watermark is not None:
        if watermark < 1:
            print(  # noqa: T201
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
        expected_full = list(range(1, watermark + 1))
        if pre == expected_full:
            tail_note = f", all 1..{watermark} survived (pre-kill watermark {watermark})"
        else:
            missing = sorted(set(expected_full) - set(pre))[:10]
            print(  # noqa: T201
                f"kill_drill_FAIL: tail loss — pre-watermark ids missing "
                f"(first 10): {missing} (pre-kill watermark={watermark})",
                file=sys.stderr,
            )
            return 1
    else:
        tail_note = ", tail-loss check SKIPPED (no --watermark)"
    print(  # noqa: T201
        f"kill_drill_ok ({len(ids)} events, ids {ids[0]}..{ids[-1]}, contiguous{tail_note})"
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
    args = p.parse_args(argv)
    return verify(args.db, watermark=args.watermark)


if __name__ == "__main__":
    sys.exit(main())
