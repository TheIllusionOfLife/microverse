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
from datetime import UTC, datetime
from pathlib import Path

_logger = logging.getLogger(__name__)

# SQLite extended error codes. Stable across SQLite versions.
# https://www.sqlite.org/rescode.html
_SQLITE_NOTADB = 26  # "file is not a database"


class SnapshotBusyError(RuntimeError):
    """Raised when wal_checkpoint(TRUNCATE) cannot complete cleanly.

    The caller should treat this as a transient failure (try again
    next interval) rather than archive a possibly-torn DB. Carrying a
    distinct exception type lets ``run.py``'s ``maybe_snapshot`` site
    catch this specifically and bump a metric without swallowing
    unrelated errors.
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
