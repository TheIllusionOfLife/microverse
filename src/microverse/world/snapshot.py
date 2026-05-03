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

import shutil
import tarfile
import threading
from datetime import UTC, datetime
from pathlib import Path

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
    """
    archive = Path(archive)
    data_dir = Path(data_dir)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive, "r:gz") as tar:
        # Python 3.12+ adds a 'data' filter that rejects suspicious paths.
        tar.extractall(path=str(data_dir), filter="data")


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
