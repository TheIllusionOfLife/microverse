"""Cold-backup snapshot roundtrip.

Snapshots are NOT the recovery path — WAL is. Snapshots exist only for
catastrophic-corruption rollback (e.g. a kernel panic that leaves the
SQLite file unrecoverable). The contract: snapshot the data dir at a
point in time; later, wipe and restore from the snapshot, get back the
exact byte-state captured.
"""

from __future__ import annotations

from pathlib import Path

from microverse.world.snapshot import restore_snapshot, take_snapshot


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_snapshot_creates_tar_gz_in_snapshots_dir(tmp_path: Path):
    data_dir = tmp_path / "data"
    snapshots_dir = tmp_path / "snapshots"
    _write(data_dir / "episodic.sqlite", "fake-db-content")

    archive = take_snapshot(data_dir, snapshots_dir)

    assert archive.exists()
    assert archive.suffix == ".gz"
    assert archive.parent == snapshots_dir


def test_restore_overwrites_existing_data_dir(tmp_path: Path):
    data_dir = tmp_path / "data"
    snapshots_dir = tmp_path / "snapshots"
    _write(data_dir / "episodic.sqlite", "snapshot-time")
    _write(data_dir / "metrics.sqlite", "ALPHA")
    _write(data_dir / "subdir/file.txt", "leaf")

    archive = take_snapshot(data_dir, snapshots_dir)

    # Mutate the data dir after snapshot.
    _write(data_dir / "episodic.sqlite", "post-snapshot mutation")
    _write(data_dir / "metrics.sqlite", "BETA")
    _write(data_dir / "stale-extra.txt", "should be cleared")

    restore_snapshot(archive, data_dir)

    assert (data_dir / "episodic.sqlite").read_text() == "snapshot-time"
    assert (data_dir / "metrics.sqlite").read_text() == "ALPHA"
    assert (data_dir / "subdir/file.txt").read_text() == "leaf"
    # Stale extra files are removed — restore is full overwrite.
    assert not (data_dir / "stale-extra.txt").exists()


def test_restore_recreates_data_dir_if_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    snapshots_dir = tmp_path / "snapshots"
    _write(data_dir / "episodic.sqlite", "captured")
    archive = take_snapshot(data_dir, snapshots_dir)

    # Wipe data_dir entirely, then restore.
    import shutil

    shutil.rmtree(data_dir)
    restore_snapshot(archive, data_dir)

    assert (data_dir / "episodic.sqlite").read_text() == "captured"


def test_take_snapshot_filename_is_unique_per_call(tmp_path: Path):
    """Two snapshots in quick succession must not collide. The
    timestamp suffix has enough resolution OR a counter handles it."""
    data_dir = tmp_path / "data"
    snapshots_dir = tmp_path / "snapshots"
    _write(data_dir / "f", "1")

    a = take_snapshot(data_dir, snapshots_dir)
    b = take_snapshot(data_dir, snapshots_dir)

    assert a != b
    assert a.exists()
    assert b.exists()


def test_take_snapshot_skips_when_data_dir_missing(tmp_path: Path):
    """Edge case: if data dir doesn't exist (cold start), snapshot
    must not crash — return None instead."""
    data_dir = tmp_path / "never-created"
    snapshots_dir = tmp_path / "snapshots"

    result = take_snapshot(data_dir, snapshots_dir)
    assert result is None


