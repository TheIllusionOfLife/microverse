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

from microverse.config import HARVEST_CRAFT_CAP_PER_FLUSH

if TYPE_CHECKING:
    from microverse.agents.trader import Score
    from microverse.world.workshop import WorkshopProjection

MIN_ARTIFACT_CHARS = 20
SLUG_MAX_LEN = 60
DEFAULT_PERCENTILE = 70

# Per-primary-verb caps applied AFTER Trader scoring. Bounds the rate
# at which any single verb can dominate the harvest manifest —
# defence-in-depth against Trader v2 itself becoming a new attractor.
_PER_VERB_FLUSH_CAP: dict[str, int] = {
    "craft": HARVEST_CRAFT_CAP_PER_FLUSH,
}


class _RankerProtocol(Protocol):
    def rank(
        self,
        candidates: list[ArtifactCandidate | WIPCandidate],
    ) -> list[Score]: ...


@dataclass(frozen=True, slots=True)
class ArtifactCandidate:
    actor: str
    action: str
    artifact: str | None
    ts: float


@dataclass(frozen=True, slots=True)
class WIPCandidate:
    """A completed workshop WIP ready for harvest.

    Built by ``Harvester.flush()`` from the ``WorkshopProjection`` at
    completion time. The fragments are a snapshot — the WIP is in
    phase=complete so the projection will not add more.

    ``contributors`` is the distinct contributor set in first-
    appearance order; ``fragments`` is the (contributor, text)
    sequence in commit order so the harvested file preserves
    chronology.
    """

    name: str
    contributors: tuple[str, ...]
    fragments: tuple[tuple[str, str], ...]
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
        workshop: WorkshopProjection | None = None,
    ) -> None:
        self._root = Path(harvest_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest = self._root / "manifest.jsonl"
        self._trader = trader
        self._percentile = percentile
        self._buffer: list[ArtifactCandidate] = []
        # v0.2 (ADR 0003): workshop projection reference. flush()
        # scans for newly-completed WIPs and adds them to the
        # candidates list. _harvested_wips dedupes — a WIP that
        # was harvested in flush N is not re-harvested at flush N+1.
        self._workshop = workshop
        self._harvested_wips: set[str] = set()

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

        No-op when no trader is configured. Builds a heterogeneous
        candidates list of ArtifactCandidate (from the per-tick
        buffer) AND WIPCandidate (from any newly-completed WIPs on
        the workshop projection, if one is attached). Both flow
        through ``Trader.rank()``; the per-verb cap then bounds the
        accepted set so no single primary verb can dominate the
        manifest (ADR 0003 — Trader v2 must not become a new
        attractor).

        If ``trader.rank()`` raises, the buffer is preserved (a
        transient ranker failure must not silently discard pending
        artifacts) and the exception is re-raised so the caller can
        decide whether to retry.
        """
        wip_candidates = self._drain_completed_wips()
        if self._trader is None or (not self._buffer and not wip_candidates):
            self._buffer.clear()
            return []

        artifact_candidates: list[ArtifactCandidate] = list(self._buffer)
        candidates: list[ArtifactCandidate | WIPCandidate] = [
            *artifact_candidates,
            *wip_candidates,
        ]
        try:
            scores = self._trader.rank(candidates)
        except Exception:
            # Keep both buffers intact so a retry can rescue. The
            # WIP candidates were drained — re-add them to the
            # harvested set's inverse (i.e., remove from
            # _harvested_wips) so the next flush re-discovers them.
            for w in wip_candidates:
                self._harvested_wips.discard(w.name)
            raise

        # Only clear after a successful rank — partial failures don't
        # silently drop the batch.
        self._buffer.clear()
        cutoff = self._percentile_cutoff([s.score for s in scores])

        # Decide acceptance in score-desc order so per-verb caps pick
        # the TOP items (not the first-buffered). Then write the
        # manifest in candidate-input order so audit-trail consumers
        # get a stable insertion-order log.
        order = sorted(range(len(candidates)), key=lambda i: -scores[i].score)
        accepted_set: set[int] = set()
        accepted_paths: dict[int, Path] = {}
        per_verb_count: dict[str, int] = {}
        for idx in order:
            cand = candidates[idx]
            score = scores[idx]
            verb = self._verb_of(cand)
            cap = _PER_VERB_FLUSH_CAP.get(verb)
            # cutoff is None ⇒ no signal across a multi-item all-tied
            # population. Accept nothing rather than spam the inbox.
            above_cutoff = cutoff is not None and score.score >= cutoff
            under_cap = cap is None or per_verb_count.get(verb, 0) < cap
            if not (above_cutoff and under_cap):
                continue
            path = self._write_candidate(cand)
            accepted_set.add(idx)
            accepted_paths[idx] = path
            per_verb_count[verb] = per_verb_count.get(verb, 0) + 1

        written: list[Path] = []
        for idx, cand in enumerate(candidates):
            score = scores[idx]
            if idx in accepted_set:
                path = accepted_paths[idx]
                written.append(path)
                self._append_manifest(cand, accepted=True, path=path, score=score.score)
            else:
                self._append_manifest(cand, accepted=False, path=None, score=score.score)
        return written

    def _drain_completed_wips(self) -> list[WIPCandidate]:
        """Snapshot newly-completed WIPs from the workshop projection
        (if one is attached) and mark them harvested. Returns
        WIPCandidate objects with frozen fragment tuples — the
        projection may continue to mutate, but the snapshot is
        immutable.
        """
        if self._workshop is None:
            return []
        out: list[WIPCandidate] = []
        for w in self._workshop.wips():
            if w.phase != "complete":
                continue
            if w.name in self._harvested_wips:
                continue
            self._harvested_wips.add(w.name)
            out.append(
                WIPCandidate(
                    name=w.name,
                    contributors=w.contributors(),
                    fragments=tuple((f.contributor, f.text) for f in w.fragments),
                    ts=w.last_activity_ts,
                )
            )
        return out

    def _verb_of(self, cand: ArtifactCandidate | WIPCandidate) -> str:
        if isinstance(cand, WIPCandidate):
            return "wip"
        return cand.action

    def _write_candidate(self, cand: ArtifactCandidate | WIPCandidate) -> Path:
        if isinstance(cand, WIPCandidate):
            return self._write_wip(cand)
        return self._write_artifact(cand)

    def _percentile_cutoff(self, scores: list[float]) -> float | None:
        """Compute a score floor such that values >= floor land in the
        top (100 - percentile)% of the population.

        Edge cases:
          - empty input → None (nothing to compare).
          - single element → that element's score (one item is its own
            top quantile; refusing to harvest a sparse early run would
            be perverse).
          - 2+ elements all tied → None (no ranking signal across
            multiple items; accept nothing rather than spam the inbox).
          - p=0 → minimum score (accept everything).
          - p=100 → maximum score (accept only the top).
        """
        if not scores:
            return None
        if len(scores) == 1:
            return scores[0]
        ordered = sorted(scores)
        if ordered[0] == ordered[-1]:
            return None
        # Linear-rank percentile (no interpolation): index = N*p/100
        # clamped to [0, len-1]. p=70 on 10 items → index 7 → 8th lowest.
        idx = max(0, min(len(ordered) - 1, (len(ordered) * self._percentile) // 100))
        return ordered[idx]

    def _write_wip(self, cand: WIPCandidate) -> Path:
        """Write a completed WIP to ``harvest/inbox/<date>/<name>.md``.

        Each fragment is rendered as one line prefixed by its
        contributor; the frontmatter captures the WIP name, the
        contributor set, the action discriminator ``"wip"``, and the
        completion timestamp. Atomic-replace + O_EXCL collision guard
        matches ``_write_artifact``.
        """
        date = datetime.fromtimestamp(cand.ts, tz=UTC).strftime("%Y-%m-%d")
        day_dir = self._root / "inbox" / date
        # Strip the "workshop." prefix from the slug for readability;
        # the YAML frontmatter still carries the full name.
        slug = _slugify(cand.name.removeprefix("workshop."))
        target = _reserve_unique_path(day_dir, slug, ".md")
        frontmatter = yaml.safe_dump(
            {
                "wip": cand.name,
                "contributors": list(cand.contributors),
                "action": "wip",
                "ts": cand.ts,
            },
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
        body_lines = [f"- {actor}: {text}" for actor, text in cand.fragments]
        body = f"---\n{frontmatter}---\n\n" + "\n".join(body_lines) + "\n"
        _atomic_write_text(target, body)
        return target

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
        candidate: ArtifactCandidate | WIPCandidate,
        *,
        accepted: bool,
        path: Path | None,
        score: float | None = None,
    ) -> None:
        if isinstance(candidate, WIPCandidate):
            record = {
                "ts": candidate.ts,
                "actor": "workshop",
                "action": "wip",
                "wip": candidate.name,
                "contributors": list(candidate.contributors),
                "accepted": accepted,
                "path": str(path.relative_to(self._root)) if path else None,
                "score": score,
            }
        else:
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
