#!/usr/bin/env python3
"""Verify the kill-safety contract on an existing data dir.

Reads ``data/episodic.sqlite`` and checks:
  - Row count > 0.
  - All event ids are unique and contiguous (no gaps in the surviving
    id sequence — a committed event silently dropping out of the
    middle would be visible as a hole).
  - When ``--watermark W`` is supplied: every id in ``1..W`` still
    exists after the restart (one in-flight discard at id ``W``
    itself is allowed, so the surviving prefix may be ``1..W`` or
    ``1..W-1``). This is the only check that proves "zero tail
    loss". A count-based check is NOT sufficient: if SIGKILL drops
    the tail and the restarted process then appends fresh events,
    the count recovers but the original tail is gone — a hole the
    raw count never sees.

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
        # Allow exactly one missing id, and only if it is the
        # watermark itself (the in-flight tick the kill discarded).
        # Anything else missing in 1..watermark is silent tail loss.
        # The in-flight branch requires watermark >= 2 so the empty
        # prefix can't masquerade as a valid "1..0 survived" state
        # when every pre-kill event was actually lost.
        expected_full = list(range(1, watermark + 1))
        expected_minus_tip = list(range(1, watermark))  # 1..W-1
        if pre == expected_full:
            tail_note = f", all 1..{watermark} survived (pre-kill watermark {watermark})"
        elif watermark >= 2 and pre == expected_minus_tip:
            tail_note = (
                f", 1..{watermark - 1} survived; in-flight id "
                f"{watermark} discarded (pre-kill watermark {watermark})"
            )
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
        help="Pre-kill high-watermark MAX(id). Every id in 1..W must survive "
        "(or 1..W-1 if the in-flight tick at the watermark was discarded).",
    )
    args = p.parse_args(argv)
    return verify(args.db, watermark=args.watermark)


if __name__ == "__main__":
    sys.exit(main())