def test_restore_is_atomic_on_extract_failure(tmp_path: Path, monkeypatch):
    """If extractall raises midway, the original data_dir must NOT be
    deleted — the user can retry without losing pre-restore state."""
    import tarfile as _tarfile

    data_dir = tmp_path / "data"
    snapshots_dir = tmp_path / "snapshots"
    _write(data_dir / "before.txt", "ORIGINAL")
    archive = take_snapshot(data_dir, snapshots_dir)
    assert archive is not None

    # Mutate the original after snapshot, then attempt a restore that
    # we'll rig to fail mid-extract.
    _write(data_dir / "before.txt", "MUTATED")
    real_extractall = _tarfile.TarFile.extractall

    def boom_extractall(self, *args, **kwargs):
        # Let the staging dir get created, then fail.
        raise RuntimeError("simulated mid-extract crash")

    monkeypatch.setattr(_tarfile.TarFile, "extractall", boom_extractall)

    try:
        restore_snapshot(archive, data_dir)
    except RuntimeError:
        pass
    finally:
        monkeypatch.setattr(_tarfile.TarFile, "extractall", real_extractall)

    # Original data_dir is untouched (still has the post-snapshot mutation).
    assert (data_dir / "before.txt").read_text() == "MUTATED"
    # Staging dir cleaned up.
    leftover = list(tmp_path.glob("data.restore.*"))
    assert leftover == []


def test_snapshot_with_open_writer_roundtrips_committed_events(tmp_path: Path):
    """The real failure mode: snapshot a SQLite DB while another
    connection has it open and has just committed events that are
    still in the WAL (not yet checkpointed into the main file).

    Without a pre-snapshot wal_checkpoint(TRUNCATE), the tarball
    captures main + WAL in a state that may lose the most-recent
    commits on restore. With the checkpoint helper, a restored DB
    must have every committed event.
    """
    from microverse.memory.episodic import EpisodicMemory

    data_dir = tmp_path / "data"
    snapshots_dir = tmp_path / "snapshots"
    data_dir.mkdir()

    writer = EpisodicMemory(data_dir / "episodic.sqlite")
    for i in range(50):
        writer.append(actor="aki", action="speak", target=None, payload={"i": i})
    # Intentionally do NOT close the writer — pending WAL frames are
    # the whole point of this test.

    archive = take_snapshot(data_dir, snapshots_dir)
    assert archive is not None
    writer.close()

    # Restore into a fresh location and verify every committed event survived.
    restore_dir = tmp_path / "restored"
    restore_snapshot(archive, restore_dir)
    reader = EpisodicMemory(restore_dir / "episodic.sqlite")
    try:
        assert reader.count() == 50
    finally:
        reader.close()


def test_take_snapshot_aborts_when_checkpoint_busy(tmp_path: Path):
    """If another connection holds an open transaction that prevents
    wal_checkpoint(TRUNCATE) from completing, take_snapshot must abort
    rather than archive a possibly-torn DB."""
    import sqlite3

    from microverse.memory.episodic import EpisodicMemory
    from microverse.world.snapshot import SnapshotBusyError

    data_dir = tmp_path / "data"
    snapshots_dir = tmp_path / "snapshots"
    data_dir.mkdir()

    db_path = data_dir / "episodic.sqlite"
    em = EpisodicMemory(db_path)
    em.append(actor="aki", action="speak", target=None, payload={"i": 0})

    # Hold a write transaction open from a separate connection so
    # wal_checkpoint(TRUNCATE) cannot reclaim the WAL.
    blocker = sqlite3.connect(str(db_path), timeout=0.1)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        import pytest

        with pytest.raises(SnapshotBusyError):
            take_snapshot(data_dir, snapshots_dir)
    finally:
        blocker.rollback()
        blocker.close()
        em.close()


def test_snapshots_dir_inside_data_is_excluded(tmp_path: Path):
    """When snapshots/ is a subdirectory of data/, an existing snapshot
    must NOT be included in the next snapshot. Otherwise archives nest
    recursively and storage explodes."""
    import tarfile

    data_dir = tmp_path / "data"
    snapshots_dir = data_dir / "snapshots"  # nested
    _write(data_dir / "episodic.sqlite", "first")

    a = take_snapshot(data_dir, snapshots_dir)
    assert a is not None
    # Now there's already an archive sitting inside data/snapshots/.
    b = take_snapshot(data_dir, snapshots_dir)
    assert b is not None

    # The second archive must not contain the first.
    with tarfile.open(b, "r:gz") as tar:
        names = tar.getnames()
    assert all("snapshots" not in n for n in names), names
