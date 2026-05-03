"""Tests for microverse.agents.harvester.Harvester.

Phase 1 contract:
  - Out-of-world: never appears in episodic events.
  - Atomic writes: artifact files via tmp + os.replace; manifest.jsonl
    via append + fsync.
  - Each accepted artifact lands at:
        harvest/inbox/<UTC date>/<slug>.md
    with a UTF-8 frontmatter line listing actor, action, ts.
  - Each consider() call appends one line to manifest.jsonl regardless
    of accept/reject with {accepted, path|null, actor, ...}.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from microverse.agents.harvester import ArtifactCandidate, Harvester


def _candidate(text: str = "a beautiful wooden bowl with carved swirls") -> ArtifactCandidate:
    return ArtifactCandidate(
        actor="aki",
        action="craft",
        artifact=text,
        ts=datetime(2026, 5, 3, 12, 0, tzinfo=UTC).timestamp(),
    )


def test_accepts_long_artifact_writes_file(tmp_path: Path):
    h = Harvester(tmp_path)
    cand = _candidate()
    path = h.consider(cand)
    assert path is not None
    assert path.exists()
    assert path.parent.name == "2026-05-03"
    body = path.read_text()
    assert "aki" in body
    assert "wooden bowl" in body


def test_rejects_too_short_artifact(tmp_path: Path):
    h = Harvester(tmp_path)
    cand = _candidate(text="ok")
    path = h.consider(cand)
    assert path is None


def test_rejects_none_artifact(tmp_path: Path):
    h = Harvester(tmp_path)
    cand = ArtifactCandidate(actor="aki", action="rest", artifact=None, ts=0.0)
    assert h.consider(cand) is None


def test_writes_manifest_line_on_accept(tmp_path: Path):
    h = Harvester(tmp_path)
    h.consider(_candidate())
    manifest = tmp_path / "manifest.jsonl"
    assert manifest.exists()
    lines = manifest.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["accepted"] is True
    assert record["actor"] == "aki"
    assert record["path"].endswith(".md")


def test_writes_manifest_line_on_reject(tmp_path: Path):
    h = Harvester(tmp_path)
    h.consider(ArtifactCandidate(actor="aki", action="rest", artifact=None, ts=0.0))
    manifest = tmp_path / "manifest.jsonl"
    record = json.loads(manifest.read_text().splitlines()[0])
    assert record["accepted"] is False
    assert record["path"] is None


def test_atomic_no_temp_files_left_behind(tmp_path: Path):
    h = Harvester(tmp_path)
    h.consider(_candidate())
    leftover_tmps = list(tmp_path.rglob("*.tmp"))
    assert leftover_tmps == []


def test_filename_slug_is_safe(tmp_path: Path):
    h = Harvester(tmp_path)
    cand = ArtifactCandidate(
        actor="aki",
        action="craft",
        artifact="A beautiful Wooden / Bowl ../escape with %weird% chars",
        ts=0.0,
    )
    path = h.consider(cand)
    assert path is not None
    name = path.name
    # No path traversal characters in the slug.
    for ch in ("/", "\\", "..", " "):
        assert ch not in name
    # Stem is lowercase alnum + hyphens + underscores.
    stem = path.stem
    assert stem == stem.lower()
    assert all(c.isalnum() or c in "-_" for c in stem)


def test_multiple_artifacts_unique_filenames(tmp_path: Path):
    h = Harvester(tmp_path)
    p1 = h.consider(_candidate(text="alpha alpha alpha alpha alpha"))
    p2 = h.consider(_candidate(text="alpha alpha alpha alpha alpha"))
    assert p1 is not None
    assert p2 is not None
    assert p1 != p2  # collision-resolved


def test_emoji_only_artifact_uses_hash_slug(tmp_path: Path):
    """When the slug collapses to empty (emoji or pure non-ASCII text),
    the filename falls back to a stable content hash so distinct
    artifacts don't pile up under collision suffixes."""
    h = Harvester(tmp_path)
    cand = ArtifactCandidate(
        actor="aki",
        action="craft",
        artifact="🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸🌸",
        ts=0.0,
    )
    path = h.consider(cand)
    assert path is not None
    # Pure-emoji input must take the hash-prefixed fallback path.
    assert path.stem.startswith("artifact-")
    # Hash prefix is 8 hex chars after the dash — confirm we landed in
    # the fallback path, not a coincidence.
    assert len(path.stem) == len("artifact-") + 8


def test_yaml_frontmatter_safe_against_special_chars(tmp_path: Path):
    """Even though Phase 1 hardcodes actor/action to safe values, the
    frontmatter writer must yaml-escape so a future field with quotes
    or colons doesn't break downstream readers."""
    h = Harvester(tmp_path)
    cand = ArtifactCandidate(
        actor='evil "actor": with: colons',
        action="craft",
        artifact="a long enough artifact body to be accepted by the harvester",
        ts=0.0,
    )
    path = h.consider(cand)
    assert path is not None
    body = path.read_text()
    # Must round-trip through yaml.safe_load without raising.
    import yaml

    head = body.split("---", 2)[1]
    parsed = yaml.safe_load(head)
    assert parsed["actor"] == 'evil "actor": with: colons'
    assert parsed["action"] == "craft"


def test_no_episodic_appended_by_harvester(tmp_path: Path):
    """Harvester must not write to the episodic memory — it lives
    outside the simulated world. We assert this by verifying no
    EpisodicMemory was given to it (the constructor doesn't accept one)
    and by spot-checking that no .sqlite file is created in the harvest
    root."""
    h = Harvester(tmp_path)
    h.consider(_candidate())
    sqlites = list(tmp_path.rglob("*.sqlite"))
    assert sqlites == []
