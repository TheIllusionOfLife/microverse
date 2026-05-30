"""SnapshotGuard — circuit breaker for the cold-backup snapshot path.

Snapshots are best-effort cold backups; the WAL is the durability
boundary. A persistent checkpoint failure (e.g. ``wal_checkpoint(TRUNCATE)``
raising SQLITE_IOERR on some macOS/APFS configurations) would otherwise
retry every snapshot interval for the life of a multi-day soak, spamming
tracebacks and perturbing the WAL/-shm so that ad-hoc reader connections
observe a stale ``MAX(ts)`` — a false stall. The guard trips after a few
consecutive failures and skips snapshots for the rest of the run.
"""

from __future__ import annotations

from microverse.world.snapshot import SnapshotGuard


def test_fresh_guard_is_enabled() -> None:
    g = SnapshotGuard()
    assert g.disabled is False
    assert g.consecutive_failures == 0


def test_breaker_trips_on_nth_consecutive_failure() -> None:
    g = SnapshotGuard(max_consecutive_failures=3)
    assert g.record_failure() is False  # 1
    assert g.disabled is False
    assert g.record_failure() is False  # 2
    assert g.disabled is False
    assert g.record_failure() is True  # 3 -> trips
    assert g.disabled is True


def test_success_resets_the_failure_streak() -> None:
    g = SnapshotGuard(max_consecutive_failures=3)
    g.record_failure()
    g.record_failure()
    g.record_success()
    assert g.consecutive_failures == 0
    assert g.disabled is False
    # Two more failures after a reset must NOT trip (streak restarted).
    g.record_failure()
    g.record_failure()
    assert g.disabled is False


def test_record_failure_is_idempotent_once_disabled() -> None:
    g = SnapshotGuard(max_consecutive_failures=2)
    g.record_failure()
    assert g.record_failure() is True
    assert g.disabled is True
    # Further failures after the trip return False (already tripped) and
    # do not keep incrementing unboundedly.
    assert g.record_failure() is False
    assert g.record_failure() is False
