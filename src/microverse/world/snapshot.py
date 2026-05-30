"""Cold-backup snapshots of the data dir.

Snapshots are NOT the durability mechanism — WAL on the SQLite event
log is. Snapshots exist for *catastrophic* recovery: a kernel panic,
a disk corruption, or a developer who fat-fingered ``rm`` on the data
dir. They are tar.gz archives written to a ``snapshots/`` directory,
named by UTC timestamp + a per-call counter so two snapshots in the
same second never collide.

API:
  - :func:`take_snapshot(data_dir, snapshots_dir)` → archive Path or None
    (None when ``data_dir`` doesn't exist yet).
  - :func:`restore_snapshot(archive, data_dir)` → wipes data_dir and
    extracts the archive in its place. Full overwrite — stale files
    are removed so a restore reproduces the snapshot's exact tree.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tarfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_logger = logging.getLogger(__name__)


@dataclass
class SnapshotGuard:
    """Circuit breaker for the cold-backup snapshot path.

    Snapshots are best-effort cold backups; the WAL is the durability
    boundary (see module docstring). A persistent failure mode — e.g.
    ``wal_checkpoint(TRUNCATE)`` raising SQLITE_IOERR on some
    macOS/APFS configurations — otherwise retries every snapshot
    interval for the life of the run. That floods the log with
    thousands of identical tracebacks AND, because each failed
    checkpoint perturbs the WAL/-shm sidecars, makes ad-hoc reader
    connections observe a stale ``MAX(ts)`` that reads as a false
    stall. After ``max_consecutive_failures`` consecutive failures the
    breaker trips and the caller skips snapshots for the rest of the
    run. A single successful snapshot resets the streak.

    Transient ``SnapshotBusyError`` (a competing writer) is NOT a hard
    failure and should not be fed to :meth:`record_failure` — it is
    expected contention that clears on its own.
    """

    max_consecutive_failures: int = 5
    consecutive_failures: int = 0
    disabled: bool = False

    def record_success(self) -> None:
        """Reset the failure streak after a clean snapshot."""
        self.consecutive_failures = 0

    def record_failure(self) -> bool:
        """Count one hard failure. Return True iff this call trips the
        breaker (transitions ``disabled`` False→True). Idempotent once
        disabled: further calls return False and do not increment."""
        if self.disabled:
            return False
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.disabled = True
            return True
        return False


# SQLite extended error codes. Stable across SQLite versions.
# https://www.sqlite.org/rescode.html
_SQLITE_NOTADB = 26  # "file is not a database"


class SnapshotBusyError(RuntimeError):
    """Raised when wal_checkpoint(TRUNCATE) cannot complete cleanly.

    The caller should treat this as a transient failure (try again
    next interval) rather than archive a possibly-torn DB. Carrying a
    distinct exception type lets ``run.py``'s snapshot site catch this
    specifically — bumping ``snapshot_skip_busy`` WITHOUT feeding the
    SnapshotGuard breaker — and avoids swallowing unrelated errors.
    """


def _checkpoint_wal(data_dir: Path) -> None:
    """Truncate the WAL of every ``*.sqlite`` file under ``data_dir``.

    Uses a short-lived ``sqlite3.connect`` per file with an explicit
    busy_timeout so a competing writer does not hang the snapshot
    indefinitely. SQLite's shared-memory protocol coordinates this
    side connection with the long-lived connections held by
    EpisodicMemory / Metrics / SemanticMemory, so the checkpoint
    sees the latest committed pages.

    Raises ``SnapshotBusyError`` if any DB reports busy or an
    incomplete checkpoint — caller must abort the snapshot rather
    than archive a possibly-torn file.
    """
    for db_path in sorted(data_dir.rglob("*.sqlite")):
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        row: tuple | None = None
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            try:
                row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            except sqlite3.DatabaseError as e:
                # Skip ONLY "not a database" (errcode 26 SQLITE_NOTADB):
                # files with a .sqlite suffix that aren't actually SQLite
                # (test fixtures, accidental writes). Real corruption
                # (SQLITE_CORRUPT etc.) must propagate so the operator
                # notices instead of silently producing a bad archive.
                if getattr(e, "sqlite_errorcode", None) == _SQLITE_NOTADB:
                    _logger.warning("not a SQLite database, skipping: %s", db_path)
                    continue
                raise
        finally:
            conn.close()
        # PRAGMA wal_checkpoint returns no rows on databases that aren't
        # in WAL journal mode. Skip those — they have no WAL to reclaim
        # and the tar will capture them consistently as-is.
        if row is None:
            continue
        # Row is (busy, log_pages, checkpointed_pages). Either a busy
        # signal (1) or a mismatch between log/checkpointed pages
        # means the WAL was not fully reclaimed — the archive would
        # capture an inconsistent state.
        busy, log_pages, checkpointed = row
        if busy != 0 or log_pages != checkpointed:
            raise SnapshotBusyError(
                f"wal_checkpoint(TRUNCATE) incomplete for {db_path}: "
                f"busy={busy} log_pages={log_pages} checkpointed={checkpointed}"
            )


# Per-process counter so rapid snapshots get unique names even within
# the same wall-clock second. Locked because the watchdog (Phase 4) may
# trigger snapshots concurrently with the tick loop.
_counter_lock = threading.Lock()
_counter = 0


def _next_seq() -> int:
    global _counter
    with _counter_lock:
        _counter += 1
        return _counter


def _archive_name(now: datetime, seq: int) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{seq:06d}.tar.gz"


def take_snapshot(data_dir: Path | str, snapshots_dir: Path | str) -> Path | None:
    """Snapshot ``data_dir`` to ``snapshots_dir/<ts>-<seq>.tar.gz``.

    Returns the archive path, or None when ``data_dir`` doesn't exist
    (a cold start has nothing to snapshot — that's not an error).

    If ``snapshots_dir`` lives inside ``data_dir`` (the conventional
    layout puts it at ``data_dir/snapshots/``), it is excluded from the
    archive so a snapshot can't recursively contain its older siblings.
    """
    data_dir = Path(data_dir).resolve()
    snapshots_dir = Path(snapshots_dir).resolve()
    if not data_dir.exists():
        return None

    # Reclaim the WAL of every SQLite file in data_dir BEFORE building
    # the archive. Otherwise tar may capture main + -wal + -shm in a
    # state where the WAL holds frames that haven't migrated to the
    # main file, and a restore can present a torn DB. If the
    # checkpoint cannot complete (a competing writer is busy), abort
    # rather than ship an unsafe archive — the caller bumps a metric
    # and tries again next interval.
    _checkpoint_wal(data_dir)

    snapshots_dir.mkdir(parents=True, exist_ok=True)
    name = _archive_name(datetime.now(UTC), _next_seq())
    archive = snapshots_dir / name

    # Determine whether snapshots_dir is inside data_dir; if so, set up
    # a tarfile filter that drops it from the archive. ``filter=`` on
    # ``tar.add`` receives each TarInfo and returns it (keep) or None
    # (skip).
    excluded_relpath: str | None = None
    try:
        rel = snapshots_dir.relative_to(data_dir)
        # tarinfo.name uses POSIX separators regardless of OS, so build
        # the comparison string the same way (rel.as_posix() vs str(rel)
        # only matters on Windows but the discipline is free).
        excluded_relpath = "./" + rel.as_posix()
    except ValueError:
        excluded_relpath = None

    def _exclude_self(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if excluded_relpath and (
            tarinfo.name == excluded_relpath or tarinfo.name.startswith(excluded_relpath + "/")
        ):
            return None
        return tarinfo

    # Build to a temp file then atomic-replace so a partial archive is
    # never visible to a future restore. Clean up the .tmp on any
    # failure so a half-built archive doesn't linger.
    tmp = archive.with_suffix(archive.suffix + ".tmp")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            # arcname='.' so the archive's contents are *the data dir*
            # (no nested top-level dir) — restore extracts straight in.
            tar.add(str(data_dir), arcname=".", filter=_exclude_self)
        tmp.replace(archive)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return archive


def restore_snapshot(archive: Path | str, data_dir: Path | str) -> None:
    """Wipe ``data_dir`` and extract ``archive`` in its place.

    Full overwrite: stale files left in ``data_dir`` (after the snapshot
    was taken) are removed so the restored tree matches snapshot time
    byte-for-byte.

    Atomic: extract to a sibling tmp dir first, then swap. If extract
    fails midway, the original ``data_dir`` (or its absence) is
    preserved — no half-extracted partial state.
    """
    archive = Path(archive).resolve()
    data_dir = Path(data_dir).resolve()
    parent = data_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / (data_dir.name + ".restore.tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        with tarfile.open(archive, "r:gz") as tar:
            # Python 3.12+ adds a 'data' filter that rejects suspicious paths.
            tar.extractall(path=str(staging), filter="data")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Atomic swap: rename data_dir to a backup, replace with staging,
    # then drop the backup. If staging.replace fails, restore the
    # backup so the user is never left with no data_dir.
    backup: Path | None = None
    if data_dir.exists():
        backup = parent / (data_dir.name + ".restore.bak")
        if backup.exists():
            shutil.rmtree(backup)
        data_dir.replace(backup)
    try:
        staging.replace(data_dir)
    except BaseException:
        if backup is not None:
            backup.replace(data_dir)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def maybe_snapshot(
    tick: int,
    *,
    interval: int,
    data_dir: Path | str,
    snapshots_dir: Path | str,
) -> Path | None:
    """Snapshot when ``tick % interval == 0`` (excluding tick 0).

    Returns the archive path on snapshot, None otherwise. Lets the tick
    loop call this every iteration without scattering bookkeeping.
    """
    if interval <= 0 or tick == 0 or tick % interval != 0:
        return None
    return take_snapshot(data_dir, snapshots_dir)


def prune_snapshots(
    snapshots_dir: Path | str,
    *,
    max_count: int | None,
    max_bytes: int | None,
) -> list[Path]:
    """Drop oldest archives until both bounds hold; never delete newest.

    Archive filenames follow ``YYYYMMDDTHHMMSSZ-NNNNNN.tar.gz``, so a
    lexical sort is also a time sort. ``*.tar.gz.tmp`` files are
    in-flight writes — never counted, never deleted.

    Invariants:
      - When over ``max_count``, the oldest archives are deleted until
        the count fits.
      - When over ``max_bytes``, oldest are deleted (after count prune)
        until the remaining total fits.
      - The single newest archive always survives, even when the byte
        cap is below its size. Otherwise prune could orphan a snapshot
        mid-restore-prep.

    Returns the list of paths that were deleted.
    """
    snapshots_dir = Path(snapshots_dir)
    if not snapshots_dir.exists():
        return []

    # Reject negative bounds: a negative cap would over-prune (None or
    # 0 are the documented "no cap" / "drop everything possible" cases).
    if max_count is not None and max_count < 0:
        raise ValueError(f"max_count must be >= 0, got {max_count}")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError(f"max_bytes must be >= 0, got {max_bytes}")

    archives = sorted(snapshots_dir.glob("*.tar.gz"))
    if not archives:
        return []

    # Oldest-first ordering for prune candidacy; pop from the front.
    candidates = list(archives)
    deleted: list[Path] = []

    if max_count is not None and len(candidates) > max_count:
        n_to_drop = len(candidates) - max_count
        for _ in range(n_to_drop):
            if len(candidates) <= 1:  # newest is sacred
                break
            victim = candidates.pop(0)
            victim.unlink(missing_ok=True)
            deleted.append(victim)

    if max_bytes is not None:
        # After count prune, drop oldest until total under cap. The
        # newest archive is preserved even if it alone exceeds the cap.
        # Keep a running total so we don't re-stat the survivors every
        # iteration (Gemini PR review on #38 flagged the original
        # O(N^2) shape — switch to O(N)).
        sizes = {p: (p.stat().st_size if p.exists() else 0) for p in candidates}
        current_total = sum(sizes.values())
        while len(candidates) > 1 and current_total > max_bytes:
            victim = candidates.pop(0)
            current_total -= sizes.get(victim, 0)
            victim.unlink(missing_ok=True)
            deleted.append(victim)

    return deleted
