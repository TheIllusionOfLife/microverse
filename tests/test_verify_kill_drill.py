"""Tests for scripts/verify_kill_drill.py.

The verifier is operator-facing tooling that proves the kill-safety
contract: under SIGKILL + restart, every committed pre-kill event must
survive (with at most one in-flight tick at the watermark itself
discarded). The watermark mode is what catches silent tail loss when
the restarted process appends fresh events at ids > watermark — the
post-restart appends cannot mask a missing pre-watermark prefix
because they're filtered out before the prefix check.

Coverage focuses on the watermark gate, since that is the part codex
flagged as security-critical for the kill-safety claim.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

_VERIFY_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_kill_drill.py"
_spec = importlib.util.spec_from_file_location("verify_kill_drill", _VERIFY_PATH)
assert _spec is not None
assert _spec.loader is not None
verify_kill_drill = importlib.util.module_from_spec(_spec)
sys.modules["verify_kill_drill"] = verify_kill_drill
_spec.loader.exec_module(verify_kill_drill)
verify = verify_kill_drill.verify


def _make_db(path: Path, ids: list[int]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE events ("
            "id INTEGER PRIMARY KEY, ts REAL, actor TEXT, "
            "action TEXT, target TEXT, payload_json TEXT)"
        )
        conn.executemany("INSERT INTO events (id) VALUES (?)", [(i,) for i in ids])
        conn.commit()
    finally:
        conn.close()


def test_db_missing_returns_failure(tmp_path: Path) -> None:
    rc = verify(tmp_path / "nope.sqlite")
    assert rc == 1


def test_empty_db_fails(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite"
    _make_db(db, [])
    assert verify(db) == 1


def test_contiguous_no_watermark_passes(tmp_path: Path) -> None:
    db = tmp_path / "ok.sqlite"
    _make_db(db, [1, 2, 3, 4, 5])
    assert verify(db) == 0


def test_gap_in_middle_fails_without_watermark(tmp_path: Path) -> None:
    db = tmp_path / "gap.sqlite"
    _make_db(db, [1, 2, 4, 5])
    assert verify(db) == 1


def test_watermark_zero_rejected(tmp_path: Path) -> None:
    """watermark=0 means no pre-kill events; the drill is meaningless."""
    db = tmp_path / "wm0.sqlite"
    _make_db(db, [1, 2, 3])
    assert verify(db, watermark=0) == 1


def test_watermark_negative_rejected(tmp_path: Path) -> None:
    db = tmp_path / "wmneg.sqlite"
    _make_db(db, [1, 2, 3])
    assert verify(db, watermark=-1) == 1


def test_watermark_strict_pass(tmp_path: Path) -> None:
    """All pre-watermark ids survived; no in-flight loss."""
    db = tmp_path / "strict.sqlite"
    _make_db(db, [1, 2, 3, 4, 5])
    assert verify(db, watermark=5) == 0


def test_watermark_lost_w_row_fails(tmp_path: Path) -> None:
    """W = MAX(id) was captured before SIGKILL, so id=W is by
    definition committed at kill time and MUST survive. Losing it
    is real data loss — not an acceptable in-flight discard. The
    in-flight tick is at id=W+1 and is filtered out by the
    `i <= W` predicate, so it never enters the prefix check."""
    db = tmp_path / "lost_w.sqlite"
    _make_db(db, [1, 2, 3, 4])  # id=5 (the watermark) was lost
    assert verify(db, watermark=5) == 1


def test_watermark_with_post_restart_appends_passes(tmp_path: Path) -> None:
    """The watermark filter excludes ids > W. Newly appended
    post-restart events do NOT count toward the prefix check."""
    db = tmp_path / "appended.sqlite"
    _make_db(db, [1, 2, 3, 4, 5, 6, 7, 8])  # W=5; 6,7,8 are post-restart
    assert verify(db, watermark=5) == 0


def test_watermark_silent_tail_loss_with_appends_fails(tmp_path: Path) -> None:
    """Codex's prescribed scenario: SIGKILL drops a committed tail
    event AND post-restart appends fresh events. Count alone would
    say 'recovered'; the watermark prefix check correctly catches
    that the pre-watermark prefix is incomplete.

    Constructed as id=1,2,3 then GAP at 4 then 5,6,7 — the gap is
    caught by contiguity. To test the watermark logic specifically
    (without a contiguity gap), see the next test."""
    db = tmp_path / "silent.sqlite"
    _make_db(db, [1, 2, 3, 5, 6, 7])  # missing id=4 (a tail-internal hole)
    assert verify(db, watermark=4) == 1


def test_watermark_pure_pre_kill_loss_caught(tmp_path: Path) -> None:
    """Hardest case: pre-kill ids 1..3 all lost, restart starts the
    id sequence over (or auto-increments past) — survivors are only
    post-watermark ids. The watermark filter would yield pre=[];
    without the W >= 2 guard on the in-flight branch this could
    have falsely matched expected_minus_tip=[]."""
    db = tmp_path / "wm1empty.sqlite"
    _make_db(db, [2, 3, 4])  # contiguous on the surviving range
    assert verify(db, watermark=1) == 1


def test_watermark_total_pre_kill_loss_at_w2(tmp_path: Path) -> None:
    """Watermark=2 with pre=[]: must FAIL even though [] would be
    list(range(1, 1)) for some smaller W."""
    db = tmp_path / "wm2empty.sqlite"
    _make_db(db, [3, 4, 5])
    assert verify(db, watermark=2) == 1


def test_watermark_count_recovery_does_not_mask_loss(tmp_path: Path) -> None:
    """Direct codex scenario: pre-kill W=5 (count=5). After kill,
    ids 4 and 5 are lost from the tail. Restart appends 3 new
    events — total count is back to 5, and a naive count check
    would say 'kill_drill_ok'. The watermark prefix check catches
    that pre-watermark ids are [1,2,3] not [1,2,3,4] or [1,2,3,4,5].

    Surviving id sequence here is constructed so it is contiguous
    (1,2,3,6,7,8) would have a gap; to isolate the watermark logic
    we use 1,2,3 only on the pre side and synthesize the appends
    as separate ids — but for a unit test we model the surviving
    rowset directly."""
    db = tmp_path / "count_recovery.sqlite"
    # Surviving ids: pre-kill {1,2,3}, post-restart {6,7,8}. The
    # gap between 3 and 6 is itself a contiguity violation, which
    # is already caught — so the watermark fail is a SECOND line
    # of defense. To test the watermark line in isolation we use
    # a contiguous-but-tail-truncated state:
    _make_db(db, [1, 2, 3])  # W=5 but only 1..3 survived
    assert verify(db, watermark=5) == 1
