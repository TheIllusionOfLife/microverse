"""Manifest rotation — when manifest.jsonl exceeds MANIFEST_ROTATE_BYTES,
roll over to manifest-<UTC>.jsonl. Readers (dashboard, gates producer)
glob ``manifest*.jsonl``.
"""

from __future__ import annotations

from pathlib import Path

from microverse.agents.harvester import Harvester


def test_active_manifest_path_returns_default_when_no_rotation(tmp_path: Path) -> None:
    h = Harvester(harvest_root=tmp_path)
    p = h._active_manifest_path()
    assert p.name == "manifest.jsonl"


def test_active_manifest_path_rotates_when_over_threshold(tmp_path: Path, monkeypatch) -> None:
    """When manifest.jsonl exceeds MANIFEST_ROTATE_BYTES, the next
    _active_manifest_path call rolls it aside to manifest-<UTC>.jsonl
    and returns a fresh manifest.jsonl path. Live writes always go to
    manifest.jsonl; manifest-* files are the audit archive."""
    import microverse.agents.harvester as harvester_mod

    monkeypatch.setattr(harvester_mod, "MANIFEST_ROTATE_BYTES", 200)
    h = Harvester(harvest_root=tmp_path)

    # Pre-fill manifest.jsonl past the threshold.
    live = tmp_path / "manifest.jsonl"
    live.write_text("x" * 500)
    assert live.stat().st_size > 200

    returned = h._active_manifest_path()
    # Returned path is still the live manifest.jsonl (the rotation
    # rewrote the underlying file to manifest-<UTC>.jsonl).
    assert returned.name == "manifest.jsonl"
    # The rotated archive file exists alongside.
    archives = sorted(tmp_path.glob("manifest-*.jsonl"))
    assert len(archives) == 1
    assert archives[0].read_text() == "x" * 500
    # And the live manifest is now absent (or empty) — next write
    # creates it fresh.
    assert not live.exists() or live.stat().st_size == 0


def test_glob_finds_all_manifest_variants(tmp_path: Path, monkeypatch) -> None:
    import microverse.agents.harvester as harvester_mod

    monkeypatch.setattr(harvester_mod, "MANIFEST_ROTATE_BYTES", 100)

    # Simulate prior rotated files + the live one.
    (tmp_path / "manifest.jsonl").write_text('{"a":1}\n')
    (tmp_path / "manifest-20260520T120000Z.jsonl").write_text('{"b":2}\n')

    matches = sorted(tmp_path.glob("manifest*.jsonl"))
    assert len(matches) == 2
