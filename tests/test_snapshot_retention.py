"""Snapshot retention — prune_snapshots() bounds disk usage during long soaks.

A 7-day soak at SNAPSHOT_EVERY=1000 ticks emits ~50 archives totalling
multiple gigabytes. Without retention, snapshots fill disk. prune_snapshots
drops oldest archives until both count and byte bounds hold; the single
newest archive is always preserved; .tmp files (mid-write) are skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

from microverse.world.snapshot import prune_snapshots


def _make_fake_snapshot(snapshots_dir: Path, name: str, size_bytes: int = 1024) -> Path:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    p = snapshots_dir / name
    p.write_bytes(b"x" * size_bytes)
    return p


def _fake_chain(snapshots_dir: Path, count: int, size_each: int = 1024) -> list[Path]:
    """Names follow the real archive convention so lexical sort == time order."""
    out: list[Path] = []
    base = "20260520T000000Z-{seq:06d}.tar.gz"
    for i in range(1, count + 1):
        p = _make_fake_snapshot(snapshots_dir, base.format(seq=i), size_each)
        # Stagger mtimes so any mtime-based logic also sees a stable order.
        ts = 1_700_000_000 + i
        os.utime(p, (ts, ts))
        out.append(p)
    return out


def test_prune_keeps_newest_n_by_count(tmp_path: Path) -> None:
    snapshots_dir = tmp_path / "snapshots"
    archives = _fake_chain(snapshots_dir, count=30)

    prune_snapshots(snapshots_dir, max_count=24, max_bytes=None)

    remaining = sorted(snapshots_dir.glob("*.tar.gz"))
    assert len(remaining) == 24
    # The newest 24 (highest sequence numbers) survived.
    assert remaining == archives[-24:]


def test_prune_byte_ceiling(tmp_path: Path) -> None:
    snapshots_dir = tmp_path / "snapshots"
    _fake_chain(snapshots_dir, count=10, size_each=1_000_000)  # 10 MB total

    # Cap at 3 MB → only the 3 newest 1 MB archives can fit.
    prune_snapshots(snapshots_dir, max_count=None, max_bytes=3_000_000)

    remaining = sorted(snapshots_dir.glob("*.tar.gz"))
    assert len(remaining) <= 3
    assert sum(p.stat().st_size for p in remaining) <= 3_000_000


def test_prune_never_deletes_newest(tmp_path: Path) -> None:
    """Even when over the byte cap, the single newest archive must
    survive — otherwise prune can orphan a snapshot mid-restore-prep."""
    snapshots_dir = tmp_path / "snapshots"
    _fake_chain(snapshots_dir, count=2, size_each=10_000_000)

    # Cap at 1 byte — would delete everything if prune was naive.
    prune_snapshots(snapshots_dir, max_count=None, max_bytes=1)

    remaining = sorted(snapshots_dir.glob("*.tar.gz"))
    assert len(remaining) == 1, "single newest archive must always survive"


def test_prune_skips_tmp_files(tmp_path: Path) -> None:
    """A .tar.gz.tmp file is a snapshot in mid-write. Prune must not
    touch it; otherwise a concurrent write loses its target."""
    snapshots_dir = tmp_path / "snapshots"
    _fake_chain(snapshots_dir, count=5)
    tmp_file = _make_fake_snapshot(snapshots_dir, "20260520T999999Z-999999.tar.gz.tmp")

    prune_snapshots(snapshots_dir, max_count=1, max_bytes=None)

    # The .tmp file survives (not counted, not deleted).
    assert tmp_file.exists()
    remaining = sorted(snapshots_dir.glob("*.tar.gz"))
    assert len(remaining) == 1


def test_prune_no_op_on_missing_dir(tmp_path: Path) -> None:
    """A cold start (no snapshots dir yet) is not an error."""
    missing = tmp_path / "never-created"
    prune_snapshots(missing, max_count=24, max_bytes=None)  # must not raise


def test_prune_no_op_when_under_bounds(tmp_path: Path) -> None:
    snapshots_dir = tmp_path / "snapshots"
    archives = _fake_chain(snapshots_dir, count=5, size_each=100)

    prune_snapshots(snapshots_dir, max_count=10, max_bytes=10_000)

    remaining = sorted(snapshots_dir.glob("*.tar.gz"))
    assert remaining == archives


def test_prune_both_bounds_applied(tmp_path: Path) -> None:
    """When both bounds are set, the stricter one wins (intersection,
    not union — we keep only archives that satisfy BOTH)."""
    snapshots_dir = tmp_path / "snapshots"
    _fake_chain(snapshots_dir, count=10, size_each=1_000_000)

    # count says keep 8; bytes says keep at most 3 (3 MB cap).
    prune_snapshots(snapshots_dir, max_count=8, max_bytes=3_000_000)

    remaining = sorted(snapshots_dir.glob("*.tar.gz"))
    assert len(remaining) <= 3
