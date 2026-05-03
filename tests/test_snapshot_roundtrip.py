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
