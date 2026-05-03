"""Harvester: the only out-of-world entity.

Inhabitants do not perceive the Harvester. It observes today's artifact
buffer and writes accepted artifacts to ``harvest/inbox/<UTC date>/``
for the host user.

Two acceptance modes (selectable at construction):
  - **Phase 1 heuristic** (no trader): each ``consider()`` writes
    immediately if the artifact text is at least ``MIN_ARTIFACT_CHARS``
    long. Maintained for backwards compat and zero-LLM smoke tests.
  - **Phase 2 percentile** (trader given): each ``consider()`` buffers
    the candidate and returns ``None``. ``flush()`` rates the entire
    buffer through the Trader, applies a percentile threshold (default
    p70), writes accepted artifacts, and records every candidate's
    score in ``manifest.jsonl`` so rejection decisions are auditable.

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
from typing import TYPE_CHECKING, Protocol

import yaml

if TYPE_CHECKING:
    from microverse.agents.trader import Score

MIN_ARTIFACT_CHARS = 20
SLUG_MAX_LEN = 60
DEFAULT_PERCENTILE = 70


class _RankerProtocol(Protocol):
    def rank(self, candidates: list[ArtifactCandidate]) -> list[Score]: ...


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

    def __init__(
        self,
        harvest_root: str | Path,
        *,
        trader: _RankerProtocol | None = None,
        percentile: int = DEFAULT_PERCENTILE,
    ) -> None:
        self._root = Path(harvest_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest = self._root / "manifest.jsonl"
        self._trader = trader
        self._percentile = percentile
        self._buffer: list[ArtifactCandidate] = []

    def consider(self, candidate: ArtifactCandidate) -> Path | None:
        # Phase 2 path: trader present → buffer the candidate, write at flush().
        # Only pre-reject genuinely empty artifacts; let the Trader judge
        # short ones (it has the right context).
        if self._trader is not None:
            if candidate.artifact and candidate.artifact.strip():
                self._buffer.append(candidate)
            else:
                self._append_manifest(candidate, accepted=False, path=None, score=None)
            return None

        # Phase 1 path: heuristic only — accept on length, no LLM call.
        accepted = False
        path: Path | None = None
        if candidate.artifact and len(candidate.artifact.strip()) >= MIN_ARTIFACT_CHARS:
            accepted = True
            path = self._write_artifact(candidate)
        self._append_manifest(candidate, accepted=accepted, path=path, score=None)
        return path

    def flush(self) -> list[Path]:
        """Apply Trader scoring + percentile threshold; write accepted.

        No-op when no trader is configured. The buffer is always cleared,
        even if the trader returned malformed output (defense in depth —
        a stuck buffer would otherwise grow unbounded).
        """
        if self._trader is None or not self._buffer:
            self._buffer.clear()
            return []

        candidates = list(self._buffer)
        self._buffer.clear()
        scores = self._trader.rank(candidates)
        cutoff = self._percentile_cutoff([s.score for s in scores])

        written: list[Path] = []
        for cand, score in zip(candidates, scores, strict=True):
            # cutoff is None ⇒ no signal (e.g. all scores tied / all zero).
            # Accept nothing — better to lose a batch than to spam the
            # inbox with unranked output.
            if cutoff is not None and score.score >= cutoff:
                path = self._write_artifact(cand)
                written.append(path)
                self._append_manifest(cand, accepted=True, path=path, score=score.score)
            else:
                self._append_manifest(cand, accepted=False, path=None, score=score.score)
        return written

    def _percentile_cutoff(self, scores: list[float]) -> float | None:
        """Compute a score floor such that values >= floor land in the
        top (100 - percentile)% of the population.

        Returns None when the input is empty OR all scores are tied
        (in which case there is no ranking signal and the caller should
        accept nothing).
        """
        if not scores:
            return None
        ordered = sorted(scores)
        if ordered[0] == ordered[-1]:
            return None
        # Linear-rank percentile (no interpolation): index = N*p/100
        # clamped to [0, len-1]. p=70 on 10 items → index 7 → 8th lowest.
        idx = max(0, min(len(ordered) - 1, (len(ordered) * self._percentile) // 100))
        return ordered[idx]

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
        score: float | None = None,
    ) -> None:
        record = {
            "ts": candidate.ts,
            "actor": candidate.actor,
            "action": candidate.action,
            "accepted": accepted,
            "path": str(path.relative_to(self._root)) if path else None,
            "score": score,
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        # Records are well below PIPE_BUF (4 KB on macOS/Linux), so a
        # single os.write under O_APPEND is atomic across concurrent
        # appenders. fsync ensures the data is on disk before we return.
        with open(self._manifest, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
