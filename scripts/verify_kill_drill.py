#!/usr/bin/env python3
"""Verify the kill-safety contract on an existing data dir.

Reads ``data/episodic.sqlite`` and checks:
  - All event ids are strictly increasing.
  - No duplicate ids.
  - Row count > 0.

This is the post-soak verification for the SIGKILL drill — after a
``kill -9`` of the run subprocess and a clean restart, this script
confirms the WAL recovery preserved the committed-event invariant.
Prints ``kill_drill_ok`` and exits 0 on success.

Usage::

    uv run python scripts/verify_kill_drill.py --db data/episodic.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def verify(db: Path) -> int:
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
    # Contiguity: SQLite AUTOINCREMENT only re-uses ids on rollback,
    # so any gap means a committed event was lost. The first id need
    # not be 1 (a snapshot restore can carry a higher floor) but the
    # set must be every integer from min..max with no holes.
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
    print(  # noqa: T201
        f"kill_drill_ok ({len(ids)} events, ids {ids[0]}..{ids[-1]}, contiguous)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, type=Path)
    args = p.parse_args(argv)
    return verify(args.db)


if __name__ == "__main__":
    sys.exit(main())
