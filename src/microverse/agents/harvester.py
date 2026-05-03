"""Harvester: the only out-of-world entity.

Inhabitants do not perceive the Harvester. It observes today's artifact
buffer and writes accepted artifacts to ``harvest/inbox/<UTC date>/``
for the host user.

Phase 1 acceptance heuristic is intentionally crude — accept when the
artifact text is at least ``MIN_ARTIFACT_CHARS`` long. Phase 2 swaps
this for Trader-ranked percentile selection.

Atomic write contract:
  - artifact files: write to ``*.tmp``, fsync, then ``os.replace`` to
    the final name. The final name is reserved via ``O_CREAT|O_EXCL``
    *before* the temp write so two concurrent harvesters cannot race
    to the same path. (Single-process today; Phase 4 watchdog may run
    concurrently.)
  - ``manifest.jsonl``: open in append mode, write one JSON line, fsync.
    POSIX guarantees atomicity for ``write()`` calls smaller than
    ``PIPE_BUF`` (4096 on Linux/macOS); our records are ~200 bytes, so
    a single ``f.write(line)`` is one atomic ``write(2)``.

Filename slug: lowercase alnum/hyphen/underscore only, derived from the
first ~60 chars of the artifact text. If the slug collapses to empty
(e.g., emoji-only artifact), it is replaced by an 8-char content hash
so distinct artifacts don't pile up under collision suffixes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

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
    s = s.strip("-_")[:SLUG_MAX_LEN].rstrip("-_")
    if s:
        return s
    # Fallback: hash-based stable slug for non-ASCII / emoji-only inputs.
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"artifact-{digest}"


def _atomic_write_text(target: Path, content: str) -> None:
    """Atomically reserve ``target`` (O_EXCL) then fill it via tmp + replace.

    ``target`` is expected to already be the result of a collision check
    upstream; this function only handles the actual byte transfer.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


def _reserve_unique_path(parent: Path, stem: str, suffix: str) -> Path:
    """Atomically create an empty file at ``parent/<stem><n><suffix>``.

    Uses ``O_CREAT|O_EXCL``; on EEXIST, increments ``n`` and retries.
    Returns the reserved path. The caller fills it with the real content
    via ``_atomic_write_text`` (which writes to a tmp and replaces).
    """
    parent.mkdir(parents=True, exist_ok=True)
    n = 0
    while True:
        candidate = parent / (f"{stem}{suffix}" if n == 0 else f"{stem}-{n + 1}{suffix}")
        try:
            fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return candidate


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
        target = _reserve_unique_path(day_dir, slug, ".md")
        # YAML-safe frontmatter: defend against actor/action containing
        # colons, quotes, or newlines, even though Phase 1 hard-codes
        # them to safe values.
        frontmatter = yaml.safe_dump(
            {"actor": candidate.actor, "action": candidate.action, "ts": candidate.ts},
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
        body = f"---\n{frontmatter}---\n\n{candidate.artifact}\n"
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
        # Records are well below PIPE_BUF (4 KB on macOS/Linux), so a
        # single os.write under O_APPEND is atomic across concurrent
        # appenders. fsync ensures the data is on disk before we return.
        with open(self._manifest, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
