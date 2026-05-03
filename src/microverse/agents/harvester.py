"""Harvester: the only out-of-world entity.

Inhabitants do not perceive the Harvester. It observes today's artifact
buffer and writes accepted artifacts to ``harvest/inbox/<UTC date>/``
for the host user.

Phase 1 acceptance heuristic is intentionally crude — accept when the
artifact text is at least ``MIN_ARTIFACT_CHARS`` long. Phase 2 swaps
this for Trader-ranked percentile selection.

Atomic write contract:
  - artifact files: write to ``*.tmp``, fsync, then ``os.replace`` to
    the final name. No partial files visible to readers.
  - ``manifest.jsonl``: open in append mode, write one JSON line, fsync.
    Each line is < PIPE_BUF so the OS guarantees atomicity per write.

Filename slug: lowercase alnum/hyphen/underscore only, derived from the
first ~60 chars of the artifact text, with collision suffix ``-N``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MIN_ARTIFACT_CHARS = 20
SLUG_MAX_LEN = 60


@dataclass(frozen=True, slots=True)
class ArtifactCandidate:
    actor: str
    action: str
    artifact: str | None
    ts: float


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("-", text.lower())
    s = s.strip("-_")
    if not s:
        s = "artifact"
    return s[:SLUG_MAX_LEN].rstrip("-_") or "artifact"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _resolve_collision(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    parent = target.parent
    n = 2
    while True:
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


class Harvester:
    """Out-of-world artifact collector.

    Phase 1 contract: ``consider()`` returns the written Path on accept,
    None on reject; in either case appends one line to manifest.jsonl.
    """

    def __init__(self, harvest_root: str | Path) -> None:
        self._root = Path(harvest_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest = self._root / "manifest.jsonl"

    def consider(self, candidate: ArtifactCandidate) -> Path | None:
        accepted = False
        path: Path | None = None

        if candidate.artifact and len(candidate.artifact.strip()) >= MIN_ARTIFACT_CHARS:
            accepted = True
            path = self._write_artifact(candidate)

        self._append_manifest(candidate, accepted=accepted, path=path)
        return path

    def _write_artifact(self, candidate: ArtifactCandidate) -> Path:
        date = datetime.fromtimestamp(candidate.ts, tz=UTC).strftime("%Y-%m-%d")
        day_dir = self._root / "inbox" / date
        slug = _slugify((candidate.artifact or "")[: SLUG_MAX_LEN * 2])
        target = _resolve_collision(day_dir / f"{slug}.md")
        body = (
            f"---\nactor: {candidate.actor}\naction: {candidate.action}\n"
            f"ts: {candidate.ts}\n---\n\n{candidate.artifact}\n"
        )
        _atomic_write_text(target, body)
        return target

    def _append_manifest(
        self,
        candidate: ArtifactCandidate,
        *,
        accepted: bool,
        path: Path | None,
    ) -> None:
        record = {
            "ts": candidate.ts,
            "actor": candidate.actor,
            "action": candidate.action,
            "accepted": accepted,
            "path": str(path.relative_to(self._root)) if path else None,
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with open(self._manifest, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
